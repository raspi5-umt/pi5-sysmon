#!/usr/bin/env python3
# 1.69" 240x280 LCD_1inch69 + CST816S touch
# "Tap-to-mark": Dokunduğun yerde tek ve net hedef işareti. İz bırakmaz.
# İlk çalıştırmada 4-nokta kalibrasyon. Sol-üstte 1s basılı tut -> yeniden kalibrasyon.

import time, json, math, subprocess
from pathlib import Path
from PIL import Image, ImageDraw

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
CALIB_PATH = Path.home() / ".config" / "pi169_touch.json"
CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)

C = {
    "BG":   (0, 0, 0),
    "FG":   (230, 230, 230),
    "ACC":  (120, 180, 255),
    "OK":   ( 90, 200, 120),
    "BAD":  (255,  80,  80),
    "GRID": (30, 34, 40),
    "X":    (255,  80,  80),   # hedef rengi
}

def draw_grid(d, W, H):
    for gy in range(0, H, 28):
        d.line((0, gy, W, gy), fill=C["GRID"])
    for gx in range(0, W, 24):
        d.line((gx, 0, gx, H), fill=C["GRID"])

def default_calib():
    return {"swap_xy": True, "invert_x": True, "invert_y": False,
            "xmin": 0, "xmax": 3840, "ymin": 0, "ymax": 3840}

class Touch:
    def __init__(self):
        self.available = SMBUS_OK
        self.bus = None
        self.calib = default_calib()
        if CALIB_PATH.exists():
            try: self.calib.update(json.loads(CALIB_PATH.read_text()))
            except Exception: pass
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
        r = self.read_raw()
        if not r: return None
        return self._map(r[0], r[1], self.calib, W, H)

    # --- basit 4-nokta kalibrasyon ---
    def calibrate(self, lcd, W, H):
        targets = [(18,18), (W-18,18), (W-18,H-18), (18,H-18)]  # TL,TR,BR,BL
        samples = []
        for i,(tx,ty) in enumerate(targets, 1):
            img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
            draw_grid(d, W, H)
            d.text((8,8), f"Calibrate {i}/4", fill=C["FG"])
            # hedef çiz
            r=10
            d.ellipse((tx-r,ty-r,tx+r,ty+r), outline=C["ACC"], width=2)
            d.line((tx-14,ty,tx+14,ty), fill=C["ACC"], width=2)
            d.line((tx,ty-14,tx,ty+14), fill=C["ACC"], width=2)
            lcd.ShowImage(img)
            # tek dokunuş bekle
            t0 = time.time()
            while time.time() - t0 < 8.0:
                raw = self.read_raw()
                if raw:
                    samples.append(raw)
                    time.sleep(0.3)
                    break
                time.sleep(0.01)

        if len(samples) < 4:
            # başarısız
            img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
            d.text((8,8), "Calibration failed", fill=C["BAD"])
            lcd.ShowImage(img); time.sleep(0.8)
            return False

        rx = [s[0] for s in samples]; ry = [s[1] for s in samples]
        pad = 40
        xmin = max(0, min(rx) - pad); xmax = max(rx) + pad
        ymin = max(0, min(ry) - pad); ymax = max(ry) + pad

        # dört kombinasyondan en iyi eşleşeni seç
        combos = []
        for swap in (False, True):
            for ix in (False, True):
                for iy in (False, True):
                    cfg = {"swap_xy": swap, "invert_x": ix, "invert_y": iy,
                           "xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}
                    mapped = [self._map(samples[i][0], samples[i][1], cfg, W, H) for i in range(4)]
                    err = 0.0
                    for (mx,my), (tx,ty) in zip(mapped, targets):
                        err += ((mx-tx)**2 + (my-ty)**2)**0.5
                    combos.append((err, cfg))
        combos.sort(key=lambda t: t[0])
        best_err, best_cfg = combos[0]
        if best_err > (W+H)*0.9:
            img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
            d.text((8,8), "Calibration error high", fill=C["BAD"])
            lcd.ShowImage(img); time.sleep(0.8)
            return False

        self.calib = best_cfg
        CALIB_PATH.write_text(json.dumps(self.calib))
        img = Image.new("RGB", (W,H), C["BG"]); d = ImageDraw.Draw(img)
        d.text((8,8), "Calibration saved", fill=C["OK"])
        lcd.ShowImage(img); time.sleep(0.5)
        return True

class App:
    def __init__(self):
        self.lcd = LCD_1inch69()
        self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height
        self.touch = Touch()

    def ensure_calibrated(self):
        need = not CALIB_PATH.exists()
        if not need:
            try:
                cfg = json.loads(CALIB_PATH.read_text())
                need = (cfg.get("xmax",0)-cfg.get("xmin",0) < 200) or (cfg.get("ymax",0)-cfg.get("ymin",0) < 200)
            except Exception:
                need = True
        if need and self.touch.available:
            self.touch.calibrate(self.lcd, self.W, self.H)

    def draw_idle(self):
        img = Image.new("RGB", (self.W, self.H), C["BG"]); d = ImageDraw.Draw(img)
        draw_grid(d, self.W, self.H)
        d.text((8,8), "Tap anywhere to mark", fill=C["FG"])
        d.text((8,26), "Hold top-left 1s -> Recalib", fill=C["FG"])
        if not self.touch.available:
            d.text((self.W-8,8), "TOUCH OFF", fill=C["BAD"], anchor="ra")
        self.lcd.ShowImage(img)

    def draw_mark(self, x, y):
        img = Image.new("RGB", (self.W, self.H), C["BG"]); d = ImageDraw.Draw(img)
        draw_grid(d, self.W, self.H)
        # büyük hedef
        d.ellipse((x-16,y-16,x+16,y+16), outline=C["X"], width=3)
        d.line((x-22,y, x+22,y), fill=C["X"], width=3)
        d.line((x,y-22, x,y+22), fill=C["X"], width=3)
        # koordinatlar
        d.text((8,8), f"x={x} y={y}", fill=C["FG"])
        self.lcd.ShowImage(img)

    def run(self):
        self.ensure_calibrated()
        idle_since = 0
        touching = False
        hold_tl_t0 = None

        while True:
            pt = self.touch.read_point(self.W, self.H)
            if pt:
                x,y = pt
                # sol-üst 1s basılı -> yeniden kalibrasyon
                if x < 52 and y < 40:
                    if hold_tl_t0 is None: hold_tl_t0 = time.time()
                    elif time.time() - hold_tl_t0 > 1.0:
                        self.touch.calibrate(self.lcd, self.W, self.H)
                        hold_tl_t0 = None
                        continue
                else:
                    hold_tl_t0 = None

                self.draw_mark(x, y)
                touching = True
                idle_since = time.time()
            else:
                hold_tl_t0 = None
                if touching:
                    # parmağı kaldırdı; 150 ms sonra idle ekrana dön
                    if time.time() - idle_since > 0.15:
                        touching = False
                        self.draw_idle()
                else:
                    # idle ekranı periyodik yenile
                    if time.time() - idle_since > 1.0:
                        self.draw_idle()
                        idle_since = time.time()

            time.sleep(0.01)

if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
