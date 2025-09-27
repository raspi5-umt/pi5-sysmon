#!/usr/bin/env python3
# Waveshare 1.69" (240x280) + CST816S
# Robust orientation wizard: try all 8 combos (swap_xy, invert_x, invert_y) and score them.
# Rule: RIGHT swipe -> screen Δx>0, |Δy| small ; DOWN swipe -> screen Δy>0, |Δx| small.
# Then mark exact touch with "TOUCHED!" kept on screen. Hold top-left 1s -> rerun wizard.

import time, json, math, statistics
from pathlib import Path
from PIL import Image, ImageDraw

from lib.LCD_1inch69 import LCD_1inch69

try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15
CALIB_PATH = Path.home() / ".config" / "pi169_touch.json"
CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)

BG   = (0, 0, 0)
FG   = (230, 230, 230)
GOOD = (90, 200, 120)
BAD  = (255, 80, 80)
GRID = (30, 34, 40)
MARK = (255, 80, 80)

RAW_MIN, RAW_MAX = 0, 4095

def draw_grid(d, W, H):
    for gy in range(0, H, 28): d.line((0, gy, W, gy), fill=GRID)
    for gx in range(0, W, 24): d.line((gx, 0, gx, H), fill=GRID)

def default_calib():
    return {"swap_xy": True, "invert_x": True, "invert_y": False,
            "xmin": 0, "xmax": 3840, "ymin": 0, "ymax": 3840}

def clamp_range(a, b):
    lo = max(RAW_MIN, min(a, b))
    hi = min(RAW_MAX, max(a, b))
    if hi - lo < 50:  # aşırı dar ise genişlet
        mid = (lo + hi) // 2
        lo = max(RAW_MIN, mid - 600)
        hi = min(RAW_MAX, mid + 600)
    return lo, hi

def map_point(rx, ry, cfg, W, H):
    if cfg["swap_xy"]: rx, ry = ry, rx
    xmin, xmax = clamp_range(cfg["xmin"], cfg["xmax"])
    ymin, ymax = clamp_range(cfg["ymin"], cfg["ymax"])
    nx = (rx - xmin) / max(1, (xmax - xmin))
    ny = (ry - ymin) / max(1, (ymax - ymin))
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else nx
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else ny
    if cfg["invert_x"]: nx = 1.0 - nx
    if cfg["invert_y"]: ny = 1.0 - ny
    x = int(nx * (W - 1)); y = int(ny * (H - 1))
    return x, y

class Touch:
    def __init__(self):
        self.ok = SMBUS_OK
        self.bus = None
        self.calib = default_calib()
        if CALIB_PATH.exists():
            try: self.calib.update(json.loads(CALIB_PATH.read_text()))
            except Exception: pass
        if self.ok:
            try: self.bus = SMBus(I2C_BUS)
            except Exception: self.ok = False

    def read_raw(self):
        if not self.ok or self.bus is None: return None
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x01, 7)
        except Exception:
            return None
        if (d[1] & 0x0F) == 0:
            return None
        rx = ((d[2] & 0x0F) << 8) | d[3]
        ry = ((d[4] & 0x0F) << 8) | d[5]
        return rx, ry

    # ---------- Wizard helpers ----------
    def _collect_swipe(self, lcd, W, H, label):
        img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img); draw_grid(d,W,H)
        d.text((8,8), f"Swipe {label}", fill=FG)
        d.text((8,26), "Press, move ~1s, release", fill=FG)
        lcd.ShowImage(img)

        # wait for touch start
        t0 = time.time()
        while time.time()-t0 < 6.0:
            r = self.read_raw()
            if r: break
            time.sleep(0.01)
        if not r: return None

        rx_list, ry_list = [], []
        t1 = time.time()
        while time.time()-t1 < 1.2:
            rr = self.read_raw()
            if not rr: break
            rx_list.append(rr[0]); ry_list.append(rr[1])
            time.sleep(0.01)

        if len(rx_list) < 5: return None
        n = len(rx_list)
        k = max(1, n//5)
        # medyan başlangıç ve bitiş
        start = (int(statistics.median(rx_list[:k])), int(statistics.median(ry_list[:k])))
        end   = (int(statistics.median(rx_list[-k:])), int(statistics.median(ry_list[-k:])))
        rng = {
            "xmin": min(rx_list), "xmax": max(rx_list),
            "ymin": min(ry_list), "ymax": max(ry_list)
        }
        return {"start": start, "end": end, **rng}

    def _score_combo(self, cfg, W, H, right_swipe, down_swipe):
        # Map medyan başlangıç-bitiş -> ekran vektörleri
        sx1, sy1 = map_point(*right_swipe["start"], cfg, W, H)
        ex1, ey1 = map_point(*right_swipe["end"],   cfg, W, H)
        v1x, v1y = ex1 - sx1, ey1 - sy1

        sx2, sy2 = map_point(*down_swipe["start"], cfg, W, H)
        ex2, ey2 = map_point(*down_swipe["end"],   cfg, W, H)
        v2x, v2y = ex2 - sx2, ey2 - sy2

        # Ceza fonksiyonu:
        #  - sağ kaydırmada Δx <= 0 ise ağır ceza, |Δy| sapması orta ceza
        #  - aşağı kaydırmada Δy <= 0 ise ağır ceza, |Δx| sapması orta ceza
        #  - büyüklük iyi şey: |Δx| ve |Δy| büyükse puanı düşür (yani ödül)
        NEG = 10000
        score = 0
        if v1x <= 0: score += NEG + abs(v1x)*10
        if v2y <= 0: score += NEG + abs(v2y)*10
        score += 6*abs(v1y) + 6*abs(v2x)
        score += 2*(abs(v1x) + abs(v2y))*(-1)  # ödül: esas eksen büyüklüğü
        # ek: çok büyük açı sapmalarına küçük ceza
        # hedef v1 ~ (1,0), v2 ~ (0,1)
        score += 2*abs(v1y) + 2*abs(v2x)
        return score

    def run_wizard(self, lcd, W, H):
        r = self._collect_swipe(lcd, W, H, "RIGHT")
        if not r:
            self._flash(lcd, W, H, "RIGHT failed", BAD); return False
        time.sleep(0.2)
        d = self._collect_swipe(lcd, W, H, "DOWN")
        if not d:
            self._flash(lcd, W, H, "DOWN failed", BAD); return False

        # ham aralıkları genişlet
        pad = 80
        xmin = max(RAW_MIN, min(r["xmin"], d["xmin"]) - pad)
        xmax = min(RAW_MAX, max(r["xmax"], d["xmax"]) + pad)
        ymin = max(RAW_MIN, min(r["ymin"], d["ymin"]) - pad)
        ymax = min(RAW_MAX, max(r["ymax"], d["ymax"]) + pad)

        best = None
        for swap in (False, True):
            for ix in (False, True):
                for iy in (False, True):
                    cfg = {"swap_xy": swap, "invert_x": ix, "invert_y": iy,
                           "xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}
                    s = self._score_combo(cfg, W, H, r, d)
                    if best is None or s < best[0]:
                        best = (s, cfg)

        _, cfg = best
        self.calib = cfg
        CALIB_PATH.write_text(json.dumps(cfg))
        self._flash(lcd, W, H, f"swap={cfg['swap_xy']} ix={cfg['invert_x']} iy={cfg['invert_y']}", GOOD, "Saved")
        return True

    def _flash(self, lcd, W, H, msg, color, extra=None):
        img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img); draw_grid(d,W,H)
        d.text((8,8), msg, fill=color)
        if extra: d.text((8,26), extra, fill=FG)
        lcd.ShowImage(img); time.sleep(0.8)

class App:
    def __init__(self):
        self.lcd = LCD_1inch69(); self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height
        self.touch = Touch()
        self.hold_tl_t0 = None

    def screen_idle(self, msg="Touch: waiting  |  Hold TL 1s -> wizard"):
        img = Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img); draw_grid(d,self.W,self.H)
        d.text((8,8), msg, fill=FG)
        self.lcd.ShowImage(img)

    def screen_touched(self, x=None, y=None):
        img = Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img); draw_grid(d,self.W,self.H)
        d.text((self.W//2, 12), "TOUCHED!", fill=GOOD, anchor="mm")
        if x is not None and y is not None:
            d.text((8, 28), f"x={x} y={y}", fill=FG)
            d.ellipse((x-16,y-16,x+16,y+16), outline=MARK, width=3)
            d.line((x-22,y, x+22,y), fill=MARK, width=3)
            d.line((x,y-22, x,y+22), fill=MARK, width=3)
        self.lcd.ShowImage(img)

    def ensure_orient(self):
        need = not CALIB_PATH.exists()
        if not need:
            try:
                c = json.loads(CALIB_PATH.read_text())
                if (c.get("xmax",0)-c.get("xmin",0) < 300) or (c.get("ymax",0)-c.get("ymin",0) < 300):
                    need = True
            except Exception:
                need = True
        if need and self.touch.ok:
            self.touch.run_wizard(self.lcd, self.W, self.H)

    def run(self):
        self.ensure_orient()
        self.screen_idle()
        touching = False
        last_idle = time.time()

        while True:
            raw = None
            if self.touch.ok and self.touch.bus is not None:
                for _ in range(3):
                    rr = self.touch.read_raw()
                    if rr: raw = rr; break
                    time.sleep(0.004)

            if raw:
                x,y = map_point(raw[0], raw[1], self.touch.calib, self.W, self.H)
                self.screen_touched(x,y)
                touching = True
                # top-left hold -> wizard
                if x < 52 and y < 40:
                    if self.hold_tl_t0 is None: self.hold_tl_t0 = time.time()
                    elif time.time()-self.hold_tl_t0 > 1.0:
                        self.touch.run_wizard(self.lcd, self.W, self.H)
                        self.hold_tl_t0 = None
                        self.screen_idle()
                        touching = False
                        time.sleep(0.2)
                else:
                    self.hold_tl_t0 = None
            else:
                self.hold_tl_t0 = None
                if touching:
                    touching = False
                    self.screen_idle()
                    last_idle = time.time()
                else:
                    if time.time()-last_idle > 1.0:
                        self.screen_idle()
                        last_idle = time.time()

            time.sleep(0.01)

if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
