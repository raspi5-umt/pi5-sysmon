#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 1.69" Waveshare (LCD_1inch69) için NASA tadında sistem monitörü
# - Üretici sürücüsü: LCD_1inch69
# - Dokunmatik: CST816S (I2C addr 0x15) - swipe ile sayfa değişir
# - 4 sayfa: Özet, Disk&Net, Süreçler, Sistem
# - Sparkline çizgiler Numpy'sız, NaN güvenli
# - Yumuşak sayfa geçiş animasyonu

import os, sys, time, math, threading, subprocess
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import psutil

# -------- ÜRETİCİ SÜRÜCÜ (aynı klasörde lib/ altında olmalı) --------
if "lib" not in sys.path:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from LCD_1inch69 import LCD_1inch69

# -------- Dokunmatik (CST816S I2C) --------
try:
    from smbus2 import SMBus
    _SMBUS_OK = True
except Exception:
    _SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15

# -------- Görsel Ayarlar --------
DARK = {
    "BG": (5,8,12),
    "FG": (235,235,235),
    "ACC1": (120,180,255),
    "ACC2": (255,120,180),
    "OK": (90,200,120),
    "WARN": (255,170,0),
    "BAD": (255,80,80),
    "GRID": (25,30,36),
    "BAR_BG": (22,26,32)
}
LIGHT = {
    "BG": (240,242,246),
    "FG": (20,22,28),
    "ACC1": (30,90,220),
    "ACC2": (200,60,120),
    "OK": (30,170,90),
    "WARN": (230,140,0),
    "BAD": (210,40,40),
    "GRID": (210,214,220),
    "BAR_BG": (210,214,220)
}

def load_font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F12, F14, F16, F18, F22, F26 = (load_font(s) for s in (12,14,16,18,22,26))

def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def lerp(a,b,t): return a + (b-a)*t
def ease_out_cubic(t): return 1 - (1 - t)**3

# -------- Metrikler --------
class Metrics:
    def __init__(self, hist_len=90):
        self.cpu = self.ram = self.disk = self.temp = 0.0
        self.up = self.dn = 0.0
        self.hist_cpu = deque(maxlen=hist_len)
        self.hist_ram = deque(maxlen=hist_len)
        self.hist_tmp = deque(maxlen=hist_len)
        self.hist_up = deque(maxlen=hist_len)
        self.hist_dn = deque(maxlen=hist_len)
        self.last_net = psutil.net_io_counters()

    def _read_temp(self):
        try:
            out = subprocess.check_output(["vcgencmd","measure_temp"]).decode()
            return float(out.split("=")[1].split("'")[0])
        except Exception:
            try:
                return int(open("/sys/class/thermal/thermal_zone0/temp").read().strip())/1000.0
            except Exception:
                return 0.0

    def update(self):
        self.cpu = clamp(psutil.cpu_percent(interval=None), 0, 100)
        self.ram = clamp(psutil.virtual_memory().percent, 0, 100)
        self.disk = clamp(psutil.disk_usage("/").percent, 0, 100)
        self.temp = clamp(self._read_temp(), 0, 120)

        now = psutil.net_io_counters()
        self.up = max(0.0, (now.bytes_sent - self.last_net.bytes_sent)/1024.0)
        self.dn = max(0.0, (now.bytes_recv - self.last_net.bytes_recv)/1024.0)
        self.last_net = now

        self.hist_cpu.append(self.cpu); self.hist_ram.append(self.ram); self.hist_tmp.append(self.temp)
        self.hist_up.append(self.up);    self.hist_dn.append(self.dn)

# -------- Dokunmatik --------
class Touch:
    def __init__(self):
        self.available = _SMBUS_OK
        self.bus = None
        if self.available:
            try:
                self.bus = SMBus(I2C_BUS)
                self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 1)
            except Exception:
                self.available = False
                self.bus = None
        self.start_y = None
        self.swipe_thresh = 30

    def read_point(self, W, H):
        if not self.available: return None
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 7)
            event = d[1] & 0x0F
            if event == 0:
                self.start_y = None
                return None
            x = ((d[2] & 0x0F) << 8) | d[3]
            y = ((d[4] & 0x0F) << 8) | d[5]
            x = max(0, min(W-1, x)); y = max(0, min(H-1, y))
            return (x,y)
        except Exception:
            return None

    def detect_swipe(self, y):
        if self.start_y is None:
            self.start_y = y; return 0
        dy = y - self.start_y
        if dy <= -self.swipe_thresh:
            self.start_y = None; return -1  # up
        if dy >= self.swipe_thresh:
            self.start_y = None; return  1  # down
        return 0

# -------- Çizim araçları --------
def pick_color(p, C):
    p = clamp(p,0,100)
    return C["OK"] if p<70 else (C["WARN"] if p<85 else C["BAD"])

def bar(d, x,y,w,h,pct,C):
    pct = clamp(pct,0,100)
    d.rounded_rectangle([x,y,x+w,y+h], radius=6, fill=C["BAR_BG"])
    v = int(w*pct/100.0)
    d.rounded_rectangle([x,y,x+v,y+h], radius=6, fill=pick_color(pct,C))

def sparkline(d, x,y,w,h,data,color,grid,C):
    # arkaplan
    if grid:
        for gy in range(3):
            gy_y = y + int(gy*h/3)
            d.line((x,gy_y,x+w,gy_y), fill=C["GRID"])
    vals=[]
    for v in list(data):
        try:
            v=float(v)
            if math.isfinite(v): vals.append(v)
        except: pass
    if len(vals)<2:
        py = y + h//2
        d.line((x,py,x+w,py), fill=color, width=2)
        return
    mn, mx = min(vals), max(vals)
    if mx==mn:
        py = y + h//2
        d.line((x,py,x+w,py), fill=color, width=2)
        return
    prev=None
    n=len(vals)
    for i,v in enumerate(vals):
        t = (v-mn)/(mx-mn)
        px = x + int(i*(w-1)/max(1,n-1))
        py = y + h - 1 - int(t*(h-1))
        if prev:
            d.line((prev[0],prev[1],px,py), fill=color, width=2)
        prev=(px,py)

def ring_gauge(d, cx, cy, r, pct, C, width=10):
    # basit yay
    pct = clamp(pct,0,100)/100.0
    bbox=[cx-r,cy-r,cx+r,cy+r]
    d.arc(bbox, start=135, end=405, width=width, fill=C["BAR_BG"])
    end_ang = 135 + int(270*pct)
    d.arc(bbox, start=135, end=end_ang, width=width, fill=pick_color(pct*100,C))

# -------- Sayfalar --------
def page_summary(img, d, m, C, W, H):
    d.text((12,10), "SYSTEM", font=F22, fill=C["FG"])
    d.text((W-12,10), time.strftime("%H:%M"), font=F18, fill=C["ACC1"], anchor="ra")

    # halkalar: CPU / RAM / TEMP
    ring_gauge(d, 60, 92, 42, m.cpu, C)
    d.text((60,92), f"{m.cpu:0.0f}%", font=F14, fill=C["FG"], anchor="mm")
    d.text((60,118), "CPU", font=F12, fill=C["ACC1"], anchor="mm")

    ring_gauge(d, 180, 92, 42, m.ram, C)
    d.text((180,92), f"{m.ram:0.0f}%", font=F14, fill=C["FG"], anchor="mm")
    d.text((180,118), "RAM", font=F12, fill=C["ACC1"], anchor="mm")

    t_pct = clamp((m.temp-30)*(100.0/60.0),0,100)
    ring_gauge(d, 120, 170, 48, t_pct, C)
    d.text((120,170), f"{m.temp:0.1f}°C", font=F14, fill=C["FG"], anchor="mm")
    d.text((120,196), "TEMP", font=F12, fill=C["ACC2"], anchor="mm")

    # CPU spark
    d.text((12, 212), "CPU history", font=F12, fill=C["FG"])
    sparkline(d, 12, 228, W-24, 36, m.hist_cpu, C["ACC1"], True, C)

def page_disk_net(img, d, m, C, W, H):
    d.text((12,10), "DISK & NET", font=F22, fill=C["FG"])
    d.text((12,50), f"DISK {m.disk:0.0f}%", font=F18, fill=C["FG"])
    bar(d, 12, 70, W-24, 14, m.disk, C)

    d.text((12,100), f"UP {m.up:0.0f} KB/s", font=F16, fill=C["ACC1"])
    sparkline(d, 12, 118, W-24, 30, m.hist_up, C["ACC1"], True, C)
    d.text((12,156), f"DN {m.dn:0.0f} KB/s", font=F16, fill=C["ACC2"])
    sparkline(d, 12, 174, W-24, 30, m.hist_dn, C["ACC2"], True, C)

    d.text((W-12,H-10), "Yukarı/Aşağı kaydır", font=F12, fill=(160,160,160), anchor="rs")

def page_processes(img, d, m, C, W, H):
    d.text((12,10), "TOP PROCESSES", font=F22, fill=C["FG"])
    # CPU’ya göre ilk 6 süreç
    procs=[]
    for p in psutil.process_iter(attrs=["pid","name","cpu_percent","memory_percent"]):
        try:
            procs.append(p.info)
        except Exception:
            pass
    procs.sort(key=lambda x: x.get("cpu_percent",0.0), reverse=True)
    y=44
    for row in procs[:6]:
        name=str(row.get("name",""))[:14]
        cpu = clamp(row.get("cpu_percent",0.0),0,100)
        mem = clamp(row.get("memory_percent",0.0),0,100)
        d.text((12,y), f"{name}", font=F14, fill=C["FG"])
        d.text((W-12,y), f"{cpu:0.0f}% CPU  {mem:0.0f}% MEM", font=F12, fill=C["ACC1"], anchor="ra")
        y+=28

def page_system(img, d, m, C, W, H):
    d.text((12,10), "SYSTEM INFO", font=F22, fill=C["FG"])
    # uptime
    upt = time.time() - psutil.boot_time()
    dys, r = divmod(int(upt), 86400)
    hrs, r = divmod(r, 3600)
    mins,_ = divmod(r, 60)
    # ip
    ip="0.0.0.0"
    try:
        ip = subprocess.check_output(["hostname","-I"]).decode().strip().split()[0]
    except Exception:
        pass
    # freq
    try:
        arm = subprocess.check_output(["vcgencmd","measure_clock","arm"]).decode().split("=")[1]
        arm = int(arm)/1_000_000
    except Exception:
        arm=0
    d.text((12,48), f"Uptime: {dys}g {hrs}s {mins}d", font=F16, fill=C["FG"])
    d.text((12,72), f"IP: {ip}", font=F16, fill=C["FG"])
    d.text((12,96), f"CPU Freq: {arm:0.0f} MHz", font=F16, fill=C["FG"])
    # disk detayı
    y=124
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            d.text((12,y), f"{part.mountpoint} {u.percent:0.0f}%", font=F14, fill=C["FG"])
            bar(d, 112, y+2, W-124, 10, u.percent, C)
            y += 22
            if y > H-24: break
        except Exception:
            continue

PAGES = [page_summary, page_disk_net, page_processes, page_system]

# -------- Uygulama --------
class App:
    def __init__(self):
        self.disp = LCD_1inch69()
        self.disp.Init()
        try: self.disp.bl_DutyCycle(100)
        except Exception: pass

        self.W, self.H = self.disp.width, self.disp.height
        self.theme_dark = True
        self.C = DARK

        self.metrics = Metrics()
        for _ in range(4):
            self.metrics.update(); time.sleep(0.1)

        self.touch = Touch()
        self.current = 0
        self.target = 0
        self.anim_t = 1.0

        self.img = Image.new("RGB", (self.W, self.H), self.C["BG"])
        self.draw = ImageDraw.Draw(self.img)

        self.running = True
        self.t_metrics = threading.Thread(target=self._metrics_loop, daemon=True)
        self.t_metrics.start()

    def _metrics_loop(self):
        while self.running:
            self.metrics.update()
            time.sleep(0.5)

    def _handle_touch(self):
        pt = self.touch.read_point(self.W, self.H)
        if not pt: return
        x,y = pt
        # sağ üst köşe dokunuşu: tema değiştir
        if x > self.W-50 and y < 40:
            self.theme_dark = not self.theme_dark
            self.C = DARK if self.theme_dark else LIGHT
            time.sleep(0.2)
            return
        swipe = self.touch.detect_swipe(y)
        if swipe == -1:
            self._switch((self.current-1) % len(PAGES))
        elif swipe == 1:
            self._switch((self.current+1) % len(PAGES))

    def _switch(self, idx):
        if idx == self.current: return
        self.target = idx
        self.anim_t = 0.0

    def _render_page(self, idx):
        img = Image.new("RGB", (self.W, self.H), self.C["BG"])
        d = ImageDraw.Draw(img)
        # grid
        for gy in range(0, self.H, 28):
            d.line((0, gy, self.W, gy), fill=self.C["GRID"])
        PAGES[idx](img, d, self.metrics, self.C, self.W, self.H)
        return img

    def loop(self):
        fps=30.0; dt=1.0/fps; last=time.time()
        while True:
            now=time.time()
            if now - last < dt: time.sleep(dt-(now-last))
            last=now

            if self.touch.available: self._handle_touch()

            if self.anim_t < 1.0:
                self.anim_t = min(1.0, self.anim_t + 0.12)
                t = ease_out_cubic(self.anim_t)
                dirn = 1 if (self.target > self.current or (self.current==len(PAGES)-1 and self.target==0)) else -1
                off = int(lerp(0, -dirn*self.H, t))
                cur = self._render_page(self.current)
                tar = self._render_page(self.target)
                frame = Image.new("RGB", (self.W, self.H), self.C["BG"])
                frame.paste(cur, (0, off))
                frame.paste(tar, (0, off + dirn*self.H))
                self.disp.ShowImage(frame)
                if self.anim_t >= 1.0:
                    self.current = self.target
            else:
                img = self._render_page(self.current)
                self.disp.ShowImage(img)

if __name__ == "__main__":
    try:
        App().loop()
    except KeyboardInterrupt:
        pass
