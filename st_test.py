#!/usr/bin/env python3
# Waveshare 1.69" (240x280) + CST816S
# 2-gesture orientation wizard: Swipe RIGHT, then DOWN. Auto-detects SWAP/INVERT.
# Expands raw range from captured swipes to kill 20x20 corner shrink.
# Then shows "TOUCHED!" and marks exact touch point. Hold top-left 1s to rerun wizard.

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

RAW_MIN, RAW_MAX = 0, 4095  # güvenli çit

def draw_grid(d, W, H):
    for gy in range(0, H, 28): d.line((0, gy, W, gy), fill=GRID)
    for gx in range(0, W, 24): d.line((gx, 0, gx, H), fill=GRID)

def default_calib():
    # bir başlangıç, ama wizard hemen güncelleyecek
    return {"swap_xy": True, "invert_x": True, "invert_y": False,
            "xmin": 0, "xmax": 3840, "ymin": 0, "ymax": 3840}

def map_point(rx, ry, cfg, W, H):
    if cfg["swap_xy"]: rx, ry = ry, rx
    # dinamik clamp
    xmin = max(RAW_MIN, min(cfg["xmin"], cfg["xmax"]))
    xmax = min(RAW_MAX, max(cfg["xmin"], cfg["xmax"]))
    ymin = max(RAW_MIN, min(cfg["ymin"], cfg["ymax"]))
    ymax = min(RAW_MAX, max(cfg["ymin"], cfg["ymax"]))
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
        fingers = d[1] & 0x0F
        if fingers == 0: return None
        rx = ((d[2] & 0x0F) << 8) | d[3]
        ry = ((d[4] & 0x0F) << 8) | d[5]
        return rx, ry

    # ---------------- Orientation Wizard ----------------
    def _collect_swipe(self, lcd, W, H, label):
        """Kullanıcıdan tek bir kaydırma al: başlangıç ve bitiş ham (rx,ry) ve aralıklar."""
        img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img); draw_grid(d,W,H)
        d.text((8,8), f"Swipe {label}", fill=FG)
        d.text((8,26), "Press, move ~1s, release", fill=FG)
        lcd.ShowImage(img)

        # dokunma başlasın
        t0 = time.time()
        while time.time()-t0 < 6.0:
            r = self.read_raw()
            if r: break
            time.sleep(0.01)
        if not r: return None

        # örnek topla
        rx_list, ry_list = [], []
        t1 = time.time()
        while time.time()-t1 < 1.2:
            rr = self.read_raw()
            if not rr: break
            rx_list.append(rr[0]); ry_list.append(rr[1])
            time.sleep(0.01)

        if len(rx_list) < 4: return None
        start = (rx_list[0], ry_list[0])
        end   = (rx_list[-1], ry_list[-1])
        # aralıklar
        xmin, xmax = min(rx_list), max(rx_list)
        ymin, ymax = min(ry_list), max(ry_list)
        return {
            "start": start, "end": end,
            "dx": end[0]-start[0], "dy": end[1]-start[1],
            "xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax
        }

    def run_wizard(self, lcd, W, H):
        """SAĞ ve AŞAĞI kaydırma ile swap/invert ve geniş min/max bul."""
        data_r = self._collect_swipe(lcd, W, H, "RIGHT")
        if not data_r:
            self._flash(lcd, W, H, "RIGHT failed", BAD); return False
        time.sleep(0.3)
        data_d = self._collect_swipe(lcd, W, H, "DOWN")
        if not data_d:
            self._flash(lcd, W, H, "DOWN failed", BAD); return False

        # Eksen tespiti: RIGHT kaydırmada hangi ham eksen daha çok değişti?
        swap_xy = abs(data_r["dy"]) > abs(data_r["dx"])

        # Sağ kaydırma için X yönü
        if swap_xy:
            # x ekranı ry'dan gelir; DOWN/UP ile aynı eksen
            invert_x = False if data_r["dy"] > 0 else True
        else:
            invert_x = False if data_r["dx"] > 0 else True

        # Aşağı kaydırma için Y yönü (ekran y artınca aşağı iner)
        if swap_xy:
            # y ekranı rx'den gelir
            invert_y = False if data_d["dx"] > 0 else True
        else:
            invert_y = False if data_d["dy"] > 0 else True

        # Ham aralık: iki jestte görülen min/max'ları birleştir ve pad ekle
        rx_min = min(data_r["xmin"], data_d["xmin"])
        rx_max = max(data_r["xmax"], data_d["xmax"])
        ry_min = min(data_r["ymin"], data_d["ymin"])
        ry_max = max(data_r["ymax"], data_d["ymax"])
        pad = 80
        cfg = {
            "swap_xy": swap_xy, "invert_x": invert_x, "invert_y": invert_y,
            "xmin": max(RAW_MIN, rx_min - pad), "xmax": min(RAW_MAX, rx_max + pad),
            "ymin": max(RAW_MIN, ry_min - pad), "ymax": min(RAW_MAX, ry_max + pad),
        }
        self.calib = cfg
        CALIB_PATH.write_text(json.dumps(cfg))
        self._flash(lcd, W, H, f"swap={swap_xy} ix={invert_x} iy={invert_y}", GOOD, extra="Saved")
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
        # Dosya var ama saçma aralık ise yine de sihirbaz
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
            # kısa aralıklarla birkaç kez yokla
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
                # sol-üst 1sn -> wizard
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
