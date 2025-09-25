#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time
from PIL import Image, ImageDraw, ImageFont

# 1) lib yolunu ekle (klasik waveshare dizinleri)
for cand in ["./lib", "./python/lib", "/home/pi/lib", "/home/pi/python/lib", "."]:
    if os.path.isdir(cand) and cand not in sys.path:
        sys.path.insert(0, cand)

try:
    from LCD_1inch69 import LCD_1inch69
except Exception as e:
    print("LCD_1inch69 modülü bulunamadı. Üreticinin lib klasörüne bu scripti koy ya da yolu düzelt.")
    sys.exit(1)

def load_font(sz=22):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"]:
        if os.path.exists(p):
            from PIL import ImageFont
            return ImageFont.truetype(p, sz)
    from PIL import ImageFont
    return ImageFont.load_default()

def main():
    lcd = LCD_1inch69()
    lcd.Init()             # üreticinin init'i
    try:
        lcd.bl_DutyCycle(100)  # bazı sürümlerde var, yoksa problem değil
    except Exception:
        pass

    W, H = lcd.width, lcd.height
    F = load_font(22)

    # ekranı kesin konuştur: kırmızı-yeşil-mavi
    for color, text in [((255,0,0), "HELLO R"),
                        ((0,255,0), "HELLO G"),
                        ((0,0,255), "HELLO B")]:
        img = Image.new("RGB", (W, H), color)
        d = ImageDraw.Draw(img)
        d.text((10, 10), text, fill=(255,255,255), font=F)
        lcd.ShowImage(img)
        time.sleep(0.8)

    # saat/sayaç döngüsü
    n = 0
    while True:
        img = Image.new("RGB", (W, H), (5,8,12))
        d = ImageDraw.Draw(img)
        d.text((10, 10), "HELLO (vendor)", fill=(220,220,220), font=F)
        d.text((10, 40), time.strftime("%H:%M:%S"), fill=(120,180,255), font=F)
        d.text((10, 70), f"COUNT: {n}", fill=(255,170,0), font=F)
        lcd.ShowImage(img)
        n += 1
        time.sleep(0.5)

if __name__ == "__main__":
    main()
