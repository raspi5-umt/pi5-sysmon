#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, math, threading, subprocess
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import psutil

# ---------- LCD SÜRÜCÜ ----------
from lib.LCD_1inch69 import LCD_1inch69

# ---------- Dokunmatik ----------
try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15

# ---------- Tema Renkleri ----------
DARK = dict(
    BG=(5,8,12), FG=(235,235,235), ACC1=(120,180,255), ACC2=(255,120,180),
    OK=(90,200,120), WARN=(255,170,0), BAD=(255,80,80),
    GRID=(25,30,36), BARBG=(22,26,32), MUTED=(120,120,120)
)

LIGHT = dict(
    BG=(240,242,246), FG=(20,22,28), ACC1=(30,90,220), ACC2=(200,60,120),
    OK=(30,170,90), WARN=(230,140,0), BAD=(210,40,40),
    GRID=(210,214,220), BARBG=(210,214,220), MUTED=(80,80,80)
)

# ---------- Font ----------
def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F12, F14, F18, F22 = (load_font(s) for s in (12,14,18,22))

# ---------- Yardımcı ----------
def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def pick_color(p, C):
    p = clamp(p, 0, 100)
    return C["OK"] if p < 70 else (C["WARN"] if p < 85 else C["BAD"])

def bar(d, x,y,w,h,pct,C):
    pct = clamp(pct,0,100)
    d.rectangle([x,y,x+w,y+h], fill=C["BARBG"])
    d.rectangle([x,y,x+int(w*pct/100.0),y+h], fill=pick_color(pct,C))

# ---------- Metrikler ----------
class Metrics:
    def __init__(self):
        self.cpu = self.ram = self.temp = self.disk = 0.0

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
        self.cpu = clamp(psutil.cpu_percent(interval=None), 0, 100)
        self.ram = clamp(psutil.virtual_memory().percent, 0, 100)
        self.disk = clamp(psutil.disk_usage("/").percent, 0, 100)
        self.temp = clamp(self._temp(), 0, 120)

# ---------- Dokunmatik ----------
class Touch:
    def __init__(self):
        self.available = SMBUS_OK
        self.bus = None
        if self.available:
            try:
                self.bus = SMBus(I2C_BUS)
                self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 1)
            except Exception:
                self.available = False
                self.bus = None
        self.start_x = None
        self.start_y = None
        self.swipe_thresh = 40

    def read_point(self, W, H):
        if not self.available: return None
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 7)
            event = d[1] & 0x0F
            if event == 0:
                self.start_x = self.start_y = None
                return None
            x = ((d[2]&0x0F)<<8) | d[3]
            y = ((d[4]&0x0F)<<8) | d[5]
            return (max(0,min(W-1,x)), max(0,min(H-1,y)))
        except Exception:
            return None

    def detect_swipe(self, x,y):
        if self.start_x is None or self.start_y is None:
            self.start_x, self.start_y = x,y
            return None
        dx = x - self.start_x
        dy = y - self.start_y
        if abs(dx) > abs(dy):  # yatay swipe
            if dx <= -self.swipe_thresh: self.start_x=self.start_y=None; return "L"
            if dx >=  self.swipe_thresh: self.start_x=self.start_y=None; return "R"
        else:  # dikey swipe
            if dy <= -self.swipe_thresh: self.start_x=self.start_y=None; return "U"
            if dy >=  self.swipe_thresh: self.start_x=self.start_y=None; return "D"
        return None

# ---------- Sayfalar ----------
def page_cpu(img, d, m, C, W, H):
    d.text((12,12),"CPU", font=F22, fill=C["FG"])
    bar(d, 20, 60, W-40, 24, m.cpu, C)
    d.text((W//2, 100), f"{m.cpu:.0f} %", font=F18, fill=pick_color(m.cpu,C), anchor="mm")

def page_ram(img, d, m, C, W, H):
    d.text((12,12),"RAM", font=F22, fill=C["FG"])
    bar(d, 20, 60, W-40, 24, m.ram, C)
    d.text((W//2, 100), f"{m.ram:.0f} %", font=F18, fill=pick_color(m.ram,C), anchor="mm")

def page_temp(img, d, m, C, W, H):
    d.text((12,12),"TEMP", font=F22, fill=C["FG"])
    bar(d, 20, 60, W-40, 24, m.temp, C)
    d.text((W//2, 100), f"{m.temp:.1f} °C", font=F18, fill=pick_color(m.temp,C), anchor="mm")

def page_all(img, d, m, C, W, H):
    d.text((12,12),"SUMMARY", font=F22, fill=C["FG"])
    d.text((12,60), f"CPU  : {m.cpu:.0f}%", font=F14, fill=C["FG"])
    d.text((12,80), f"RAM  : {m.ram:.0f}%", font=F14, fill=C["FG"])
    d.text((12,100), f"TEMP : {m.temp:.1f}°C", font=F14, fill=C["FG"])
    d.text((12,120), f"DISK : {m.disk:.0f}%", font=F14, fill=C["FG"])

# Matris: 2x2
PAGES = [
    [page_cpu, page_ram],
    [page_temp, page_all]
]

# ---------- Uygulama ----------
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
        self.touch = Touch()

        self.row = 0
        self.col = 0

        threading.Thread(target=self._metrics_loop, daemon=True).start()

    def _metrics_loop(self):
        while True:
            self.metrics.update()
            time.sleep(0.5)

    def _render(self, r, c):
        img = Image.new("RGB", (self.W, self.H), self.C["BG"])
        d = ImageDraw.Draw(img)
        for gy in range(0,self.H,28):
            d.line((0,gy,self.W,gy), fill=self.C["GRID"])
        d.text((self.W-16,8),"◑", font=F12, fill=self.C["MUTED"], anchor="ra")
        PAGES[r][c](img,d,self.metrics,self.C,self.W,self.H)
        return img

    def loop(self):
        while True:
            img = self._render(self.row, self.col)
            self.disp.ShowImage(img)

            if self.touch.available:
                pt = self.touch.read_point(self.W,self.H)
                if pt:
                    x,y = pt
                    # Sağ üst köşe → tema değiştir
                    if x > self.W-52 and y < 40:
                        self.theme_dark = not self.theme_dark
                        self.C = DARK if self.theme_dark else LIGHT
                        time.sleep(0.2)
                        continue
                    move = self.touch.detect_swipe(x,y)
                    if move == "L":
                        self.col = (self.col-1) % len(PAGES[0])
                    elif move == "R":
                        self.col = (self.col+1) % len(PAGES[0])
                    elif move == "U":
                        self.row = (self.row-1) % len(PAGES)
                    elif move == "D":
                        self.row = (self.row+1) % len(PAGES)

if __name__ == "__main__":
    App().loop()
