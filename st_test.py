#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, os, sys
from PIL import Image, ImageDraw, ImageFont

# ---- Font ----
def font(sz=20):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"]:
        if os.path.exists(p): 
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F = font(22)

def show_img(display, img, backend):
    if backend == "waveshare":
        display.ShowImage(img)
    else:
        display.display(img)

def solid(display, W, H, color, text, backend):
    img = Image.new("RGB", (W, H), color)
    d = ImageDraw.Draw(img)
    d.text((10, 10), text, fill=(255,255,255), font=F)
    show_img(display, img, backend)

def try_waveshare():
    # Üretici sürücüsünü dene
    try:
        # lib yolunu ekle (çoğu repo'da lib/LCD_1inch69.py)
        for cand in ["./lib", "./python/lib", ".", "/usr/local/lib/python3.11/dist-packages"]:
            if cand not in sys.path and os.path.isdir(cand):
                sys.path.insert(0, cand)
        from LCD_1inch69 import LCD_1inch69
        lcd = LCD_1inch69()
        lcd.Init()
        try:
            lcd.bl_DutyCycle(100)
        except Exception:
            pass
        W, H = lcd.width, lcd.height
        for col, name in [((255,0,0),"HELLO (waveshare) R"),
                          ((0,255,0),"HELLO (waveshare) G"),
                          ((0,0,255),"HELLO (waveshare) B")]:
            solid(lcd, W, H, col, name, "waveshare")
            time.sleep(0.8)
        return lcd, "waveshare", W, H
    except Exception:
        return None, None, None, None

def try_st7789():
    # Yaygın kombinasyonları dene
    import st7789
    combos = [
        dict(width=240, height=280, rotation=0,   offset_left=0,  offset_top=0),
        dict(width=240, height=280, rotation=0,   offset_left=0,  offset_top=20),
        dict(width=240, height=280, rotation=90,  offset_left=0,  offset_top=0),
        dict(width=240, height=280, rotation=90,  offset_left=20, offset_top=0),
        dict(width=240, height=280, rotation=270, offset_left=0,  offset_top=0),
        dict(width=240, height=280, rotation=180, offset_left=0,  offset_top=0),
    ]
    pins = dict(port=0, cs=0, dc=25, rst=27, backlight=None, spi_speed_hz=80_000_000)

    last_ok = None
    for cfg in combos:
        try:
            disp = st7789.ST7789(**cfg, **pins)
            disp.begin()
            W, H = cfg["width"], cfg["height"]
            for col, name in [((255,0,0), f"HELLO rot={cfg['rotation']}"),
                              ((0,255,0), f"off=({cfg['offset_left']},{cfg['offset_top']})"),
                              ((0,0,255), "st7789 OK")]:
                solid(disp, W, H, col, name, "st7789")
                time.sleep(0.6)
            last_ok = (disp, "st7789", W, H)
            break
        except Exception:
            continue
    if last_ok:
        return last_ok
    # CE1 kullanan kartlar için cs=1'i de dene
    pins_cs1 = dict(pins); pins_cs1["cs"] = 1
    for cfg in combos:
        try:
            disp = st7789.ST7789(**cfg, **pins_cs1)
            disp.begin()
            W, H = cfg["width"], cfg["height"]
            for col, name in [((255,0,0), f"HELLO rot={cfg['rotation']} cs=1"),
                              ((0,255,0), f"off=({cfg['offset_left']},{cfg['offset_top']})"),
                              ((0,0,255), "st7789 OK")]:
                solid(disp, W, H, col, name, "st7789")
                time.sleep(0.6)
            return (disp, "st7789", W, H)
        except Exception:
            continue
    return None, None, None, None

def main():
    # 1) Waveshare sürücüsü varsa onu kullan
    disp, backend, W, H = try_waveshare()
    if disp is None:
        # 2) st7789 fallback
        try:
            disp, backend, W, H = try_st7789()
        except Exception as e:
            print("st7789 denenirken hata:", e)
            disp = None

    if disp is None:
        print("Ekran bulunamadı. SPI açık mı? Sürücüler kurulu mu?")
        sys.exit(1)

    # Ekran çalışıyor; ekranda basit sayaç döndür
    n = 0
    while True:
        img = Image.new("RGB", (W, H), (5,8,12))
        d = ImageDraw.Draw(img)
        d.text((10, 10), f"HELLO • {backend}", fill=(220,220,220), font=F)
        d.text((10, 40), time.strftime("%H:%M:%S"), fill=(120,180,255), font=F)
        d.text((10, 70), f"COUNT: {n}", fill=(255,170,0), font=F)
        show_img(disp, img, backend)
        n += 1
        time.sleep(0.5)

if __name__ == "__main__":
    main()
