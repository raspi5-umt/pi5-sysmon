#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
from PIL import Image, ImageDraw, ImageFont
from lib.LCD_1inch69 import LCD_1inch69

try:
    from smbus2 import SMBus
except ImportError:
    print("smbus2 yok. Kur: sudo apt update && sudo apt install -y python3-smbus python3-pip && pip3 install smbus2")
    raise SystemExit(1)

# ---- Dokunmatik ayarları ----
CST816_ADDR = 0x15
I2C_BUS = 1  # gerekirse 13/14 deneyebilirsin

# ---- Ekran yön düzeltmeleri ----
SWAP_XY  = False   # True yap ve tekrar dene: bazı panellerde X/Y ters gelir
INVERT_X = False   # True yap ve tekrar dene: X ekseni ters ise
INVERT_Y = False   # True yap ve tekrar dene: Y ekseni ters ise

# CST816S çoğunlukla 12-bit (0..4095) ham koordinat verir
RAW_MAX_X = 4095
RAW_MAX_Y = 4095

# ---- Font (isteğe bağlı) ----
def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    return ImageFont.load_default()
F12 = load_font(12)

class Touch:
    def __init__(self, bus_id=I2C_BUS, addr=CST816_ADDR):
        self.bus = SMBus(bus_id)
        self.addr = addr
        # basit okuma testi
        try:
            self.bus.read_i2c_block_data(self.addr, 0x00, 1)
        except Exception as e:
            raise SystemExit(f"I2C okuma hatası: {e}")

    def read_raw(self):
        """Ham 12-bit X/Y döndür. Yoksa None."""
        try:
            d = self.bus.read_i2c_block_data(self.addr, 0x00, 7)
            # d[1] event: 0 = no touch
            if (d[1] & 0x0F) == 0:
                return None
            x_raw = ((d[2] & 0x0F) << 8) | d[3]
            y_raw = ((d[4] & 0x0F) << 8) | d[5]
            return (x_raw, y_raw)
        except Exception:
            return None

def scale_map(x_raw, y_raw, W, H):
    # Ham değerleri önce gerekirse swap et
    if SWAP_XY:
        x_raw, y_raw = y_raw, x_raw

    # 0..RAW_MAX aralığını ekrana ölçekle
    x = int(x_raw * (W - 1) / max(1, RAW_MAX_X))
    y = int(y_raw * (H - 1) / max(1, RAW_MAX_Y))

    # İnvert gerekiyorsa uygula
    if INVERT_X:
        x = (W - 1) - x
    if INVERT_Y:
        y = (H - 1) - y

    # Ekran sınırlarına kırp
    x = max(0, min(W - 1, x))
    y = max(0, min(H - 1, y))
    return x, y

def main():
    # LCD
    lcd = LCD_1inch69()
    lcd.Init()
    try:
        lcd.bl_DutyCycle(100)
    except Exception:
        pass
    W, H = lcd.width, lcd.height

    # Touch
    touch = Touch()

    # Arka planı her framede temizlemeyelim; iz bırakan mod daha iyi test ettirir.
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Bilgi bandı
    info_bg = (20, 20, 20)
    info_fg = (180, 255, 180)

    while True:
        # üst şeridi temizle
        draw.rectangle((0, 0, W, 22), fill=info_bg)

        raw = touch.read_raw()
        if raw:
            rx, ry = raw
            x, y = scale_map(rx, ry, W, H)

            # Ham ve ölçekli değerleri yaz
            draw.text((4, 4), f"RAW {rx:4d},{ry:4d}  SCALED {x:3d},{y:3d}", font=F12, fill=info_fg)

            # Nokta ve hedef çizgileri
            draw.ellipse((x-4, y-4, x+4, y+4), fill=(255, 60, 60))
            draw.line((x-8, y, x+8, y), fill=(255, 60, 60))
            draw.line((x, y-8, x, y+8), fill=(255, 60, 60))
        else:
            draw.text((4, 4), "dokunma yok", font=F12, fill=(200, 200, 200))

        lcd.ShowImage(img)
        time.sleep(0.03)

if __name__ == "__main__":
    main()
