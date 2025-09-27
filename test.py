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

# ---- Donanım ----
CST816_ADDR = 0x15
I2C_BUS = 1  # gerekirse 13/14

# Senin panel bayrakların:
SWAP_XY  = True
INVERT_X = True
INVERT_Y = False

# Başlangıçta kaba tahmin; otomatik öğrenme bunları güncelleyecek:
RAW_MIN_X = 0
RAW_MAX_X = 3840
RAW_MIN_Y = 0
RAW_MAX_Y = 3840

# ---- Font ----
def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        try:
            from PIL import ImageFont
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    from PIL import ImageFont
    return ImageFont.load_default()
F12 = load_font(12)

class Touch:
    def __init__(self, bus_id=I2C_BUS, addr=CST816_ADDR):
        self.bus = SMBus(bus_id)
        self.addr = addr
        # basit okuma testi
        self.bus.read_i2c_block_data(self.addr, 0x00, 1)

    def read_raw(self):
        # Yöntem A: 0x00'dan 7 byte
        try:
            d = self.bus.read_i2c_block_data(self.addr, 0x00, 7)
            if (d[1] & 0x0F) != 0:
                x = ((d[2] & 0x0F) << 8) | d[3]
                y = ((d[4] & 0x0F) << 8) | d[5]
                return (x, y)
        except Exception:
            pass
        # Yöntem B: 0x01'den 6 byte (bazı paneller)
        try:
            b = self.bus.read_i2c_block_data(self.addr, 0x01, 6)
            if (b[0] & 0x0F) != 0:
                x = ((b[1] & 0x0F) << 8) | b[2]
                y = ((b[3] & 0x0F) << 8) | b[4]
                return (x, y)
        except Exception:
            pass
        return None

# ---- Otomatik öğrenme ----
# İlk birkaç saniye boyunca gözlenen min/max’ı toplayıp aralığı açıyoruz.
OBS_MIN_X = None
OBS_MAX_X = None
OBS_MIN_Y = None
OBS_MAX_Y = None

LEARN_FRAMES = 240      # ~8 sn @30fps
frames = 0
locked = False

def update_observed(rx, ry):
    global OBS_MIN_X, OBS_MAX_X, OBS_MIN_Y, OBS_MAX_Y
    if OBS_MIN_X is None or rx < OBS_MIN_X: OBS_MIN_X = rx
    if OBS_MAX_X is None or rx > OBS_MAX_X: OBS_MAX_X = rx
    if OBS_MIN_Y is None or ry < OBS_MIN_Y: OBS_MIN_Y = ry
    if OBS_MAX_Y is None or ry > OBS_MAX_Y: OBS_MAX_Y = ry

def scale_map(rawx, rawy, W, H):
    # SWAP mantığı
    if SWAP_XY:
        rx, ry = rawy, rawx   # rx -> ekran X’in ham kaynağı, ry -> ekran Y’nin ham kaynağı
    else:
        rx, ry = rawx, rawy

    # Öğrenme kilitliyse sabit aralık, değilse gözlenen aralık
    xmin = OBS_MIN_X if (locked and OBS_MIN_X is not None) else RAW_MIN_X
    xmax = OBS_MAX_X if (locked and OBS_MAX_X is not None) else RAW_MAX_X
    ymin = OBS_MIN_Y if (locked and OBS_MIN_Y is not None) else RAW_MIN_Y
    ymax = OBS_MAX_Y if (locked and OBS_MAX_Y is not None) else RAW_MAX_Y

    if xmax <= xmin: xmax = xmin + 1
    if ymax <= ymin: ymax = ymin + 1

    # Kırp ve normalize
    rx = min(max(rx, xmin), xmax)
    ry = min(max(ry, ymin), ymax)

    nx = (rx - xmin) / (xmax - xmin)
    ny = (ry - ymin) / (ymax - ymin)

    if INVERT_X: nx = 1 - nx
    if INVERT_Y: ny = 1 - ny

    x = int(nx * (W - 1))
    y = int(ny * (H - 1))
    return x, y

def main():
    global frames, locked
    lcd = LCD_1inch69()
    lcd.Init()
    try:
        lcd.bl_DutyCycle(100)
    except Exception:
        pass
    W, H = lcd.width, lcd.height

    touch = Touch()

    # “iz bırakan” siyah arka plan
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)

    t0 = time.time()
    while True:
        raw = touch.read_raw()
        if raw:
            rx, ry = raw
            # Öğrenme modunda isek gözlenen aralıkları genişlet
            if not locked:
                update_observed(rx, ry)
                frames += 1
                # Kullanıcıdan beklenti: ilk 5–8 saniye boyunca ekranın tüm köşelerine sür
                if frames >= LEARN_FRAMES:
                    locked = True
                    # Kilitlendiğinde ekrana önerilen sabitleri yaz
                    d.rectangle((0, 0, W, 48), fill=(0, 0, 0))
                    d.text(
                        (4, 4),
                        f"LOCKED. Use these constants:\n"
                        f"RAW_MIN_X={OBS_MIN_X} RAW_MAX_X={OBS_MAX_X}  "
                        f"RAW_MIN_Y={OBS_MIN_Y} RAW_MAX_Y={OBS_MAX_Y}",
                        font=F12,
                        fill=(180, 220, 255)
                    )

            x, y = scale_map(rx, ry, W, H)

            # Nokta + artı
            d.ellipse((x-3, y-3, x+3, y+3), fill=(255, 60, 60))
            d.line((x-6, y, x+6, y), fill=(255, 60, 60))
            d.line((x, y-6, x, y+6), fill=(255, 60, 60))

            # Üst bant: debug bilgisi
            d.rectangle((0, 0, W, 16), fill=(0, 0, 0))
            d.text(
                (4, 2),
                f"RAW=({rx},{ry})  OBS_X=[{OBS_MIN_X},{OBS_MAX_X}]  OBS_Y=[{OBS_MIN_Y},{OBS_MAX_Y}]  {'LOCK' if locked else 'LEARN'}",
                font=F12,
                fill=(180, 220, 255)
            )

            lcd.ShowImage(img)

        # Öğrenme süresince kullanıcıya hatırlatma
        if not locked and (time.time() - t0) % 1.0 < 0.02:
            d.rectangle((0, H-18, W, H), fill=(0, 0, 0))
            d.text((4, H-16), "Tüm köşelere sür (öğreniyor)...", font=F12, fill=(180, 255, 180))
            lcd.ShowImage(img)

        time.sleep(0.01)

if __name__ == "__main__":
    main()
