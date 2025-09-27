#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, statistics
from PIL import Image, ImageDraw, ImageFont
from lib.LCD_1inch69 import LCD_1inch69

try:
    from smbus2 import SMBus
except ImportError:
    print("smbus2 yok. Kur: sudo apt update && sudo apt install -y python3-smbus python3-pip && pip3 install smbus2")
    raise SystemExit(1)

# ---- Touch donanımı ----
CST816_ADDR = 0x15
I2C_BUS = 1  # gerekirse 13/14

# ---- Font ----
def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        try: return ImageFont.truetype(p, sz)
        except: pass
    return ImageFont.load_default()
F12 = load_font(12)
F16 = load_font(16)
F20 = load_font(20)

class Touch:
    def __init__(self, bus_id=I2C_BUS, addr=CST816_ADDR):
        self.bus = SMBus(bus_id)
        self.addr = addr
        try:
            self.bus.read_i2c_block_data(self.addr, 0x00, 1)
        except Exception as e:
            raise SystemExit(f"I2C okuma hatası: {e}")

    def read_raw(self):
        """Ham X/Y (genelde 0..4095) döndür; yoksa None."""
        try:
            d = self.bus.read_i2c_block_data(self.addr, 0x00, 7)
            if (d[1] & 0x0F) == 0:
                return None
            x_raw = ((d[2] & 0x0F) << 8) | d[3]
            y_raw = ((d[4] & 0x0F) << 8) | d[5]
            return (x_raw, y_raw)
        except Exception:
            return None

def draw_ui(draw, W, H, msg_top, msg_mid=None, target=None):
    draw.rectangle((0,0,W,H), fill=(0,0,0))
    draw.text((W//2, 10), msg_top, fill=(200,255,200), font=F16, anchor="ma")
    if msg_mid:
        draw.text((W//2, 34), msg_mid, fill=(200,200,255), font=F12, anchor="ma")
    if target:
        tx, ty = target
        # hedefi göster
        draw.line((tx-8, ty, tx+8, ty), fill=(60,160,255))
        draw.line((tx, ty-8, tx, ty+8), fill=(60,160,255))
        draw.ellipse((tx-3, ty-3, tx+3, ty+3), outline=(60,160,255))

def collect_corner_median(lcd, touch, W, H, name, target):
    """Köşeye 1 sn basılı tut; medyan ham X/Y döndür."""
    img = Image.new("RGB", (W,H), (0,0,0))
    d = ImageDraw.Draw(img)
    draw_ui(d, W, H, f"{name} köşesine dokun ve 1 sn basılı tut", "Bırakınca bir sonraki köşeye geçilecek", target)
    lcd.ShowImage(img)

    samples_x, samples_y = [], []
    t_start = time.time()
    pressed = False

    while True:
        raw = touch.read_raw()
        if raw:
            rx, ry = raw
            samples_x.append(rx); samples_y.append(ry)
            # kırmızı nokta göster
            d.ellipse((target[0]-2, target[1]-2, target[0]+2, target[1]+2), fill=(255,60,60))
            lcd.ShowImage(img)
            if not pressed:
                pressed = True
                t_start = time.time()
        else:
            # bırakıldı; eğer en az 0.7 sn veri aldıysak kabul
            if pressed and (time.time() - t_start) > 0.7 and len(samples_x) > 8:
                break

        time.sleep(0.01)

    mx = int(statistics.median(samples_x))
    my = int(statistics.median(samples_y))
    return mx, my

def compute_mapping(p_ul, p_ur, p_ll, p_lr):
    """SWAP/INVERT ve min/max çıkar."""
    x_ul, y_ul = p_ul
    x_ur, y_ur = p_ur
    x_ll, y_ll = p_ll
    x_lr, y_lr = p_lr

    # Önce sağ–sol hareketinde hangi ham eksen daha çok değişiyor?
    dx_lr = abs(x_ur - x_ul)
    dy_lr = abs(y_ur - y_ul)
    swap_xy = dy_lr > dx_lr  # sağa giderken y daha çok değişiyorsa eksenler swap

    if not swap_xy:
        # X: sağa artıyor mu?
        invert_x = (x_ur < x_ul)
        # Y: aşağı artıyor mu? (UL -> LL)
        invert_y = (y_ll < y_ul)
        raw_min_x = min(x_ul, x_ur, x_ll, x_lr)
        raw_max_x = max(x_ul, x_ur, x_ll, x_lr)
        raw_min_y = min(y_ul, y_ur, y_ll, y_lr)
        raw_max_y = max(y_ul, y_ur, y_ll, y_lr)
    else:
        # X ekranı ham Y ile, Y ekranı ham X ile
        # sağa: Y artıyor mu?
        invert_x = (y_ur < y_ul)
        # aşağı: X artıyor mu? (UL -> LL)
        invert_y = (x_ll < x_ul)
        raw_min_x = min(y_ul, y_ur, y_ll, y_lr)
        raw_max_x = max(y_ul, y_ur, y_ll, y_lr)
        raw_min_y = min(x_ul, x_ur, x_ll, x_lr)
        raw_max_y = max(x_ul, x_ur, x_ll, x_lr)

    return {
        "SWAP_XY": swap_xy,
        "INVERT_X": invert_x,
        "INVERT_Y": invert_y,
        "RAW_MIN_X": raw_min_x,
        "RAW_MAX_X": raw_max_x,
        "RAW_MIN_Y": raw_min_y,
        "RAW_MAX_Y": raw_max_y,
    }

def scale_map(rawx, rawy, W, H, cfg):
    # Eksen seçimi
    if not cfg["SWAP_XY"]:
        rx, ry = rawx, rawy
    else:
        rx, ry = rawy, rawx

    # Min–max normalize
    rx = max(cfg["RAW_MIN_X"], min(cfg["RAW_MAX_X"], rx))
    ry = max(cfg["RAW_MIN_Y"], min(cfg["RAW_MAX_Y"], ry))

    nx = (rx - cfg["RAW_MIN_X"]) / max(1, (cfg["RAW_MAX_X"] - cfg["RAW_MIN_X"]))
    ny = (ry - cfg["RAW_MIN_Y"]) / max(1, (cfg["RAW_MAX_Y"] - cfg["RAW_MIN_Y"]))

    if cfg["INVERT_X"]:
        nx = 1.0 - nx
    if cfg["INVERT_Y"]:
        ny = 1.0 - ny

    x = int(nx * (W - 1))
    y = int(ny * (H - 1))
    return x, y

def main():
    lcd = LCD_1inch69(); lcd.Init()
    try: lcd.bl_DutyCycle(100)
    except: pass
    W, H = lcd.width, lcd.height

    touch = Touch()

    # Köşe hedefleri (ekran koordinatı)
    targets = {
        "Sol-Üst": (8, 8),
        "Sağ-Üst": (W-9, 8),
        "Sol-Alt": (8, H-9),
        "Sağ-Alt": (W-9, H-9),
    }

    # 4 köşe topla
    img = Image.new("RGB", (W,H), (0,0,0)); d = ImageDraw.Draw(img)
    draw_ui(d, W, H, "KALİBRASYON", "Sırayla köşelere basılı tut", None); lcd.ShowImage(img); time.sleep(0.8)

    p_ul = collect_corner_median(lcd, touch, W, H, "Sol-Üst", targets["Sol-Üst"])
    p_ur = collect_corner_median(lcd, touch, W, H, "Sağ-Üst", targets["Sağ-Üst"])
    p_ll = collect_corner_median(lcd, touch, W, H, "Sol-Alt", targets["Sol-Alt"])
    p_lr = collect_corner_median(lcd, touch, W, H, "Sağ-Alt", targets["Sağ-Alt"])

    cfg = compute_mapping(p_ul, p_ur, p_ll, p_lr)

    # Önerileri göster
    img = Image.new("RGB", (W,H), (0,0,0)); d = ImageDraw.Draw(img)
    msg = [
        "ÖNERİLEN AYARLAR:",
        f"SWAP_XY  = {cfg['SWAP_XY']}",
        f"INVERT_X = {cfg['INVERT_X']}",
        f"INVERT_Y = {cfg['INVERT_Y']}",
        f"RAW_MIN_X = {cfg['RAW_MIN_X']}   RAW_MAX_X = {cfg['RAW_MAX_X']}",
        f"RAW_MIN_Y = {cfg['RAW_MIN_Y']}   RAW_MAX_Y = {cfg['RAW_MAX_Y']}",
        "",
        "Artık test moduna geçiliyor...",
    ]
    y = 18
    for line in msg:
        d.text((10, y), line, fill=(220,220,220), font=F12); y += 16
    lcd.ShowImage(img)

    # Terminale de bas
    print("\n=== KALİBRASYON SONUCU ===")
    for k,v in cfg.items():
        print(f"{k} = {v}")
    print("==========================\n")
    time.sleep(1.2)

    # Canlı test: ekranda dokunduğun yerde kırmızı nokta
    img = Image.new("RGB", (W,H), (0,0,0)); d = ImageDraw.Draw(img)
    d.text((W//2, 8), "CANLI TEST: ekrana dokun", fill=(200,255,200), font=F12, anchor="ma")
    lcd.ShowImage(img)

    while True:
        raw = touch.read_raw()
        if raw:
            rx, ry = raw
            x, y = scale_map(rx, ry, W, H, cfg)
            # çizim
            d.ellipse((x-3,y-3,x+3,y+3), fill=(255,60,60))
            # ince artı
            d.line((x-6,y,x+6,y), fill=(255,60,60))
            d.line((x,y-6,x,y+6), fill=(255,60,60))
            # üstte değerler
            d.rectangle((0,20,W,38), fill=(0,0,0))
            d.text((6,22), f"RAW=({rx},{ry})  SCALED=({x},{y})", fill=(180,220,255), font=F12)
            lcd.ShowImage(img)
        time.sleep(0.01)

if __name__ == "__main__":
    main()
