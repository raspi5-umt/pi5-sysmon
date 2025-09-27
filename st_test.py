#!/usr/bin/env python3
# Waveshare 1.69" (240x280) + CST816S
# Polling-only (IRQ gerekmez). Dokununca "TOUCHED!" yazısı ve dokunduğun yere hedef çizer.
# İstersen sol-üstte 1sn basılı tut: 4-nokta kalibrasyon. Kalibrasyon ~/.config/pi169_touch.json

import time, json, math
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

def default_calib():
    # Bu değerler çoğu CST816S panelde işe yarar; kalibrasyonla güncellenecek
    return {"swap_xy": True, "invert_x": True, "invert_y": False,
            "xmin": 0, "xmax": 3840, "ymin": 0, "ymax": 3840}

def draw_grid(d, W, H):
    for gy in range(0, H, 28): d.line((0, gy, W, gy), fill=GRID)
    for gx in range(0, W, 24): d.line((gx, 0, gx, H), fill=GRID)

def map_point(rx, ry, cfg, W, H):
    if cfg["swap_xy"]: rx, ry = ry, rx
    nx = (rx - cfg["xmin"]) / max(1, (cfg["xmax"] - cfg["xmin"]))
    ny = (ry - cfg["ymin"]) / max(1, (cfg["ymax"] - cfg["ymin"]))
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else nx
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else ny
    if cfg["invert_x"]: nx = 1.0 - nx
    if cfg["invert_y"]: ny = 1.0 - ny
    x = int(nx * (W-1)); y = int(ny * (H-1))
    return x, y

class Touch:
    def __init__(self):
        self.bus = None
        self.ok = SMBUS_OK
        self.calib = default_calib()
        if CALIB_PATH.exists():
            try: self.calib.update(json.loads(CALIB_PATH.read_text()))
            except Exception: pass
        if self.ok:
            try: self.bus = SMBus(I2C_BUS)
            except Exception: self.ok = False
        self._press_start = None

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

    def read_point(self, W, H):
        raw = self.read_raw()
        if not raw: return None
        return map_point(raw[0], raw[1], self.calib, W, H)

    def calibrate(self, lcd, W, H):
        targets = [(18,18),(W-18,18),(W-18,H-18),(18,H-18)]
        samples = []
        for i,(tx,ty) in enumerate(targets,1):
            img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img)
            draw_grid(d,W,H)
            d.text((8,8), f"Calibrate {i}/4", fill=FG)
            r=10
            d.ellipse((tx-r,ty-r,tx+r,ty+r), outline=GOOD, width=2)
            d.line((tx-14,ty,tx+14,ty), fill=GOOD, width=2)
            d.line((tx,ty-14,tx,ty+14), fill=GOOD, width=2)
            lcd.ShowImage(img)
            t0=time.time()
            got=None
            while time.time()-t0<8.0:
                rr=self.read_raw()
                if rr: got=rr; time.sleep(0.25); break
                time.sleep(0.01)
            if not got:
                img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img)
                d.text((8,8),"Calibration failed", fill=BAD); lcd.ShowImage(img); time.sleep(0.8)
                return False
            samples.append(got)

        rx=[s[0] for s in samples]; ry=[s[1] for s in samples]
        pad=40
        xmin=max(0,min(rx)-pad); xmax=max(rx)+pad
        ymin=max(0,min(ry)-pad); ymax=max(ry)+pad

        best=None
        for swap in (False,True):
            for ix in (False,True):
                for iy in (False,True):
                    cfg={"swap_xy":swap,"invert_x":ix,"invert_y":iy,
                         "xmin":xmin,"xmax":xmax,"ymin":ymin,"ymax":ymax}
                    mapped=[map_point(samples[i][0],samples[i][1],cfg,W,H) for i in range(4)]
                    err=sum(((mx-tx)**2+(my-ty)**2)**0.5 for (mx,my),(tx,ty) in zip(mapped,targets))
                    if best is None or err<best[0]: best=(err,cfg)
        err,cfg=best
        if err>(W+H)*0.9:
            img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img)
            d.text((8,8),"Calibration error high", fill=BAD); lcd.ShowImage(img); time.sleep(0.8)
            return False
        self.calib=cfg
        CALIB_PATH.write_text(json.dumps(cfg))
        img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img)
        d.text((8,8),"Calibration saved", fill=GOOD); lcd.ShowImage(img); time.sleep(0.5)
        return True

class App:
    def __init__(self):
        self.lcd = LCD_1inch69()
        self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height
        self.touch = Touch()
        self.touching = False
        self.hold_tl_t0 = None  # top-left hold timer

    def draw_idle(self):
        img = Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img)
        draw_grid(d,self.W,self.H)
        d.text((8,8),"Touch: waiting  |  Hold TL 1s -> calibrate", fill=FG)
        self.lcd.ShowImage(img)

    def draw_touched(self, x=None, y=None):
        img = Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img)
        draw_grid(d,self.W,self.H)
        # Üstte TOUCHED! sabit kalsın
        d.text((self.W//2, 12), "TOUCHED!", fill=GOOD, anchor="mm")
        # Koordinatlar ve hedef
        if x is not None and y is not None:
            d.text((8, 28), f"x={x} y={y}", fill=FG)
            d.ellipse((x-16,y-16,x+16,y+16), outline=MARK, width=3)
            d.line((x-22,y, x+22,y), fill=MARK, width=3)
            d.line((x,y-22, x,y+22), fill=MARK, width=3)
        self.lcd.ShowImage(img)

    def run(self):
        self.draw_idle()
        last_idle = time.time()
        while True:
            # birkaç hızlı probe: kısa dokunuşları yakalamak için
            touched_raw = None
            if self.touch.ok and self.touch.bus is not None:
                for _ in range(3):
                    rr = self.touch.read_raw()
                    if rr:
                        touched_raw = rr
                        break
                    time.sleep(0.004)

            if touched_raw:
                x,y = map_point(touched_raw[0], touched_raw[1], self.touch.calib, self.W, self.H)
                self.draw_touched(x,y)
                self.touching = True
                # Sol-üstte 1sn -> kalibrasyon
                if x < 52 and y < 40:
                    if self.hold_tl_t0 is None: self.hold_tl_t0 = time.time()
                    elif time.time() - self.hold_tl_t0 > 1.0:
                        self.touch.calibrate(self.lcd, self.W, self.H)
                        self.hold_tl_t0 = None
                        self.draw_idle()
                        self.touching = False
                        time.sleep(0.2)
                else:
                    self.hold_tl_t0 = None
            else:
                self.hold_tl_t0 = None
                if self.touching:
                    self.touching = False
                    self.draw_idle()
                    last_idle = time.time()
                else:
                    if time.time() - last_idle > 1.0:
                        self.draw_idle()
                        last_idle = time.time()

            time.sleep(0.01)

if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
