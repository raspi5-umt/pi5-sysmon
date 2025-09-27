#!/usr/bin/env python3
# 1.69" 240x280 LCD_1inch69 + CST816S touch: calibrate + draw (copy-paste ready)

import os, time, json, math, subprocess
from pathlib import Path
from PIL import Image, ImageDraw
from collections import deque

# --- vendor lcd driver ---
from lib.LCD_1inch69 import LCD_1inch69

# --- touch (CST816S) ---
try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15

# --- config path for calibration ---
CALIB_PATH = Path.home() / ".config" / "pi169_touch.json"
CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)

# --- colors (ASCII only names) ---
C = {
    "BG":   (0, 0, 0),
    "FG":   (230, 230, 230),
    "ACC":  (120, 180, 255),
    "OK":   ( 90, 200, 120),
    "BAD":  (255,  80,  80),
    "GRID": (24, 28, 36),
}

# ---------------- helpers ----------------
def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def default_calib():
    # safe defaults; will be replaced by interactive calibration
    return {
        "swap_xy": True,
        "invert_x": True,
        "invert_y": False,
        "xmin": 0, "xmax": 3840,
        "ymin": 0, "ymax": 3840
    }

def map_coord(rx, ry, cfg, W, H):
    if cfg.get("swap_xy", True):
        rx, ry = ry, rx
    nx = (rx - cfg["xmin"]) / max(1, (cfg["xmax"] - cfg["xmin"]))
    ny = (ry - cfg["ymin"]) / max(1, (cfg["ymax"] - cfg["ymin"]))
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else nx
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else ny
    if cfg.get("invert_x", True): nx = 1.0 - nx
    if cfg.get("invert_y", False): ny = 1.0 - ny
    x = int(nx * (W - 1))
    y = int(ny * (H - 1))
    return x, y

# ---------------- touch wrapper ----------------
class Touch:
    def __init__(self):
        self.available = SMBUS_OK
        self.bus = None
        self.calib = default_calib()
        self.start_y = None
        self.swipe_thresh = 30

        # load calib if exists
        if CALIB_PATH.exists():
            try:
                self.calib.update(json.loads(CALIB_PATH.read_text()))
            except Exception:
                pass

        if self.available:
            try:
                self.bus = SMBus(I2C_BUS)
                self.bus.read_i2c_block_data(CST816_ADDR, 0x01, 1)
            except Exception:
                self.available = False
                self.bus = None

    def read_raw(self):
        if not self.available: return None
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x01, 7)
        except Exception:
            return None
        if (d[1] & 0x0F) == 0:
            self.start_y = None
            return None
        rx = ((d[2] & 0x0F) << 8) | d[3]
        ry = ((d[4] & 0x0F) << 8) | d[5]
        return rx, ry

    def read_point(self, W, H):
        raw = self.read_raw()
        if not raw: return None
        return map_coord(raw[0], raw[1], self.calib, W, H)

    def detect_swipe(self, y):
        if self.start_y is None:
            self.start_y = y; return 0
        dy = y - self.start_y
        if dy <= -self.swipe_thresh:
            self.start_y = None; return -1
        if dy >=  self.swipe_thresh:
            self.start_y = None; return  1
        return 0

    def calibrate_interactive(self, lcd, W, H):
        targets = [(18,18), (W-18,18), (W-18,H-18), (18,H-18)]
        rx, ry = [], []
        for i,(tx,ty) in enumerate(targets, 1):
            img = Image.new("RGB", (W,H), C["BG"])
            d = ImageDraw.Draw(img)
            d.text((8,8), f"Calibrate {i}/4", fill=C["FG"])
            draw_cross(d, tx, ty, (255, 210, 0))
            lcd.ShowImage(img)
            t0 = time.time()
            while time.time() - t0 < 8.0:
                r = self.read_raw()
                if r:
                    rx.append(r[0]); ry.append(r[1])
                    time.sleep(0.30)
                    break
                time.sleep(0.01)
        if len(rx) >= 2 and len(ry) >= 2:
            pad = 40
            self.calib["xmin"] = max(0, min(rx) - pad)
            self.calib["xmax"] = max(rx) + pad
            self.calib["ymin"] = max(0, min(ry) - pad)
            self.calib["ymax"] = max(ry) + pad
            CALIB_PATH.write_text(json.dumps(self.calib))

# ---------------- drawing ----------------
def draw_cross(d, x, y, col=(0,255,0)):
    r = 6
    d.line((x-r, y, x+r, y), fill=col, width=2)
    d.line((x, y-r, x, y+r), fill=col, width=2)

def grid(d, W, H):
    for gy in range(0, H, 28):
        d.line((0, gy, W, gy), fill=C["GRID"])

# ---------------- app ----------------
class App:
    def __init__(self):
        self.lcd = LCD_1inch69()
        self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height

        self.touch = Touch()
        self.cur_page = 0

        # simple metrics history to show something alive
        self.cpu_hist = deque(maxlen=80)

    def _cpu_pct(self):
        # try vcgencmd; fall back to loadavg eye candy
        try:
            out = subprocess.check_output(["bash","-lc","top -b -n1 | awk '/Cpu\\(s\\)/{print $2}'"])
            p = float(out.decode().strip())
            return clamp(p, 0, 100)
        except Exception:
            try:
                la1 = os.getloadavg()[0]
                p = min(100.0, la1 * 25.0)
                return p
            except Exception:
                return 0.0

    def calibrate_once_if_empty(self):
        # if calib file never saved, force calibration
        if not CALIB_PATH.exists():
            self.touch.calibrate_interactive(self.lcd, self.W, self.H)

    def run(self):
        self.calibrate_once_if_empty()
        last = 0
        while True:
            # background
            img = Image.new("RGB", (self.W, self.H), C["BG"])
            d = ImageDraw.Draw(img)
            grid(d, self.W, self.H)

            # header
            d.text((8,8), "Touch test", fill=C["FG"])
            if not self.touch.available:
                d.text((self.W-8,8), "TOUCH OFF", fill=C["BAD"], anchor="ra")

            # draw cpu history simple bar to show refresh
            now = time.time()
            if now - last > 0.5:
                last = now
                self.cpu_hist.append(self._cpu_pct())
            x0, y0, w, h = 8, self.H-28, self.W-16, 18
            d.rectangle((x0, y0, x0+w, y0+h), outline=C["GRID"], width=1)
            if len(self.cpu_hist) > 1:
                for i, val in enumerate(self.cpu_hist):
                    vx = x0 + int(i * (w-2) / max(1, len(self.cpu_hist)-1))
                    vy = y0 + h - 2 - int(clamp(val,0,100) * (h-4) / 100.0)
                    d.line((vx, y0+h-2, vx, vy), fill=C["ACC"])

            # handle touch
            pt = self.touch.read_point(self.W, self.H)
            if pt:
                x, y = pt
                draw_cross(d, x, y, (0,255,0))
                # top-left long press -> calibration
                if x < 52 and y < 40:
                    t0 = time.time()
                    while True:
                        p2 = self.touch.read_point(self.W, self.H)
                        if not p2: break
                        if time.time() - t0 > 1.0:
                            self.touch.calibrate_interactive(self.lcd, self.W, self.H)
                            break
                        time.sleep(0.02)

            self.lcd.ShowImage(img)
            time.sleep(0.01)

# ---------------- main ----------------
if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
