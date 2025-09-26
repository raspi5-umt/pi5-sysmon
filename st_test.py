#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 1.69" Waveshare ekran için basit sistem monitörü (CPU, RAM, DISK, TEMP, NET).
# Üretici sürücüsünü kullanır: from lib import LCD_1inch69
# Dokunmatik şart değil; varsa hiç dokunmadan akmaya devam eder.

import os, sys, time, math, subprocess
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import psutil

# Üreticinin lib yolunu ekle (dosyaların durduğu klasör yapına uyuyor)
# Bu dosya, 1inch69_LCD_test.py ile aynı yerde durmalı. Yanında "lib" klasörü var.
if "lib" not in sys.path:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from lib import LCD_1inch69

# ---------- AYAR ----------
BG = (5, 8, 12)
FG = (235, 235, 235)
ACCENT = (120, 180, 255)
OK = (90, 200, 120)
WARN = (255, 170, 0)
BAD = (255, 80, 80)
BAR_BG = (25, 30, 36)
GRID = (25, 30, 36)

def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F14, F18, F24 = load_font(14), load_font(18), load_font(24)

def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def pick_color(p):
    p = clamp(p, 0, 100)
    return OK if p < 70 else (WARN if p < 85 else BAD)

def draw_bar(drw, x, y, w, h, pct):
    pct = clamp(pct, 0, 100)
    drw.rounded_rectangle([x, y, x+w, y+h], radius=6, fill=BAR_BG)
    v = int(w * pct / 100.0)
    drw.rounded_rectangle([x, y, x+v, y+h], radius=6, fill=pick_color(pct))

def read_temp_c():
    # vcgencmd varsa onu kullan, yoksa hwmon
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"]).decode()
        return float(out.split("=")[1].split("'")[0])
    except Exception:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read().strip()) / 1000.0
        except Exception:
            return 0.0

def main():
    # ÜRETİCİ SÜRÜCÜSÜ
    disp = LCD_1inch69.LCD_1inch69()
    disp.Init()
    try:
        disp.clear()
    except Exception:
        pass
    try:
        # bazı sürümlerde var; yoksa sorun değil
        disp.bl_DutyCycle(100)
    except Exception:
        pass

    W, H = disp.width, disp.height

    img = Image.new("RGB", (W, H), BG)
    drw = ImageDraw.Draw(img)

    last_net = psutil.net_io_counters()

    # ilk ölçümleri al ki barlar boş kalmasın
    for _ in range(2):
        time.sleep(0.1)

    while True:
        # METRİKLER
        cpu = clamp(psutil.cpu_percent(interval=0.2), 0, 100)
        ram = clamp(psutil.virtual_memory().percent, 0, 100)
        disk = clamp(psutil.disk_usage("/").percent, 0, 100)
        temp = clamp(read_temp_c(), 0, 120)

        now_net = psutil.net_io_counters()
        up_kbs = clamp((now_net.bytes_sent - last_net.bytes_sent) / 1024.0, 0, 99999)
        dn_kbs = clamp((now_net.bytes_recv - last_net.bytes_recv) / 1024.0, 0, 99999)
        last_net = now_net

        # ÇİZ
        drw.rectangle((0, 0, W, H), fill=BG)
        # arka plana hafif grid
        for gy in range(0, H, 28):
            drw.line((0, gy, W, gy), fill=GRID)

        drw.text((12, 10), "SYSTEM", font=F24, fill=FG)
        drw.text((W-12, 10), time.strftime("%H:%M"), font=F18, fill=ACCENT, anchor="ra")

        drw.text((12, 46), f"CPU {cpu:4.0f}%", font=F18, fill=FG);   draw_bar(drw, 12, 66, W-24, 14, cpu)
        drw.text((12, 96), f"RAM {ram:4.0f}%", font=F18, fill=FG);   draw_bar(drw, 12,116, W-24, 14, ram)
        t_pct = (temp - 30) * (100.0 / 60.0)  # 30-90°C → 0-100%
        drw.text((12,146), f"TEMP {temp:4.1f}°C", font=F18, fill=FG); draw_bar(drw, 12,166, W-24, 14, t_pct)
        drw.text((12,196), f"DISK {disk:4.0f}%", font=F18, fill=FG);  draw_bar(drw, 12,216, W-24, 14, disk)

        drw.text((12, 244), f"UP {up_kbs:5.0f} KB/s  DN {dn_kbs:5.0f} KB/s", font=F14, fill=ACCENT)

        # EKRANA BAS
        disp.ShowImage(img)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            LCD_1inch69.module_exit()  # bazı örneklerde global metod var
        except Exception:
            pass
