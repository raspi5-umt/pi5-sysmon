#!/usr/bin/env python3
# touch_calibrate_draw.py — 240x280 ST7789 + CST816S kalibrasyon ve çizim
import time
from smbus2 import SMBus
from PIL import Image, ImageDraw, ImageFont
import st7789

# --- EKRAN ---
WIDTH, HEIGHT = 240, 280
ROTATION = 0           # 0 ile başla. 180 veya 270 deneyebilirsin. 90 BU PANELDE YOK.
DC_PIN = 25
RST_PIN = 27
BL_PIN  = 13
PORT, CS = 0, 0
OFFSET_LEFT, OFFSET_TOP = 0, 0

# --- DOKUNMATİK ---
I2C_BUS = 1
ADDR = 0x15  # CST816S

# --- KALIBRASYON/AKS ---
SWAP_XY  = True   # birçoğunda gerekli
INVERT_X = True
INVERT_Y = False

# Başlangıç aralıkları: kalibrasyon bunları düzeltecek
RAW_MIN_X, RAW_MAX_X = 0, 3840
RAW_MIN_Y, RAW_MAX_Y = 0, 3840

# --- EKRAN NESNESİ (init otomatik; init() çağırma) ---
disp = st7789.ST7789(
    rotation=ROTATION,
    port=PORT, cs=CS, dc=DC_PIN, backlight=BL_PIN, rst=RST_PIN,
    width=WIDTH, height=HEIGHT, offset_left=OFFSET_LEFT, offset_top=OFFSET_TOP
)

img = Image.new("RGB", (WIDTH, HEIGHT), (0,0,0))
draw = ImageDraw.Draw(img)

def cls(c=(0,0,0)):
    draw.rectangle((0,0,WIDTH,HEIGHT), fill=c)

def text(x,y,t, c=(255,255,255)):
    draw.text((x,y), t, fill=c)

def show():
    disp.display(img)

# --- TOUCH ---
bus = SMBus(I2C_BUS)

def read_touch():
    try:
        data = bus.read_i2c_block_data(ADDR, 0x01, 7)
    except OSError as e:
        # 121 = Remote I/O error => 0x15 yok/IRQ-RST yanlış
        return None
    fingers = data[1] & 0x0F
    if fingers == 0:
        return None
    rx = ((data[2] & 0x0F) << 8) | data[3]
    ry = ((data[4] & 0x0F) << 8) | data[5]
    return rx, ry

def map_coord(rx, ry):
    # eksen/çevirmeler
    x_raw, y_raw = (ry, rx) if SWAP_XY else (rx, ry)
    # normalize 0..1
    nx = (x_raw - RAW_MIN_X) / max(1, (RAW_MAX_X - RAW_MIN_X))
    ny = (y_raw - RAW_MIN_Y) / max(1, (RAW_MAX_Y - RAW_MIN_Y))
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else nx
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else ny
    if INVERT_X: nx = 1.0 - nx
    if INVERT_Y: ny = 1.0 - ny
    sx = int(nx * (WIDTH - 1))
    sy = int(ny * (HEIGHT - 1))
    return sx, sy

def draw_cross(x,y,col=(0,255,0)):
    r = 6
    draw.line((x-r,y, x+r,y), fill=col, width=2)
    draw.line((x,y-r, x,y+r), fill=col, width=2)

def calibrate_minmax():
    global RAW_MIN_X, RAW_MAX_X, RAW_MIN_Y, RAW_MAX_Y
    targets = [(20,20),(WIDTH-20,20),(WIDTH-20,HEIGHT-20),(20,HEIGHT-20)]
    raw_points = []

    for i,(tx,ty) in enumerate(targets, 1):
        cls((0,0,25))
        text(8,8,f"Kalibrasyon {i}/4: hedefe dokun", (200,200,200))
        draw_cross(tx,ty,(255,200,0))
        show()
        # bekle ve tek dokunuş yakala
        while True:
            t = read_touch()
            if t:
                raw_points.append(t)
                time.sleep(0.4)  # parmağı çekmesi için küçük gecikme
                break
            time.sleep(0.01)

    rx = [p[0] for p in raw_points]
    ry = [p[1] for p in raw_points]
    pad = 40
    RAW_MIN_X = max(0, min(rx) - pad)
    RAW_MAX_X = max(rx) + pad
    RAW_MIN_Y = max(0, min(ry) - pad)
    RAW_MAX_Y = max(ry) + pad

def main():
    # hoşgeldin
    cls((0,0,0)); text(8,8,"Waveshare 1.69\" Dokunma Test", (180,180,180)); show()
    time.sleep(0.6)

    # 4-nokta kalibrasyon
    calibrate_minmax()

    # Bilgi overlay
    cls((10,10,10))
    text(8,8,"Dokunma Test: yesil iz bırakır", (200,200,200))
    text(8,24,f"SWAP_XY={SWAP_XY}  IX={INVERT_X}  IY={INVERT_Y}", (150,150,150))
    text(8,40,f"RAWX[{RAW_MIN_X},{RAW_MAX_X}] RAWY[{RAW_MIN_Y},{RAW_MAX_Y}]", (150,150,150))
    show()

    # iz çiz
    while True:
        t = read_touch()
        if t:
            rx,ry = t
            x,y = map_coord(rx,ry)
            draw_cross(x,y,(0,255,0))
            show()
        else:
            time.sleep(0.01)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
