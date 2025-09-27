#!/usr/bin/env python3
# Minimal 240x280 ST7789 + CST816S touch test (ASCII-only)
import time
from smbus2 import SMBus
from PIL import Image, ImageDraw
import st7789

# -------- DISPLAY (Waveshare 1.69", ST7789) --------
WIDTH, HEIGHT = 240, 280
ROTATION = 0          # try 0, then 180 or 270. (90 is NOT supported for this combo)
DC_PIN  = 25
RST_PIN = 27
BL_PIN  = 13
PORT, CS = 0, 0
OFFSET_LEFT, OFFSET_TOP = 0, 0

disp = st7789.ST7789(
    rotation=ROTATION,
    port=PORT, cs=CS, dc=DC_PIN, backlight=BL_PIN, rst=RST_PIN,
    width=WIDTH, height=HEIGHT, offset_left=OFFSET_LEFT, offset_top=OFFSET_TOP
)

img  = Image.new("RGB", (WIDTH, HEIGHT), (0,0,0))
draw = ImageDraw.Draw(img)

def cls(c=(0,0,0)):
    draw.rectangle((0,0,WIDTH,HEIGHT), fill=c)

def text(x, y, s, col=(255,255,255)):
    # ASCII only to avoid font/encoding issues
    draw.text((x, y), s, fill=col)

def show():
    disp.display(img)

# -------- TOUCH (CST816S @ 0x15) --------
I2C_BUS = 1
ADDR    = 0x15
bus     = SMBus(I2C_BUS)

# Axis options (adjust if needed)
SWAP_XY  = True
INVERT_X = True
INVERT_Y = False

# Raw ranges (updated by calibration)
RAW_MIN_X, RAW_MAX_X = 0, 3840
RAW_MIN_Y, RAW_MAX_Y = 0, 3840

def read_touch():
    """Return (raw_x, raw_y) or None."""
    try:
        data = bus.read_i2c_block_data(ADDR, 0x01, 7)
    except OSError as e:
        # 121 Remote I/O -> wiring/IRQ/RST problem
        return None
    fingers = data[1] & 0x0F
    if fingers == 0:
        return None
    rx = ((data[2] & 0x0F) << 8) | data[3]
    ry = ((data[4] & 0x0F) << 8) | data[5]
    return rx, ry

def map_coord(rx, ry):
    """Map raw to screen coordinates with swap/invert and clamping."""
    x_raw, y_raw = (ry, rx) if SWAP_XY else (rx, ry)
    # normalize 0..1
    nx = (x_raw - RAW_MIN_X) / max(1, (RAW_MAX_X - RAW_MIN_X))
    ny = (y_raw - RAW_MIN_Y) / max(1, (RAW_MAX_Y - RAW_MIN_Y))
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else nx
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else ny
    if INVERT_X: nx = 1.0 - nx
    if INVERT_Y: ny = 1.0 - ny
    sx = int(nx * (WIDTH  - 1))
    sy = int(ny * (HEIGHT - 1))
    return sx, sy

def cross(x, y, col=(0,255,0)):
    r = 6
    draw.line((x-r, y, x+r, y), fill=col, width=2)
    draw.line((x, y-r, x, y+r), fill=col, width=2)

def calibrate_minmax():
    """Collect 4 touches near corners to set raw min/max."""
    global RAW_MIN_X, RAW_MAX_X, RAW_MIN_Y, RAW_MAX_Y
    targets = [(20,20), (WIDTH-20,20), (WIDTH-20,HEIGHT-20), (20,HEIGHT-20)]
    raw_pts = []

    for i, (tx, ty) in enumerate(targets, 1):
        cls((0,0,25))
        text(8,8, f"Calibrate {i}/4: touch the target", (200,200,200))
        cross(tx, ty, (255,200,0))
        show()
        # wait single touch
        while True:
            t = read_touch()
            if t:
                raw_pts.append(t)
                time.sleep(0.4)  # let finger go
                break
            time.sleep(0.01)

    rxs = [p[0] for p in raw_pts]
    rys = [p[1] for p in raw_pts]
    pad = 40
    RAW_MIN_X = max(0, min(rxs) - pad)
    RAW_MAX_X = max(rxs) + pad
    RAW_MIN_Y = max(0, min(rys) - pad)
    RAW_MAX_Y = max(rys) + pad

def main():
    cls(); text(8,8,"Waveshare 1.69 Touch Test", (180,180,180)); show(); time.sleep(0.5)
    calibrate_minmax()

    cls((10,10,10))
    text(8,8, "Draw: green marks follow finger", (200,200,200))
    text(8,24, f"SWAP={SWAP_XY} IX={INVERT_X} IY={INVERT_Y}", (150,150,150))
    text(8,40, f"RX[{RAW_MIN_X},{RAW_MAX_X}] RY[{RAW_MIN_Y},{RAW_MAX_Y}]", (150,150,150))
    show()

    while True:
        t = read_touch()
        if t:
            rx, ry = t
            x, y = map_coord(rx, ry)
            cross(x, y, (0,255,0))
            show()
        else:
            time.sleep(0.01)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
