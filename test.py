#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Waveshare 1.69" (240x280) Touch LCD • Raspberry Pi 5
# Smartwatch tarzı Sistem Paneli
# İyileştirmeler: RAM/Storage küçük punto detay, Top Processes siyah boşluk fix,
# fan toggle butonu, tema sadece sağ üst tap ile.

import os, sys, time, math, threading, subprocess, logging
from collections import deque
from PIL import Image, ImageDraw, ImageFont

sys.path.append("..")
from lib import LCD_1inch69, Touch_1inch69

# --- RPi & Touch ---
RST, DC, BL = 27, 25, 18
TP_INT, TP_RST = 4, 17
Mode, Flag = 2, 0
touch = None

# --- Debounce ---
SWIPE_COOLDOWN_MS  = 600
SCROLL_COOLDOWN_MS = 100
TAP_COOLDOWN_MS    = 250
last_gesture_time_ms = 0
last_gesture_code    = 0
last_scroll_time_ms  = 0
last_tap_time_ms     = 0
last_button_time_ms  = 0   # fan buton

logging.basicConfig(level=logging.INFO)

# --- Tema (mavi/pembe yok) ---
DARK = dict(
    BG=(10,12,18), FG=(242,244,248),
    TEAL=(50,190,165), ORANGE=(245,145,50), VIOLET=(160,120,250),
    LIME=(140,235,90), AMBER=(255,200,80),
    OK=(90,210,130), WARN=(255,185,70), BAD=(255,95,95),
    GRID=(26,32,40), BARBG=(28,32,38),
    SURFACE=(18,22,30), SURFACE2=(22,27,35),
    CHIP_DARK=(36,42,56)
)
LIGHT = dict(
    BG=(244,246,252), FG=(22,24,28),
    TEAL=(0,165,140), ORANGE=(230,140,0), VIOLET=(135,95,220),
    LIME=(110,200,70), AMBER=(235,180,60),
    OK=(44,175,100), WARN=(230,140,0), BAD=(210,40,40),
    GRID=(210,214,220), BARBG=(210,214,220),
    SURFACE=(255,255,255), SURFACE2=(248,249,251),
    CHIP_DARK=(230,232,238)
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

# büyük puntolar
F12,F14,F16,F18,F20,F22,F24,F26,F28,F30,F32,F36 = (load_font(s) for s in (12,14,16,18,20,22,24,26,28,30,32,36))

def clamp(v, lo, hi):
    try:
        v = float(v)
        if not math.isfinite(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def now_ms(): return int(time.time()*1000)
def bytes_gb(b): return b/1024/1024/1024

# --- Çizim yardımcıları ---
def rounded_fill(d, box, radius, fill): d.rounded_rectangle(box, radius=radius, fill=fill)

def chip(d, x, y, text, bg, fg, font=F18, pad=7):
    w = int(d.textlength(text, font=font)) + 2*pad
    h = 24 if font.size >= 18 else 20
    d.rounded_rectangle((x,y,x+w,y+h), radius=10, fill=bg)
    d.text((x+pad, y+2), text, font=font, fill=fg)
    return w, h

def ring(d, cx, cy, r, pct, track, color, width=14):
    pct = clamp(pct,0,100)/100.0
    d.arc((cx-r, cy-r, cx+r, cy+r), start=135, end=405, width=width, fill=track)
    d.arc((cx-r, cy-r, cx+r, cy+r), start=135, end=135+int(270*pct), width=width, fill=color)

def bar(d, x,y,w,h,pct,color,track):
    pct = clamp(pct,0,100)
    d.rounded_rectangle([x,y,x+w,y+h], radius=h//2, fill=track)
    d.rounded_rectangle([x,y,x+int(w*pct/100.0),y+h], radius=h//2, fill=color)

def sparkline(d, x,y,w,h,series,color,grid_col):
    d.rectangle((x,y,x+w,y+h), outline=grid_col, width=1)
    vals=[]
    for v in list(series):
        try:
            vv=float(v)
            if math.isfinite(vv): vals.append(vv)
        except: pass
    if len(vals) < 2:
        py = y + h//2
        d.line((x,py,x+w,py), fill=color, width=3)
        return
    mn, mx = min(vals), max(vals)
    rng = max(1e-6, mx-mn)
    prev=None
    for i,v in enumerate(vals):
        t=(v-mn)/rng
        px = x + int(i*(w-1)/max(1,len(vals)-1))
        py = y + h - 1 - int(t*(h-2))
        if prev: d.line((prev[0],prev[1],px,py), fill=color, width=3)
        prev=(px,py)

# --- Metrikler ---
try:
    import psutil
except Exception:
    psutil = None

class Metrics:
    def __init__(self, hist_len=90):
        self.cpu=self.ram=self.disk=self.temp=0.0
        self.mem_total=0; self.mem_used=0
        self.disk_total=0; self.disk_used=0
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

    def _mem_totals(self):
        if psutil:
            vm = psutil.virtual_memory()
            self.mem_total = int(vm.total)
            self.mem_used  = int(vm.total - vm.available)
            self.ram       = clamp(vm.percent, 0, 100)
        else:
            try:
                meminfo = {}
                with open("/proc/meminfo") as f:
                    for line in f:
                        k,v,*_ = line.split()
                        meminfo[k.rstrip(":")] = int(v)*1024
                total = meminfo.get("MemTotal",0)
                free  = meminfo.get("MemAvailable",0)
                used  = max(0,total-free)
                self.mem_total = total; self.mem_used = used
                self.ram = 100.0*used/max(1,total)
            except Exception:
                pass

    def _disk_totals(self):
        try:
            if psutil:
                u = psutil.disk_usage("/")
                self.disk       = clamp(u.percent, 0, 100)
                self.disk_total = int(u.total); self.disk_used = int(u.used)
            else:
                st = os.statvfs("/")
                total = st.f_blocks*st.f_frsize
                free  = st.f_bavail*st.f_frsize
                used  = total-free
                self.disk_total = int(total); self.disk_used = int(used)
                self.disk = 100.0*used/max(1,total)
        except Exception:
            pass

    def update(self):
        if psutil:
            try: self.cpu = clamp(psutil.cpu_percent(interval=None), 0, 100)
            except Exception: pass
        else:
            try: self.cpu = clamp(os.getloadavg()[0]*25.0, 0, 100)
            except Exception: pass
        self._mem_totals()
        self._disk_totals()
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

# --- Fan Controller ---
class FanControl:
    def __init__(self):
        self.pwm_path = None
        self.pwm_enable = None
        self.cool_cur = None
        self.cool_max = None
        self.state = 0  # 0 kapalı, 1 açık
        self._discover()

    def _ls(self, root):
        try: return [os.path.join(root, x) for x in os.listdir(root)]
        except Exception: return []

    def _discover(self):
        # hwmon pwm
        for hw in self._ls("/sys/class/hwmon"):
            for node in self._ls(hw):
                p = os.path.join(node, "pwm1")
                if os.path.exists(p):
                    self.pwm_path = p
                    e = os.path.join(node, "pwm1_enable")
                    if os.path.exists(e): self.pwm_enable = e
                    return
        # thermal cooling_device
        for cd in self._ls("/sys/class/thermal"):
            if not os.path.basename(cd).startswith("cooling_device"):
                continue
            cur = os.path.join(cd, "cur_state")
            mx  = os.path.join(cd, "max_state")
            if os.path.exists(cur) and os.path.exists(mx):
                self.cool_cur, self.cool_max = cur, mx
                return

    def set_percent(self, pct):
        pct = clamp(pct, 0, 100)
        try:
            if self.pwm_path:
                if self.pwm_enable:
                    # 1: enable manual
                    open(self.pwm_enable, "w").write("1\n")
                val = int(255 * pct/100.0)
                open(self.pwm_path, "w").write(str(val) + "\n")
                self.state = 1 if pct > 0 else 0
                return True
            elif self.cool_cur and self.cool_max:
                mx = int(open(self.cool_max).read().strip() or "0")
                target = int(mx if pct > 0 else 0)
                open(self.cool_cur, "w").write(str(target) + "\n")
                self.state = 1 if pct > 0 else 0
                return True
        except Exception:
            pass
        return False

    def toggle(self):
        if self.state:
            return self.set_percent(0)
        else:
            return self.set_percent(100)

# --- SYSTEM sayfası (scrollable) ---
def render_system_scrollable(W, H, m, C, fan_on):
    y = 0
    content_h_min = H + 1
    img = Image.new("RGB", (W, max(content_h_min, 1100)), C["BG"])
    d = ImageDraw.Draw(img)

    # App bar (System daha küçük)
    rounded_fill(d, (8,6, W-8, 78), radius=14, fill=C["SURFACE"])
    d.text((18, 20), "System", font=F32, fill=C["FG"])
    hhmm = time.strftime("%H:%M")
    day  = time.strftime("%a %d %b")
    d.text((W-12, 16), hhmm, font=F30, fill=C["TEAL"], anchor="ra")
    d.text((W-98, 48), day,  font=F16, fill=(200,205,210))
    y = 90

    # Temperature
    rounded_fill(d, (8,y, W-8, y+116), radius=14, fill=C["SURFACE2"])
    chip(d, 16, y+10, "Temperature", C["ORANGE"], (0,0,0))
    t_pct = clamp((m.temp-30)*(100.0/60.0), 0, 100)
    ring(d, 58, y+68, 30, t_pct, track=C["BARBG"], color=C["ORANGE"], width=14)
    d.text((100, y+36), f"{m.temp:0.1f}°C", font=F30, fill=C["FG"])
    bar(d, 100, y+76, W-100-16, 12, t_pct, color=C["ORANGE"], track=C["BARBG"])
    y += 128

    # CPU
    rounded_fill(d, (8,y, W-8, y+128), radius=14, fill=C["SURFACE2"])
    chip(d, 16, y+10, "CPU", C["VIOLET"], (255,255,255))
    ring(d, 58, y+70, 30, m.cpu, track=C["BARBG"], color=C["VIOLET"], width=14)
    d.text((100, y+36), f"{m.cpu:0.0f}%", font=F30, fill=C["FG"])
    sparkline(d, 100, y+70, W-100-16, 46, m.hcpu, C["VIOLET"], C["GRID"])
    y += 140

    # RAM (yüzde büyük, alt satır küçük punto used/total)
    rounded_fill(d, (8,y, W-8, y+116), radius=14, fill=C["SURFACE2"])
    chip(d, 16, y+10, "RAM", C["TEAL"], (0,0,0))
    used_gb  = bytes_gb(m.mem_used)
    total_gb = bytes_gb(m.mem_total)
    d.text((16, y+42), f"{m.ram:0.0f}%", font=F30, fill=C["FG"])
    d.text((16, y+72), f"{used_gb:.1f} / {total_gb:.1f} GB", font=F18, fill=(200,205,210))  # küçük punto
    bar(d, 16, y+92, W-16-16, 14, m.ram, color=C["TEAL"], track=C["BARBG"])
    y += 128

    # Storage (yüzde bar + alt satır küçük punto used/total)
    rounded_fill(d, (8,y, W-8, y+118), radius=14, fill=C["SURFACE2"])
    chip(d, 16, y+10, "Storage", C["AMBER"], (0,0,0))
    du = (bytes_gb(m.disk_used), bytes_gb(m.disk_total))
    d.text((16, y+42), f"{m.disk:0.0f}%", font=F30, fill=C["FG"])
    d.text((16, y+72), f"{du[0]:.1f} / {du[1]:.1f} GB", font=F18, fill=(200,205,210))  # küçük punto
    bar(d, 16, y+92, W-16-16, 14, m.disk, color=C["LIME"], track=C["BARBG"])
    y += 130

    # Network
    rounded_fill(d, (8,y, W-8, y+100), radius=14, fill=C["SURFACE2"])
    chip(d, 16, y+10, "Network", C["TEAL"], (0,0,0))
    d.text((16, y+48), f"Up {m.up:0.0f} KB/s", font=F22, fill=C["TEAL"])
    d.text((16, y+74), f"Dn {m.dn:0.0f} KB/s", font=F22, fill=C["ORANGE"])
    y += 112

    # System Info (hizalı satırlar, bol aralık)
    rounded_fill(d, (8,y, W-8, y+160), radius=14, fill=C["SURFACE2"])
    chip(d, 16, y+10, "System Info", C["LIME"], (0,0,0))
    try:
        boot = psutil.boot_time() if psutil else time.time()-1
        upt = time.time()-boot
    except Exception:
        upt = 0
    dds, rr = divmod(int(upt), 86400); hhs, rr = divmod(rr, 3600); mms, _ = divmod(rr, 60)
    try:
        ip = subprocess.check_output(["hostname","-I"]).decode().strip().split()[0]
    except Exception:
        ip = "0.0.0.0"
    try:
        arm = subprocess.check_output(["vcgencmd","measure_clock","arm"]).decode().split("=")[1]
        arm = int(arm)/1_000_000
    except Exception:
        try:
            cf = psutil.cpu_freq(); arm = cf.current if cf else 0
        except Exception:
            arm = 0
    try:
        la1,la5,la15 = os.getloadavg()
    except Exception:
        la1=la5=la15=0.0

    label_x, value_x = 16, 120
    line_y, line_h  = y+46, 28
    def row(lbl, val):
        nonlocal line_y
        d.text((label_x, line_y), lbl, font=F20, fill=(200,205,210))
        d.text((value_x, line_y), val, font=F20, fill=C["FG"])
        line_y += line_h

    row("Uptime", f"{dds}g {hhs}s {mms}d")
    row("IP",     ip)
    row("CPU Hz", f"{arm:0.0f} MHz")
    row("Load",   f"{la1:.2f} {la5:.2f} {la15:.2f}")
    y += 172

    # Top Processes (uzun pano içinde, siyah boşluk yok)
    rounded_fill(d, (8,y, W-8, y+150), radius=14, fill=C["SURFACE2"])
    chip(d, 16, y+10, "Top Processes", C["VIOLET"], (255,255,255))
    yy = y+48
    try:
        procs=[]
        if psutil:
            for p in psutil.process_iter(attrs=["pid","name","cpu_percent","memory_percent"]):
                try:
                    info = p.info
                    _ = p.cpu_percent(interval=0.0)  # non-block update
                    procs.append(info)
                except Exception:
                    pass
            if any((x.get("cpu_percent",0.0) or 0) > 0 for x in procs):
                procs.sort(key=lambda x: x.get("cpu_percent",0.0), reverse=True)
            else:
                procs.sort(key=lambda x: x.get("memory_percent",0.0), reverse=True)
            for rowp in procs[:4]:
                name=str(rowp.get("name",""))[:14]
                cpu = clamp(rowp.get("cpu_percent",0.0),0,100)
                mem = clamp(rowp.get("memory_percent",0.0),0,100)
                d.text((16,yy), name, font=F20, fill=C["FG"])
                d.text((W-18,yy), f"{cpu:0.0f}% CPU  {mem:0.0f}% MEM", font=F18, fill=(200,205,210), anchor="ra")
                yy+=30
        else:
            d.text((16,yy), "psutil yok", font=F20, fill=C["FG"])
    except Exception:
        pass
    y += 162

    # FAN Toggle Button (sağ alt köşe kartı)
    btn_h = 52
    rounded_fill(d, (8,y, W-8, y+btn_h), radius=14, fill=C["SURFACE2"])
    btn_text = "FAN: ON" if fan_on else "FAN: OFF"
    btn_col  = C["TEAL"] if fan_on else C["ORANGE"]
    chip(d, 16, y+12, btn_text, btn_col, (0,0,0), font=F18)
    # buton alanını geri döndürmek için rectangle koordinatı döndürelim
    btn_box = (8, y, W-8, y+btn_h)
    y += btn_h + 12

    content_h = max(y+10, content_h_min)
    if content_h > img.height:
        new_img = Image.new("RGB", (W, content_h), C["BG"])
        new_img.paste(img, (0,0))
        img = new_img

    return img, content_h, btn_box

# --- Diğer sayfalar (basit) ---
def page_disk_net(d, m, C, W, H):
    d.text((12,10), "DISK & NET", font=F28, fill=C["FG"])
    d.text((12,56), f"DISK {m.disk:0.0f}%", font=F24, fill=C["FG"])
    d.rounded_rectangle([12,84,W-12,104], radius=10, fill=C["SURFACE2"])
    bar(d, 14,86, W-28, 14, m.disk, color=C["LIME"], track=C["BARBG"])
    d.text((12,130), f"UP {m.up:0.0f} KB/s", font=F22, fill=C["TEAL"])
    d.text((12,160), f"DN {m.dn:0.0f} KB/s", font=F22, fill=C["ORANGE"])

def page_storage(d, m, C, W, H):
    d.text((12,10), "STORAGE", font=F28, fill=C["FG"])
    y=56
    try:
        import psutil as ps
        for p in ps.disk_partitions():
            try:
                u = ps.disk_usage(p.mountpoint)
                d.text((12,y), f"{p.mountpoint} {u.percent:0.0f}%", font=F22, fill=C["FG"])
                d.rounded_rectangle([120,y+4,W-12,y+20], radius=8, fill=C["SURFACE2"])
                bar(d, 122, y+6, W-134, 12, u.percent, color=C["ORANGE"], track=C["BARBG"])
                y+=36
                if y > H-24: break
            except Exception:
                pass
    except Exception:
        d.text((12,y), f"/ {m.disk:0.0f}%", font=F22, fill=C["FG"])
        d.rounded_rectangle([120,y+4,W-12,y+20], radius=8, fill=C["SURFACE2"])
        bar(d, 122, y+6, W-134, 12, m.disk, color=C["ORANGE"], track=C["BARBG"])

def page_temp(d, m, C, W, H):
    d.text((12,10), "TEMPERATURE", font=F28, fill=C["FG"])
    t_pct = clamp((m.temp-30)*(100.0/60.0),0,100)
    ring(d, 120,120,66, t_pct, track=C["BARBG"], color=C["ORANGE"], width=16)
    d.text((120,120), f"{m.temp:0.1f}°C", font=F28, fill=C["FG"], anchor="mm")

# --- Touch Callback ---
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

# --- App ---
class App:
    def __init__(self):
        self.disp = LCD_1inch69.LCD_1inch69(rst=RST, dc=DC, bl=BL, tp_int=TP_INT, tp_rst=TP_RST, bl_freq=100)
        self.disp.Init()
        self.disp.clear()
        try: self.disp.bl_DutyCycle(90)
        except Exception: pass
        self.W, self.H = self.disp.width, self.disp.height  # 240x280

        self.dark = True
        self.C = DARK
        self.m = Metrics()
        for _ in range(3): self.m.update(); time.sleep(0.1)

        global touch
        touch = Touch_1inch69.Touch_1inch69()
        touch.init()
        touch.Set_Mode(2)
        touch.GPIO_TP_INT.when_pressed = Int_Callback

        self.cur = 0
        self.system_canvas = None
        self.system_h = self.H
        self.scroll_y = 0
        self.scroll_step = 56

        self.fan = FanControl()
        self.fan_on = False

        self.btn_box = None  # fan buton pencere koordinatı (canvas'tan dönecek)

        self.running = True
        threading.Thread(target=self._metrics_loop, daemon=True).start()

    def _metrics_loop(self):
        while self.running:
            self.m.update()
            time.sleep(0.5)

    def _render_system_canvas(self):
        img, h, btn_box = render_system_scrollable(self.W, self.H, self.m, self.C, self.fan_on)
        self.system_canvas = img
        self.system_h = h
        self.btn_box = btn_box
        max_off = max(0, self.system_h - self.H)
        self.scroll_y = max(0, min(self.scroll_y, max_off))

    def _render_page(self):
        if self.cur == 0:
            if self.system_canvas is None:
                self._render_system_canvas()
            max_off = max(0, self.system_h - self.H)
            self.scroll_y = max(0, min(self.scroll_y, max_off))
            view = self.system_canvas.crop((0, self.scroll_y, self.W, self.scroll_y + self.H))
            return view
        else:
            img = Image.new("RGB", (self.W, self.H), self.C["BG"])
            d = ImageDraw.Draw(img)
            if self.cur == 1:
                page_disk_net(d, self.m, self.C, self.W, self.H)
            elif self.cur == 2:
                page_storage(d, self.m, self.C, self.W, self.H)
            elif self.cur == 3:
                page_temp(d, self.m, self.C, self.W, self.H)
            return img

    def _tap_in_rect(self, x, y, rect):
        if not rect: return False
        x1,y1,x2,y2 = rect
        return (x1 <= x <= x2) and (y1 <= y <= y2)

    def _handle_single_tap_actions(self):
        # SADECE burada tema ve fan butonunu yönetiyoruz.
        global last_tap_time_ms, last_button_time_ms
        t = now_ms()
        x, y = getattr(touch, "X_point", None), getattr(touch, "Y_point", None)
        if x is None or y is None: return False

        changed = False

        # Tema: SADECE sağ üst köşe (52x40) tek dokunuş
        if x > self.W-52 and y < 40 and (t - last_tap_time_ms) > TAP_COOLDOWN_MS:
            last_tap_time_ms = t
            self.dark = not self.dark
            self.C = DARK if self.dark else LIGHT
            self.system_canvas = None
            changed = True

        # Fan buton: SADECE System sayfasında, buton görünür pencerede mi?
        if self.cur == 0 and self.btn_box and (t - last_button_time_ms) > TAP_COOLDOWN_MS:
            # Canvas'taki butonun pencere içi koordinatlarını hesapla
            x1,y1,x2,y2 = self.btn_box
            # Ekrandaki görünür dikdörtgen: (x1, y1 - scroll_y) .. (x2, y2 - scroll_y)
            if self._tap_in_rect(x, y + self.scroll_y, (x1,y1,x2,y2)):
                last_button_time_ms = t
                if self.fan.toggle():
                    self.fan_on = self.fan.state == 1
                    self.system_canvas = None
                    changed = True

        return changed

    def _handle_gesture(self):
        global last_gesture_time_ms, last_gesture_code, last_scroll_time_ms
        g = getattr(touch, "Gestures", 0)
        t = now_ms()

        # Jest yoksa tek-tap aksiyonuna bak
        if not g:
            return self._handle_single_tap_actions()

        # sol/sağ sayfa debounce
        if g in (0x03,0x04) and g == last_gesture_code and (t - last_gesture_time_ms) < SWIPE_COOLDOWN_MS:
            touch.Gestures = 0
            return False

        changed = False

        # DİKEY SCROLL (TERS): DOWN => içerik YUKARI, UP => içerik AŞAĞI
        if self.cur == 0 and g in (0x01, 0x02):
            if t - last_scroll_time_ms >= SCROLL_COOLDOWN_MS:
                max_off = max(0, self.system_h - self.H)
                if g == 0x01:   # DOWN: yukarı git
                    self.scroll_y = max(0, self.scroll_y - self.scroll_step)
                elif g == 0x02: # UP: aşağı git
                    self.scroll_y = min(max_off, self.scroll_y + self.scroll_step)
                self.scroll_y = max(0, min(self.scroll_y, max_off))
                last_scroll_time_ms = t
                changed = True

        elif g == 0x03:        # LEFT  -> önceki sayfa
            if (t - last_gesture_time_ms) >= SWIPE_COOLDOWN_MS:
                self.cur = (self.cur - 1) % 4
                if self.cur == 0: self.system_canvas = None; self.scroll_y = 0
                changed = True

        elif g == 0x04:        # RIGHT -> sonraki sayfa
            if (t - last_gesture_time_ms) >= SWIPE_COOLDOWN_MS:
                self.cur = (self.cur + 1) % 4
                if self.cur == 0: self.system_canvas = None; self.scroll_y = 0
                changed = True

        # ÇİFTE TIKLAMA ile tema DEĞİŞTİRMEYİ KALDIRDIK. (0x0B yok)

        touch.Gestures = 0
        if g in (0x03,0x04) and changed:
            last_gesture_time_ms = t
            last_gesture_code = g
        elif g in (0x03,0x04):
            last_gesture_code = g

        return changed

    def run(self):
        self._render_system_canvas()
        img = self._render_page()
        self.disp.ShowImage(img)
        last_draw = time.time()

        global Flag
        while True:
            if Flag == 1:
                Flag = 0
                if self._handle_gesture():
                    if self.cur == 0 and self.system_canvas is None:
                        self._render_system_canvas()
                    img = self._render_page()
                    self.disp.ShowImage(img)
                    last_draw = time.time()
            else:
                if time.time() - last_draw > 0.6:
                    if self.cur == 0:
                        self._render_system_canvas()
                    img = self._render_page()
                    self.disp.ShowImage(img)
                    last_draw = time.time()
            time.sleep(0.01)

# --- Main ---
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
