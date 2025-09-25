#!/usr/bin/env python3
import time
from PIL import Image, ImageDraw, ImageFont
import st7789  # küçük harf

# KART PİN VARSAYIMI (Waveshare 1.69"):
PORT=0; CS=0; DC=25; RST=27
# Backlight bazı HAT'larda 24 ama bazılarında sabit 3.3V. İkisini de deneriz.
BACKLIGHTS = [24, None]

WIDTH, HEIGHT = 240, 280

def screen(color, txt, disp):
    img = Image.new("RGB", (WIDTH, HEIGHT), color)
    drw = ImageDraw.Draw(img)
    try:
        fnt = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        fnt = None
    drw.text((10, 10), txt, fill=(255,255,255), font=fnt)
    disp.display(img)

cands = [
    # (rotation, offset_left, offset_top)
    (0,   0, 0),
    (0,   0, 20),
    (0,   0, 40),
    (90,  0, 0),
    (90, 20, 0),
    (270, 0, 0),
    (180, 0, 0),
]

for bl in BACKLIGHTS:
    for rot, offl, offt in cands:
        try:
            disp = st7789.ST7789(
                width=WIDTH, height=HEIGHT,
                rotation=rot, port=PORT, cs=CS, dc=DC, rst=RST,
                backlight=bl, spi_speed_hz=80_000_000,
                offset_left=offl, offset_top=offt
            )
            disp.begin()
            screen((255,0,0), f"rot={rot} off=({offl},{offt}) BL={bl}", disp); time.sleep(1.0)
            screen((0,255,0), f"rot={rot} off=({offl},{offt}) BL={bl}", disp); time.sleep(1.0)
            screen((0,0,255), f"rot={rot} off=({offl},{offt}) BL={bl}", disp); time.sleep(1.0)
            screen((0,0,0),   f"OK rot={rot} off=({offl},{offt}) BL={bl}", disp); time.sleep(1.0)
            disp.reset()
        except Exception as e:
            # geçersiz kombinasyonlar sessizce atlanır
            pass

print("Bitti. Hiç renk görmediysen SPI/pin/BL yanlıştır.")
