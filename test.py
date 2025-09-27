#!/usr/bin/env python3
# touch_calibrate_draw.py
# 240x280 ST7789 + CST816S için basit kalibrasyon ve nokta çizimi
import time, math
from smbus2 import SMBus
from PIL import Image, ImageDraw
import st7789

# --- EKRAN PARAMETRELERİ ---
WIDTH, HEIGHT = 240, 280
# Donanımsal pinler (Waveshare defaultlarına yakın; gerekirse değiştir)
DC_PIN = 25
RST_PIN = 27
BL_PIN  = 13
PORT = 0
CS = 0
ROTATION = 0      # 0/90/180/270 dene
OFFSET_LEFT = 0
OFFSET_TOP  = 0

# --- DOKUNMATİK/I2C ---
I2C_BUS = 1
ADDR = 0x15

# --- BAŞLANGIÇ KALİBRASYON ---
# Çoğu CST816S'te 0..4095 aralığına yakın ham değer gelir ama bazen daralır/kayar.
# Ön ayarları mantıklı başlatıyoruz; kalibrasyon bunları düzeltecek.
SWAP_XY  = True
INVERT_X = True
INVERT_Y = False
RAW_MIN_X, RAW_MAX_X = 0, 3840
RAW_MIN_Y, RAW_MAX_Y = 0, 3840

# --- EKRAN KURULUMU ---
disp = st7789.ST7789(
    rotation=0,   # 90 yerine 0 veya 180 dene
    port=0,
    cs=0,
    dc=25,
    backlight=13,
    rst=27,
    width=240,
    height=280,
    offset_left=0,
    offset_top=0
)
img = Image.new("RGB", (240, 280), (0,0,0))
draw = ImageDraw.Draw(img)

draw.text((10, 10), "Hello Pi5", fill=(255,255,255))
disp.display(img)

def cls(c=(0,0,0)):
    draw.rectangle((0,0,WIDTH,HEIGHT), fill=c)

def text(x,y,t, c=(255,255,255)):
    draw.text((x,y), t, fill=c)

def show():
    disp.display(img)

# --- TOUCH OKUMA ---
bus = SMBus(I2C_BUS)
def read_touch():
    data = bus.read_i2c_block_data(ADDR, 0x01, 7)
    fingers = data[1] & 0x0F
    if fingers == 0:
        return None
    rx = ((data[2] & 0x0F) << 8) | data[3]
    ry = ((data[4] & 0x0F) << 8) | data[5]
    return rx, ry

def map_coord(rx, ry):
    x_raw, y_raw = (ry, rx) if SWAP_XY else (rx, ry)
    # normalize 0..1
    nx = (x_raw - RAW_MIN_X) / max(1, (RAW_MAX_X - RAW_MIN_X))
    ny = (y_raw - RAW_MIN_Y) / max(1, (RAW_MAX_Y - RAW_MIN_Y))
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    if INVERT_X: nx = 1.0 - nx
    if INVERT_Y: ny = 1.0 - ny
    sx = int(nx * (WIDTH - 1))
    sy = int(ny * (HEIGHT - 1))
    return sx, sy

def draw_cross(x,y,col=(0,255,0)):
    # küçük artı işareti
    r = 6
    draw.line((x-r,y, x+r,y), fill=col, width=2)
    draw.line((x,y-r, x,y+r), fill=col, width=2)

def calibrate_minmax():
    # Kullanıcıdan 4 kenara yakın dokunuş alıp min/max belirler
    points = []
    cls((0,0,30)); text(10,10,"Kalibrasyon: 4 noktaya dokun", (200,200,200)); show()
    targets = [(20,20),(WIDTH-20,20),(WIDTH-20,HEIGHT-20),(20,HEIGHT-20)]
    for i,(tx,ty) in enumerate(targets):
        cls((0,0,30))
        text(10,10,f"Nokta {i+1}/4 - hedefe dokun", (200,200,200))
        draw_cross(tx,ty,(255,200,0))
        show()
        # dokunma bekle
        while True:
            t = read_touch()
            if t:
                points.append(t)
                time.sleep(0.5) # bırakmasını bekle
                break
            time.sleep(0.01)

    rx_vals = [p[0] for p in points]
    ry_vals = [p[1] for p in points]
    # Biraz buffer ekleyelim
    k = 40
    global RAW_MIN_X, RAW_MAX_X, RAW_MIN_Y, RAW_MAX_Y
    RAW_MIN_X = max(0, min(rx_vals) - k)
    RAW_MAX_X = max(rx_vals) + k
    RAW_MIN_Y = max(0, min(ry_vals) - k)
    RAW_MAX_Y = max(ry_vals) + k

def main():
    calibrate_minmax()
    cls((10,10,10))
    text(10,10,"Dokunma test: noktalar yesil", (200,200,200))
    text(10,26,f"SWAP_XY={SWAP_XY} IX={INVERT_X} IY={INVERT_Y}", (150,150,150))
    text(10,42,f"RAWX[{RAW_MIN_X},{RAW_MAX_X}] RAWY[{RAW_MIN_Y},{RAW_MAX_Y}]", (150,150,150))
    show()
    while True:
        t = read_touch()
        if t:
            rx,ry = t
            x,y = map_coord(rx,ry)
            draw_cross(x,y,(0,255,0))
            # küçük iz bırak
            draw.ellipse((x-1,y-1,x+1,y+1), fill=(0,255,0))
            show()
        else:
            time.sleep(0.01)

if __name__ == "__main__":
    main()
