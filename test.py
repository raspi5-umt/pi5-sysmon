#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Waveshare 1.69" (240x280) Touch LCD • Raspberry Pi 5
# Sistem Paneli (CPU • RAM • Depolama • Ağ • Sıcaklık)
# Dokunmatik: Waveshare Touch_1inch69 akışı (senin çalışan örnekteki gibi)
# Debounce: Swipe jestleri tek sayfa atlatsın diye cooldown eklendi.

import os, sys, time, math, threading, subprocess, logging
from collections import deque
from PIL import Image, ImageDraw, ImageFont

# Waveshare sürücüleri
sys.path.append("..")
from lib import LCD_1inch69, Touch_1inch69

# ---------- RPi pinleri ----------
RST = 27
DC  = 25
BL  = 18
TP_INT = 4
TP_RST = 17

# ---------- Dokunmatik durumu ----------
Mode = 2           # 0: gesture test, 1: point move, 2: gesture + point
Flag = 0           # callback tetikleyici (Waveshare stili)
touch = None       # Touch_1inch69 instance

# ---------- Debounce / Cooldown ----------
SWIPE_COOLDOWN_MS = 600   # bir swipe sonrası ikinci sayfa geçişine izin süresi
TAP_COOLDOWN_MS   = 250   # tema vs. için küçük soğuma

last_gesture_time_ms = 0
last_gesture_code    = 0
last_tap_time_ms     = 0

# ---------- Log ----------
logging.basicConfig(level=logging.INFO)

# ---------- Tema ----------
DARK = dict(
    BG=(5,8,12), FG=(235,235,235), ACC1=(120,180,255), ACC2=(255,120,180),
    OK=(90,200,120), WARN=(255,170,0), BAD=(255,80,80),
    GRID=(25,30,36), BARBG=(22,26,32)
)
LIGHT = dict(
    BG=(240,242,246), FG=(20,22,28), ACC1=(30,90,220), ACC2=(200,60,120),
    OK=(30,170,90), WARN=(230,140,0), BAD=(210,40,40),
    GRID=(210,214,220), BARBG=(210,214,220)
)

def load_font(sz):
    for p in ("../Font/Font01.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"):
        if os.path.exists(p):
            try: return ImageFont.truetype(p, sz)
            except Exception: pass
    from PIL import ImageFont as IF
    return IF.load_default()

F12, F14, F16, F18, F22, F26 = (load_font(s) for s in (12,14,16,18,22,26))

def clamp(v, lo, hi):
    try:
        v = float(v)
        if not math.isfinite(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def pick_color(p, C):
    p = clamp(p, 0, 100)
    return C["OK"] if p < 70 else (C["WARN"] if p < 85 else C["BAD"])

def bar(d, x,y,w,h,pct,C):
    pct = clamp(pct,0,100)
    d.rounded_rectangle([x,y,x+w,y+h], radius=6, fill=C["BARBG"])
    d.rounded_rectangle([x,y,x+int(w*pct/100.0),y+h], radius=6, fill=pick_color(pct,C))

def ring(d, cx, cy, r, pct, C, width=10):
    pct = clamp(pct,0,100)/100.0
    box=[cx-r, cy-r, cx+r, cy+r]
    d.arc(box, start=135, end=405, width=width, fill=C["BARBG"])
    d.arc(box, start=135, end=135+int(270*pct), width=width, fill=pick_color(100*pct,C))

def grid(d, W, H, C):
    for gy in range(0, H, 28):
        d.line((0, gy, W, gy), fill=C["GRID"])

# ---------- Metrikler ----------
try:
    import psutil
except Exception:
    psutil = None

class Metrics:
    def __init__(self, hist_len=90):
        self.cpu=self.ram=self.disk=self.temp=0.0
        self.up=self.dn=0.0
        self.hcpu=deque(maxlen=hist_len)
        self.hram=deque(maxlen=hist_len)
        self.htmp=deque(maxlen=hist_len)
        self.hup=deque(maxlen=hist_len)
        self.hdn=deque(maxlen=hist_len)
        self.last_net = psutil.net_io_counters() if psutil else None

    def _temp(self):
        try:
            out = subprocess.check_output(["vcgencmd","measure_temp"]).decode()
            return float(out.split("=")[1].split("'")[0])
        except Exception:
            try:
                return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000.0
            except Exception:
                return 0.0

    def update(self):
        if psutil:
            try:
                self.cpu = clamp(psutil.cpu_percent(interval=None), 0, 100)
                self.ram = clamp(psutil.virtual_memory().percent, 0, 100)
                self.disk = clamp(psutil.disk_usage("/").percent, 0, 100)
            except Exception:
                pass
        else:
            try:
                la1 = os.getloadavg()[0]
                self.cpu = clamp(la1*25.0, 0, 100)
                self.ram = 0.0
                st = os.statvfs("/")
                used = (st.f_blocks - st.f_bfree) * st.f_frsize
                total = st.f_blocks * st.f_frsize
                self.disk = clamp(100.0 * used / max(1,total), 0, 100)
            except Exception:
                pass

        self.temp = clamp(self._temp(), 0, 120)

        if psutil:
            try:
                now = psutil.net_io_counters()
                if self.last_net:
                    self.up = max(0.0, (now.bytes_sent - self.last_net.bytes_sent)/1024.0)
                    self.dn = max(0.0, (now.bytes_recv - self.last_net.bytes_recv)/1024.0)
                self.last_net = now
            except Exception:
                pass

        self.hcpu.append(self.cpu); self.hram.append(self.ram); self.htmp.append(self.temp)
        self.hup.append(self.up);   self.hdn.append(self.dn)

# ---------- Sayfalar ----------
def page_summary(d, m, C, W, H):
    d.text((12,10), "SYSTEM", font=F22, fill=C["FG"])
    d.text((W-12,10), time.strftime("%H:%M"), font=F18, fill=C["ACC1"], anchor="ra")

    ring(d, 60, 92, 42, m.cpu, C); d.text((60,92), f"{m.cpu:0.0f}%", font=F14, fill=C["FG"], anchor="mm"); d.text((60,118),"CPU", font=F12, fill=C["ACC1"], anchor="mm")
    ring(d, 180,92,42, m.ram, C); d.text((180,92),f"{m.ram:0.0f}%", font=F14, fill=C["FG"], anchor="mm"); d.text((180,118),"RAM", font=F12, fill=C["ACC1"], anchor="mm")

    t_pct = clamp((m.temp-30)*(100.0/60.0),0,100)
    ring(d, 120,170,48, t_pct, C); d.text((120,170), f"{m.temp:0.1f}°C", font=F14, fill=C["FG"], anchor="mm"); d.text((120,196),"TEMP", font=F12, fill=C["ACC2"], anchor="mm")

def page_disk_net(d, m, C, W, H):
    d.text((12,10), "DISK & NET", font=F18, fill=C["FG"])

def page_storage(d, m, C, W, H):
    d.text((12,10), "STORAGE", font=F22, fill=C["FG"])
    y=50
    try:
        import psutil as ps
        for p in ps.disk_partitions():
            try:
                u = ps.disk_usage(p.mountpoint)
                d.text((12,y), f"{p.mountpoint} {u.percent:0.0f}%", font=F16, fill=C["FG"])
                bar(d, 90, y+2, W-102, 12, u.percent, C)
                y+=32
                if y > H-30: break
            except Exception:
                pass
    except Exception:
        d.text((12,y), f"/ {m.disk:0.0f}%", font=F16, fill=C["FG"])
        bar(d, 90, y+2, W-102, 12, m.disk, C)

def page_temp(d, m, C, W, H):
    d.text((12,10), "TEMPERATURE", font=F22, fill=C["FG"])
    t_pct = clamp((m.temp-30)*(100.0/60.0),0,100)
    ring(d, 120,120,60, t_pct, C, width=14)
    d.text((120,120), f"{m.temp:0.1f}°C", font=F22, fill=C["FG"], anchor="mm")
    d.text((120,200), "CPU sıcaklık", font=F14, fill=C["FG"], anchor="mm")

PAGES = [page_summary, page_disk_net, page_storage, page_temp]
PAGE_TITLES = ["SYSTEM", "DISK&NET", "STORAGE", "TEMP"]

# ---------- Gesture callback ----------
def now_ms():
    return int(time.time()*1000)

def Int_Callback(btn):
    global Flag, Mode, touch
    try:
        if Mode == 1:
            Flag = 1
            touch.get_point()
        elif Mode == 2:
            Flag = 1
            touch.Gestures = touch.Touch_Read_Byte(0x01)
            touch.get_point()
        else:
            touch.Gestures = touch.Touch_Read_Byte(0x01)
    except Exception:
        pass

# ---------- Uygulama ----------
class App:
    def __init__(self):
        # Ekran
        self.disp = LCD_1inch69.LCD_1inch69(rst=RST, dc=DC, bl=BL, tp_int=TP_INT, tp_rst=TP_RST, bl_freq=100)
        self.disp.Init()
        self.disp.clear()
        try: self.disp.bl_DutyCycle(90)
        except Exception: pass
        self.W, self.H = self.disp.width, self.disp.height

        # Tema/Metrik
        self.dark = True
        self.C = DARK
        self.m = Metrics()
        for _ in range(4):
            self.m.update(); time.sleep(0.1)

        # Touch
        global touch
        touch = Touch_1inch69.Touch_1inch69()
        touch.init()
        touch.Set_Mode(2)
        touch.GPIO_TP_INT.when_pressed = Int_Callback

        self.cur = 0
        self.running = True
        threading.Thread(target=self._metrics_loop, daemon=True).start()

    def _metrics_loop(self):
        while self.running:
            self.m.update()
            time.sleep(0.5)

    def _render_page(self):
        img = Image.new("RGB", (self.W, self.H), self.C["BG"])
        d = ImageDraw.Draw(img)
        grid(d, self.W, self.H, self.C)
        d.text((12, 10), PAGE_TITLES[self.cur], font=F22, fill=self.C["FG"])
        PAGES[self.cur](d, self.m, self.C, self.W, self.H)
        d.text((self.W-12, self.H-8), f"{self.cur+1}/{len(PAGES)}", font=F12, fill=(150,150,150), anchor="rd")
        return img

    def _maybe_toggle_theme_on_tap(self):
        # Üst-sağ küçük dokunuşla tema değiştir, ama TAP_COOLDOWN ile
        global last_tap_time_ms
        try:
            x, y = touch.X_point, touch.Y_point
            if x is None or y is None:
                return
            if x > self.W-52 and y < 40:
                t = now_ms()
                if t - last_tap_time_ms > TAP_COOLDOWN_MS:
                    last_tap_time_ms = t
                    self.dark = not self.dark
                    self.C = DARK if self.dark else LIGHT
        except Exception:
            pass

    def _handle_gesture(self):
        global last_gesture_time_ms, last_gesture_code
        g = getattr(touch, "Gestures", 0)
        if not g:
            self._maybe_toggle_theme_on_tap()
            return False

        t = now_ms()

        # Aynı jest üst üste gelirse ve cooldown dolmadıysa görmezden gel
        if g == last_gesture_code and (t - last_gesture_time_ms) < SWIPE_COOLDOWN_MS:
            touch.Gestures = 0
            return False

        # Jest kodunu işle
        changed = False
        if g == 0x03:        # LEFT  -> önceki sayfa
            if (t - last_gesture_time_ms) >= SWIPE_COOLDOWN_MS:
                self.cur = (self.cur - 1) % len(PAGES)
                changed = True
        elif g == 0x04:      # RIGHT -> sonraki sayfa
            if (t - last_gesture_time_ms) >= SWIPE_COOLDOWN_MS:
                self.cur = (self.cur + 1) % len(PAGES)
                changed = True
        elif g == 0x0B:      # DOUBLE CLICK -> tema
            if (t - last_gesture_time_ms) >= TAP_COOLDOWN_MS:
                self.dark = not self.dark
                self.C = DARK if self.dark else LIGHT
                changed = True
        elif g in (0x01, 0x02, 0x0C):
            # DOWN/UP/LONG -> sayfa değiştirme yok; sadece cooldown güncelle
            pass

        # Jest tüketildi
        touch.Gestures = 0
        if changed:
            last_gesture_time_ms = t
            last_gesture_code = g
        else:
            # Değişiklik olmasa da tekrar tekrar işlememek için güncelle
            last_gesture_code = g
        return changed

    def run(self):
        img = self._render_page()
        self.disp.ShowImage(img)
        last_draw = time.time()

        global Flag
        while True:
            if Flag == 1:
                Flag = 0
                if self._handle_gesture():
                    img = self._render_page()
                    self.disp.ShowImage(img)
                    last_draw = time.time()
            else:
                if time.time() - last_draw > 0.5:
                    img = self._render_page()
                    self.disp.ShowImage(img)
                    last_draw = time.time()
            time.sleep(0.01)

# ---------- Main ----------
if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        try:
            disp = LCD_1inch69.LCD_1inch69(rst=RST, dc=DC, bl=BL, tp_int=TP_INT, tp_rst=TP_RST, bl_freq=100)
            disp.module_exit()
        except Exception:
            pass
        logging.info("quit")
