#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Waveshare 1.69" (240x280) Touch LCD • Raspberry Pi 5
# Sistem Paneli (CPU • RAM • Depolama • Ağ • Sıcaklık)
# Dokunmatik: Waveshare Touch_1inch69 akışı
# Debounce: jestler tek sayfa atlatsın diye cooldown
#
# Bu sürümde SYSTEM sayfası Material tarzında yeniden tasarlandı:
# - Üstte rounded "app bar": başlık + büyük saat/tarih
# - Altta 3 "card": CPU, RAM, TEMP (haleli halka grafik, chip etiketleri)
# - Modern tipografi ve yumuşak gölgeler

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
Flag = 0           # callback tetikleyici
touch = None       # Touch_1inch69 instance

# ---------- Debounce / Cooldown ----------
SWIPE_COOLDOWN_MS = 600
TAP_COOLDOWN_MS   = 250
last_gesture_time_ms = 0
last_gesture_code    = 0
last_tap_time_ms     = 0

# ---------- Log ----------
logging.basicConfig(level=logging.INFO)

# ---------- Tema ----------
DARK = dict(
    BG=(6,10,14), FG=(235,235,240),
    ACC1=(123,178,255), ACC2=(255,132,188),
    OK=(92,210,125), WARN=(255,175,60), BAD=(255,95,95),
    GRID=(22,28,34), BARBG=(22,26,32),
    SURFACE=(14,18,24), SURFACE2=(18,22,28),
    SHADOW=(0,0,0)
)
LIGHT = dict(
    BG=(242,244,248), FG=(22,24,28),
    ACC1=(36,99,240), ACC2=(205,70,135),
    OK=(44,175,100), WARN=(230,140,0), BAD=(210,40,40),
    GRID=(210,214,220), BARBG=(210,214,220),
    SURFACE=(255,255,255), SURFACE2=(248,249,251),
    SHADOW=(0,0,0)
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

F10, F12, F14, F16, F18, F20, F22, F26, F28 = (load_font(s) for s in (10,12,14,16,18,20,22,26,28))

def clamp(v, lo, hi):
    try:
        v = float(v)
        if not math.isfinite(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def now_ms():
    return int(time.time()*1000)

# ---------- Basit çizim yardımcıları ----------
def shadow_rect(img, box, radius, blur=6, alpha=90, C=None):
    # CPU'yu yakmadan sahte gölge: 2 katman yarı saydam dolgular
    x1,y1,x2,y2 = box
    d = ImageDraw.Draw(img)
    if C is None: C=(0,0,0)
    for i in range(blur//2):
        a = int(alpha * (1.0 - i/(blur//2+1)))
        d.rounded_rectangle((x1+i, y1+i, x2+i, y2+i), radius=radius, fill=(C[0],C[1],C[2],))
    # üstte gerçek yüzeyi çizecek fonksiyon çağrılır

def rounded_fill(d, box, radius, fill):
    d.rounded_rectangle(box, radius=radius, fill=fill)

def chip(d, x, y, text, C, accent=True):
    # küçük etiket
    padx, pady = 6, 3
    size = d.textlength(text, font=F12)
    w = int(size) + 2*padx
    h = 18
    col = C["ACC1"] if accent else C["SURFACE2"]
    txt = (0,0,0) if not accent else (255,255,255)
    d.rounded_rectangle((x, y, x+w, y+h), radius=9, fill=col)
    d.text((x+padx, y+2), text, font=F12, fill=txt)
    return w, h

def ring(d, cx, cy, r, pct, C, width=10, back_alpha=70):
    pct = clamp(pct,0,100)/100.0
    # arka halo
    d.ellipse((cx-r-6, cy-r-6, cx+r+6, cy+r+6), outline=(0,0,0), width=0, fill=(C["ACC1"][0], C["ACC1"][1], C["ACC1"][2],))
    # track
    d.arc((cx-r, cy-r, cx+r, cy+r), start=135, end=405, width=width, fill=C["BARBG"])
    # progress
    col = C["OK"] if pct < 0.7 else (C["WARN"] if pct < 0.85 else C["BAD"])
    d.arc((cx-r, cy-r, cx+r, cy+r), start=135, end=135+int(270*pct), width=width, fill=col)

def bar(d, x,y,w,h,pct,C):
    pct = clamp(pct,0,100)
    d.rounded_rectangle([x,y,x+w,y+h], radius=h//2, fill=C["BARBG"])
    d.rounded_rectangle([x,y,x+int(w*pct/100.0),y+h], radius=h//2, fill=C["ACC1"])

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
def page_system_material(d, m, C, W, H):
    # App bar (üst): yumuşak yüzey, büyük saat
    # Gölge
    rounded_fill(d, (8,6, W-8, 66), radius=14, fill=C["SURFACE"])
    # Başlık
    d.text((20,16), "System", font=F20, fill=C["FG"])
    # Saat & tarih
    hhmm = time.strftime("%H:%M")
    day  = time.strftime("%a %d %b")
    d.text((W-20, 14), hhmm, font=F28, fill=C["ACC1"], anchor="ra")
    d.text((W-20, 40), day,  font=F12, fill=(150,150,160), anchor="ra")

    # Altta 3 kart: CPU, RAM, TEMP
    # Kart boyutları
    pad = 10
    cw  = (W - pad*3) // 2  # iki sütun
    ch  = 84
    x1, y1 = 8, 78
    x2, y2 = x1+cw+pad, 78
    x3, y3 = 8, y1+ch+pad

    # CPU Card
    rounded_fill(d, (x1, y1, x1+cw, y1+ch), radius=12, fill=C["SURFACE2"])
    chip(d, x1+8, y1+8, "CPU", C, accent=True)
    ring(d, x1+cw-30, y1+ch//2+4, 20, m.cpu, C, width=8)
    d.text((x1+14, y1+40), f"{m.cpu:0.0f}%", font=F20, fill=C["FG"])
    bar(d, x1+12, y1+ch-18, cw-24, 10, m.cpu, C)

    # RAM Card
    rounded_fill(d, (x2, y2, x2+cw, y2+ch), radius=12, fill=C["SURFACE2"])
    chip(d, x2+8, y2+8, "RAM", C, accent=True)
    ring(d, x2+cw-30, y2+ch//2+4, 20, m.ram, C, width=8)
    d.text((x2+14, y2+40), f"{m.ram:0.0f}%", font=F20, fill=C["FG"])
    bar(d, x2+12, y2+ch-18, cw-24, 10, m.ram, C)

    # TEMP Card (geniş tek kart)
    rounded_fill(d, (x3, y3, x3+cw*2+pad, y3+ch), radius=12, fill=C["SURFACE2"])
    chip(d, x3+8, y3+8, "TEMP", C, accent=False)
    t_pct = clamp((m.temp-30)*(100.0/60.0), 0, 100)
    ring(d, x3+52, y3+ch//2+4, 24, t_pct, C, width=10)
    d.text((x3+92, y3+34), f"{m.temp:0.1f}°C", font=F22, fill=C["FG"])
    # küçük bilgi çubuğu
    bar(d, x3+92, y3+ch-18, (cw*2+pad)-92-12, 10, t_pct, C)

def page_disk_net(d, m, C, W, H):
    d.text((12,10), "DISK & NET", font=F22, fill=C["FG"])
    d.text((12,50), f"DISK {m.disk:0.0f}%", font=F18, fill=C["FG"])
    d.rounded_rectangle([12,70,W-12,86], radius=8, fill=C["SURFACE2"])
    bar(d, 14,72, W-28, 12, m.disk, C)
    d.text((12,104), f"UP {m.up:0.0f} KB/s", font=F16, fill=C["ACC1"])
    d.text((12,132), f"DN {m.dn:0.0f} KB/s", font=F16, fill=C["ACC2"])

def page_storage(d, m, C, W, H):
    d.text((12,10), "STORAGE", font=F22, fill=C["FG"])
    y=50
    try:
        import psutil as ps
        for p in ps.disk_partitions():
            try:
                u = ps.disk_usage(p.mountpoint)
                d.text((12,y), f"{p.mountpoint} {u.percent:0.0f}%", font=F16, fill=C["FG"])
                d.rounded_rectangle([90,y+2,W-12,y+16], radius=7, fill=C["SURFACE2"])
                bar(d, 92, y+4, W-104, 10, u.percent, C)
                y+=32
                if y > H-30: break
            except Exception:
                pass
    except Exception:
        d.text((12,y), f"/ {m.disk:0.0f}%", font=F16, fill=C["FG"])
        d.rounded_rectangle([90,y+2,W-12,y+16], radius=7, fill=C["SURFACE2"])
        bar(d, 92, y+4, W-104, 10, m.disk, C)

def page_temp(d, m, C, W, H):
    d.text((12,10), "TEMPERATURE", font=F22, fill=C["FG"])
    t_pct = clamp((m.temp-30)*(100.0/60.0),0,100)
    # büyük tek halka
    ring(d, 120,120,64, t_pct, C, width=14)
    d.text((120,120), f"{m.temp:0.1f}°C", font=F22, fill=C["FG"], anchor="mm")
    d.text((120,200), "CPU sıcaklık", font=F14, fill=C["FG"], anchor="mm")

PAGES = [page_system_material, page_disk_net, page_storage, page_temp]
PAGE_TITLES = ["SYSTEM", "DISK&NET", "STORAGE", "TEMP"]

# ---------- Gesture callback ----------
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
        # SYSTEM sayfasında grid yok; sade malzeme görünümü için
        if self.cur != 0:
            grid(d, self.W, self.H, self.C)
        # başlık (SYSTEM özelinde üst bar zaten başlığı içeriyor)
        if self.cur != 0:
            d.text((12, 10), PAGE_TITLES[self.cur], font=F22, fill=self.C["FG"])
        # içerik
        PAGES[self.cur](d, self.m, self.C, self.W, self.H)
        # footer
        d.text((self.W-12, self.H-8), f"{self.cur+1}/{len(PAGES)}", font=F12, fill=(150,150,150), anchor="rd")
        return img

    def _maybe_toggle_theme_on_tap(self):
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
        if g == last_gesture_code and (t - last_gesture_time_ms) < SWIPE_COOLDOWN_MS:
            touch.Gestures = 0
            return False

        changed = False
        if g == 0x03:        # LEFT
            if (t - last_gesture_time_ms) >= SWIPE_COOLDOWN_MS:
                self.cur = (self.cur - 1) % len(PAGES)
                changed = True
        elif g == 0x04:      # RIGHT
            if (t - last_gesture_time_ms) >= SWIPE_COOLDOWN_MS:
                self.cur = (self.cur + 1) % len(PAGES)
                changed = True
        elif g == 0x0B:      # DOUBLE CLICK -> tema
            if (t - last_gesture_time_ms) >= TAP_COOLDOWN_MS:
                self.dark = not self.dark
                self.C = DARK if self.dark else LIGHT
                changed = True

        touch.Gestures = 0
        if changed:
            last_gesture_time_ms = t
            last_gesture_code = g
        else:
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
