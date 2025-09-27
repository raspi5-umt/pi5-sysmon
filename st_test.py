#!/usr/bin/env python3
# 1.69" 240x280 LCD_1inch69 + CST816S touch
# Auto-orientation calibration (tries all SWAP/INVERT combos) + median sampling + smoothing.
# Copy-paste ready.

import os, time, json, math, subprocess, statistics
from pathlib import Path
from collections import deque
from PIL import Image, ImageDraw

# ---- vendor lcd driver ----
from lib.LCD_1inch69 import LCD_1inch69

# ---- touch (CST816S) ----
try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15

CALIB_PATH = Path.home() / ".config" / "pi169_touch.json"
CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)

C = {
    "BG":   (0, 0, 0),
    "FG":   (230, 230, 230),
    "ACC":  (120, 180, 255),
    "OK":   ( 90, 200, 120),
    "BAD":  (255,  80,  80),
    "GRID": (24, 28, 36),
}

# ---------- helpers ----------
def clamp(v, lo, hi):
    try:
        v = float(v)
        if not math.isfinite(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def median(vals):
    return int(statistics.median(vals)) if vals else 0

def default_calib():
    return {
        "swap_xy": True,
        "invert_x": True,
        "invert_y": False,
        "xmin": 0, "xmax": 3840,
        "ymin": 0, "ymax": 3840
    }

def draw_cross(d, x, y, col=(0,255,0)):
    r = 7
    d.line((x-r, y, x+r, y), fill=col, width=2)
    d.line((x, y-r, x, y+r), fill=col, width=2)

def grid(d, W, H):
    for gy in range(0, H, 28):
        d.line((0, gy, W, gy), fill=C["GRID"])

# ---------- touch wrapper ----------
class Touch:
    def __init__(self):
        self.available = SMBUS_OK
        self.bus = None
        self.calib = default_calib()
        self.x_smooth = None
        self.y_smooth = None
        self.alpha = 0.35  # smoothing factor

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
            self.x_smooth = self.y_smooth = None
            return None
        rx = ((d[2] & 0x0F) << 8) | d[3]
        ry = ((d[4] & 0x0F) << 8) | d[5]
        return rx, ry

    @staticmethod
    def _map(rx, ry, cfg, W, H):
        if cfg["swap_xy"]: rx, ry = ry, rx
        nx = (rx - cfg["xmin"]) / max(1, (cfg["xmax"] - cfg["xmin"]))
        ny = (ry - cfg["ymin"]) / max(1, (cfg["ymax"] - cfg["ymin"]))
        nx = 0.0 if nx < 0 else 1.0 if nx > 1 else nx
        ny = 0.0 if ny < 0 else 1.0 if ny > 1 else ny
        if cfg["invert_x"]: nx = 1.0 - nx
        if cfg["invert_y"]: ny = 1.0 - ny
        x = int(nx * (W - 1))
        y = int(ny * (H - 1))
        return x, y

    def read_point(self, W, H):
        raw = self.read_raw()
        if not raw: return None
        x, y = self._map(raw[0], raw[1], self.calib, W, H)
        # simple low-pass smoothing to fight jitter
        if self.x_smooth is None:
            self.x_smooth, self.y_smooth = x, y
        else:
            a = self.alpha
            self.x_smooth = int(self.x_smooth*(1-a) + x*a)
            self.y_smooth = int(self.y_smooth*(1-a) + y*a)
        return self.x_smooth, self.y_smooth

    # ---------- auto-orientation calibration ----------
    def calibrate_interactive(self, lcd, W, H):
        targets = [(18,18), (W-18,18), (W-18,H-18), (18,H-18)]  # TL, TR, BR, BL
        samples = []  # list of raw (rx,ry) for each target
        for i,(tx,ty) in enumerate(targets, 1):
            img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
            d.text((8,8), f"Calibrate {i}/4", fill=C["FG"])
            draw_cross(d, tx, ty, (255,210,0)); lcd.ShowImage(img)
            # collect multiple raw hits and take median to avoid jitter
            rx_list, ry_list = [], []
            t0 = time.time()
            while time.time() - t0 < 8.0:
                r = self.read_raw()
                if r:
                    rx_list.append(r[0]); ry_list.append(r[1])
                    time.sleep(0.12)  # small debounce
                    # require a couple of samples
                    if len(rx_list) >= 5: break
                else:
                    time.sleep(0.01)
            if rx_list:
                samples.append((median(rx_list), median(ry_list)))
            else:
                # if user missed a point, put a placeholder to force retry later
                samples.append(None)

        if any(s is None for s in samples):
            img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
            d.text((8,8), "Calibration failed, try again", fill=C["BAD"]); lcd.ShowImage(img)
            time.sleep(1.0)
            return False

        # raw mins/maxes from medians with small padding
        rx_vals = [s[0] for s in samples]; ry_vals = [s[1] for s in samples]
        pad = 40
        xmin = max(0, min(rx_vals) - pad); xmax = max(rx_vals) + pad
        ymin = max(0, min(ry_vals) - pad); ymax = max(ry_vals) + pad

        # try all orientation combos and pick the one that maps corners closest to targets
        combos = []
        for swap in (False, True):
            for ix in (False, True):
                for iy in (False, True):
                    cfg = {"swap_xy": swap, "invert_x": ix, "invert_y": iy,
                           "xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}
                    mapped = [self._map(samples[i][0], samples[i][1], cfg, W, H) for i in range(4)]
                    # expected order TL, TR, BR, BL matches targets list
                    err = 0.0
                    for (mx,my), (tx,ty) in zip(mapped, targets):
                        err += ((mx - tx)**2 + (my - ty)**2) ** 0.5
                    combos.append((err, cfg))
        combos.sort(key=lambda t: t[0])
        best_err, best_cfg = combos[0]

        # accept only if not insane
        if best_err > (W+H)*0.9:
            img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
            d.text((8,8), "Calibration error too high", fill=C["BAD"]); lcd.ShowImage(img)
            time.sleep(1.0)
            return False

        self.calib = best_cfg
        CALIB_PATH.write_text(json.dumps(self.calib))
        img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
        d.text((8,8), "Calibration saved", fill=C["OK"])
        d.text((8,28), f"swap={best_cfg['swap_xy']} ix={best_cfg['invert_x']} iy={best_cfg['invert_y']}", fill=C["FG"])
        lcd.ShowImage(img); time.sleep(0.8)
        return True

# ---------- app ----------
class App:
    def __init__(self):
        self.lcd = LCD_1inch69()
        self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height

        self.touch = Touch()
        self.cpu_hist = deque(maxlen=80)

    def _cpu_pct(self):
        try:
            out = subprocess.check_output(["bash","-lc","top -b -n1 | awk '/Cpu\\(s\\)/{print $2}'"])
            return clamp(out.decode().strip(), 0, 100)
        except Exception:
            try:
                la1 = os.getloadavg()[0]
                return clamp(la1*25.0, 0, 100)
            except Exception:
                return 0.0

    def ensure_calibrated(self):
        # Force calibration if file missing or looks default-ish
        need = not CALIB_PATH.exists()
        if not need:
            try:
                cfg = json.loads(CALIB_PATH.read_text())
                need = (cfg.get("xmax",0)-cfg.get("xmin",0) < 200) or (cfg.get("ymax",0)-cfg.get("ymin",0) < 200)
            except Exception:
                need = True
        if need and self.touch.available:
            self.touch.calibrate_interactive(self.lcd, self.W, self.H)

    def run(self):
        self.ensure_calibrated()
        last = 0
        while True:
            img = Image.new("RGB", (self.W, self.H), C["BG"]); d = ImageDraw.Draw(img)
            grid(d, self.W, self.H)
            d.text((8,8), "Touch test (hold TL 1s to recalib)", fill=C["FG"])
            if not self.touch.available:
                d.text((self.W-8,8), "TOUCH OFF", fill=C["BAD"], anchor="ra")

            now = time.time()
            if now - last > 0.5:
                last = now
                self.cpu_hist.append(self._cpu_pct())
            x0,y0,w,h = 8, self.H-28, self.W-16, 18
            d.rectangle((x0,y0,x0+w,y0+h), outline=C["GRID"], width=1)
            if len(self.cpu_hist) > 1:
                for i,val in enumerate(self.cpu_hist):
                    vx = x0 + int(i*(w-2)/max(1,len(self.cpu_hist)-1))
                    vy = y0 + h - 2 - int(clamp(val,0,100)*(h-4)/100.0)
                    d.line((vx, y0+h-2, vx, vy), fill=C["ACC"])

            pt = self.touch.read_point(self.W, self.H)
            if pt:
                x,y = pt
                draw_cross(d, x, y, (0,255,0))
                # top-left long press to recalibrate
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

# ---------- main ----------
if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
