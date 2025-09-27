#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
from PIL import Image, ImageDraw
from lib.LCD_1inch69 import LCD_1inch69

try:
    from smbus2 import SMBus
except ImportError:
    print("smbus2 yok: sudo apt install python3-smbus python3-pip && pip3 install smbus2")
    exit(1)

# Dokunmatik ayarları
CST816_ADDR = 0x15
I2C_BUS = 1

class Touch:
    def __init__(self):
        self.bus = SMBus(I2C_BUS)

    def read_point(self, W, H):
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 7)
            event = d[1] & 0x0F
            if event == 0:
                return None
            x = ((d[2]&0x0F)<<8) | d[3]
            y = ((d[4]&0x0F)<<8) | d[5]
            return (max(0, min(W-1, x)), max(0, min(H-1, y)))
        except Exception:
            return None

def main():
    # LCD başlat
    lcd = LCD_1inch69()
    lcd.Init()
    try: lcd.bl_DutyCycle(100)
    except: pass
    W, H = lcd.width, lcd.height

    touch = Touch()

    while True:
        img = Image.new("RGB", (W, H), (0,0,0))  # siyah arka plan
        draw = ImageDraw.Draw(img)

        pt = touch.read_point(W,H)
        if pt:
            x,y = pt
            draw.ellipse((x-5,y-5,x+5,y+5), fill=(255,0,0))  # kırmızı nokta
            draw.text((5,5), f"X={x} Y={y}", fill=(0,255,0))

        lcd.ShowImage(img)
        time.sleep(0.05)

if __name__ == "__main__":
    main()
