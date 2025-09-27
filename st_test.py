#!/usr/bin/env python3
# Waveshare 1.69" (240x280) + CST816S (polling)
# "TOUCHED!" sabit. Dokunduğun noktayı işaretler.
# DÜZELTME: Anti-diagonal yansıtma normalize edildi (dikdörtgende doğru çalışır, x negatif OLMAZ).
# Adaptif ham-aralık genişletme: raw min/max, uçlara yaklaşınca otomatik genişler.
# TL 1sn: yön sihirbazı (RIGHT, DOWN).  TR 1sn: anti-diagonal düzeltme toggle.

import time, json, statistics
from pathlib import Path
from PIL import Image, ImageDraw
from lib.LCD_1inch69 import LCD_1inch69

try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
ADDR    = 0x15

CFG_PATH = Path.home() / ".config" / "pi169_touch.json"
CFG_PATH.parent.mkdir(parents=True, exist_ok=True)

BG   = (0, 0, 0)
FG   = (230,230,230)
GOOD = (90,200,120)
BAD  = (255,80,80)
GRID = (30,34,40)
MARK = (255,80,80)

RAW_MIN, RAW_MAX = 0, 4095

def draw_grid(d, W, H):
    for gy in range(0, H, 28): d.line((0, gy, W, gy), fill=GRID)
    for gx in range(0, W, 24): d.line((gx, 0, gx, H), fill=GRID)

def default_cfg():
    return {
        "swap_xy": True, "invert_x": True, "invert_y": False,
        "xmin": 0, "xmax": 3840, "ymin": 0, "ymax": 3840,
        "ad_fix": True   # anti-diagonal düzeltme varsayılan AÇIK
    }

def clamp_range(a,b):
    lo = max(RAW_MIN, min(a,b)); hi = min(RAW_MAX, max(a,b))
    if hi - lo < 80:  # aşırı dar aralıkları genişlet
        mid = (lo+hi)//2
        lo = max(RAW_MIN, mid-700); hi = min(RAW_MAX, mid+700)
    return lo,hi

def map_point_base(rx, ry, cfg, W, H):
    """AD düzeltme YOK. Sadece swap/invert + normalize."""
    if cfg["swap_xy"]: rx, ry = ry, rx
    xmin,xmax = clamp_range(cfg["xmin"], cfg["xmax"])
    ymin,ymax = clamp_range(cfg["ymin"], cfg["ymax"])
    nx = (rx - xmin) / max(1, (xmax - xmin))
    ny = (ry - ymin) / max(1, (ymax - ymin))
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else nx
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else ny
    if cfg["invert_x"]: nx = 1.0 - nx
    if cfg["invert_y"]: ny = 1.0 - ny
    x = int(nx * (W-1)); y = int(ny * (H-1))
    return x, y

def map_point(rx, ry, cfg, W, H):
    """AD düzeltme DAHİL. Dikdörtgen için normalize edilerek yapılır."""
    x, y = map_point_base(rx, ry, cfg, W, H)
    if cfg.get("ad_fix", False):
        # normalize et, anti-diagonal (X,Y)->(1-Y, 1-X), sonra geri ölçekle
        X = x / float(W-1) if W > 1 else 0.0
        Y = y / float(H-1) if H > 1 else 0.0
        X2 = 1.0 - Y
        Y2 = 1.0 - X
        x  = int(round(X2 * (W-1)))
        y  = int(round(Y2 * (H-1)))
        # güvenli clamp
        x = 0 if x < 0 else (W-1 if x > W-1 else x)
        y = 0 if y < 0 else (H-1 if y > H-1 else y)
    return x, y

class Touch:
    def __init__(self):
        self.ok  = SMBUS_OK
        self.bus = None
        self.cfg = default_cfg()
        if CFG_PATH.exists():
            try: self.cfg.update(json.loads(CFG_PATH.read_text()))
            except Exception: pass
        if self.ok:
            try: self.bus = SMBus(I2C_BUS)
            except Exception: self.ok = False

    def read_raw(self):
        if not self.ok or self.bus is None: return None
        try:
            d = self.bus.read_i2c_block_data(ADDR, 0x01, 7)
        except Exception:
            return None
        if (d[1] & 0x0F) == 0: return None
        rx = ((d[2] & 0x0F) << 8) | d[3]
        ry = ((d[4] & 0x0F) << 8) | d[5]
        return rx, ry

    def adapt_range(self, rx, ry):
        """Ham aralığı uçlara yaklaşıldığında genişletir; konfige kaydeder."""
        pad = 60; changed = False
        xmin,xmax = self.cfg["xmin"], self.cfg["xmax"]
        ymin,ymax = self.cfg["ymin"], self.cfg["ymax"]
        if rx < xmin + 20:
            xmin = max(RAW_MIN, rx - pad); changed = True
        if rx > xmax - 20:
            xmax = min(RAW_MAX, rx + pad); changed = True
        if ry < ymin + 20:
            ymin = max(RAW_MIN, ry - pad); changed = True
        if ry > ymax - 20:
            ymax = min(RAW_MAX, ry + pad); changed = True
        if changed:
            self.cfg["xmin"], self.cfg["xmax"] = xmin, xmax
            self.cfg["ymin"], self.cfg["ymax"] = ymin, ymax
            try: CFG_PATH.write_text(json.dumps(self.cfg))
            except Exception: pass

    # ---- 2-jest sihirbazı (RIGHT, DOWN) ----
    def _collect_swipe(self, lcd, W, H, label):
        img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img); draw_grid(d,W,H)
        d.text((8,8), f"Swipe {label}", fill=FG); d.text((8,26), "Press, move ~1s, release", fill=FG)
        lcd.ShowImage(img)

        # dokunma başlangıcı
        t0=time.time()
        while time.time()-t0<6.0:
            r=self.read_raw()
            if r: break
            time.sleep(0.01)
        if not r: return None

        rx,ry=[],[]
        t1=time.time()
        while time.time()-t1<1.2:
            rr=self.read_raw()
            if not rr: break
            rx.append(rr[0]); ry.append(rr[1]); time.sleep(0.01)
        if len(rx)<5: return None
        n=len(rx); k=max(1,n//5)
        start=(int(statistics.median(rx[:k])), int(statistics.median(ry[:k])))
        end  =(int(statistics.median(rx[-k:])),int(statistics.median(ry[-k:])))
        return {"start":start,"end":end,"xmin":min(rx),"xmax":max(rx),"ymin":min(ry),"ymax":max(ry)}

    def run_wizard(self, lcd, W, H):
        # AD düzeltmeyi geçici kapat: yön tespiti swap/invert üzerinden yapılmalı
        had_ad = self.cfg.get("ad_fix", False)
        self.cfg["ad_fix"] = False

        r=self._collect_swipe(lcd,W,H,"RIGHT")
        if not r: self._flash(lcd,W,H,"RIGHT failed",BAD); self.cfg["ad_fix"]=had_ad; return False
        time.sleep(0.2)
        d=self._collect_swipe(lcd,W,H,"DOWN")
        if not d: self._flash(lcd,W,H,"DOWN failed",BAD); self.cfg["ad_fix"]=had_ad; return False

        pad=80
        xmin = max(RAW_MIN, min(r["xmin"], d["xmin"]) - pad)
        xmax = min(RAW_MAX, max(r["xmax"], d["xmax"]) + pad)
        ymin = max(RAW_MIN, min(r["ymin"], d["ymin"]) - pad)
        ymax = min(RAW_MAX, max(r["ymax"], d["ymax"]) + pad)

        best=None
        for swap in (False,True):
            for ix in (False,True):
                for iy in (False,True):
                    cfg={"swap_xy":swap,"invert_x":ix,"invert_y":iy,
                         "xmin":xmin,"xmax":xmax,"ymin":ymin,"ymax":ymax,
                         "ad_fix": False}
                    # map BASE (ad_fix kapalı)
                    sx1,sy1 = map_point_base(*r["start"], cfg, W, H)
                    ex1,ey1 = map_point_base(*r["end"],   cfg, W, H)
                    v1x,v1y = ex1-sx1, ey1-sy1
                    sx2,sy2 = map_point_base(*d["start"], cfg, W, H)
                    ex2,ey2 = map_point_base(*d["end"],   cfg, W, H)
                    v2x,v2y = ex2-sx2, ey2-sy2
                    NEG=10000
                    s=0
                    if v1x<=0: s+=NEG+abs(v1x)*10
                    if v2y<=0: s+=NEG+abs(v2y)*10
                    s+=6*abs(v1y)+6*abs(v2x) - 2*(abs(v1x)+abs(v2y))
                    if best is None or s<best[0]: best=(s,cfg)
        self.cfg = best[1]
        # AD düzeltmeyi eski haline getir (varsayılan açık kalsın)
        self.cfg["ad_fix"] = had_ad if isinstance(had_ad,bool) else True
        CFG_PATH.write_text(json.dumps(self.cfg))
        self._flash(lcd,W,H,f"swap={self.cfg['swap_xy']} ix={self.cfg['invert_x']} iy={self.cfg['invert_y']}",GOOD,"Saved")
        return True

    def _flash(self,lcd,W,H,msg,color,extra=None):
        img=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img); draw_grid(d,W,H)
        d.text((8,8),msg,fill=color)
        if extra: d.text((8,26),extra,fill=FG)
        lcd.ShowImage(img); time.sleep(0.9)

class App:
    def __init__(self):
        self.lcd = LCD_1inch69(); self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W,self.H = self.lcd.width, self.lcd.height
        self.touch = Touch()
        self.hold_tl = None
        self.hold_tr = None

    def screen_idle(self, msg="Touch: waiting  |  TL: wizard  TR: flip"):
        img=Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img); draw_grid(d,self.W,self.H)
        d.text((8,8), msg, fill=FG)
        self.lcd.ShowImage(img)

    def screen_touched(self, x=None, y=None):
        img=Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img); draw_grid(d,self.W,self.H)
        d.text((self.W//2,12), "TOUCHED!", fill=GOOD, anchor="mm")
        if x is not None and y is not None:
            d.text((8,28), f"x={x} y={y}", fill=FG)
            d.ellipse((x-16,y-16,x+16,y+16), outline=MARK, width=3)
            d.line((x-22,y,x+22,y), fill=MARK, width=3)
            d.line((x,y-22,x,y+22), fill=MARK, width=3)
        self.lcd.ShowImage(img)

    def run(self):
        self.screen_idle()
        touching=False; last_idle=time.time()
        while True:
            raw=None
            if self.touch.ok and self.touch.bus is not None:
                for _ in range(3):
                    rr=self.touch.read_raw()
                    if rr: raw=rr; break
                    time.sleep(0.004)

            if raw:
                # adaptif aralığı genişlet
                self.touch.adapt_range(raw[0], raw[1])
                # haritalama + normalize anti-diagonal düzeltme
                x,y = map_point(raw[0], raw[1], self.touch.cfg, self.W, self.H)
                self.screen_touched(x,y); touching=True

                # TL hold 1s -> wizard
                if x < 52 and y < 40:
                    if self.hold_tl is None: self.hold_tl=time.time()
                    elif time.time()-self.hold_tl > 1.0:
                        self.touch.run_wizard(self.lcd,self.W,self.H)
                        self.hold_tl=None; self.screen_idle(); touching=False; time.sleep(0.2)
                else:
                    self.hold_tl=None

                # TR hold 1s -> anti-diagonal flip toggle
                if x > self.W-52 and y < 40:
                    if self.hold_tr is None: self.hold_tr=time.time()
                    elif time.time()-self.hold_tr > 1.0:
                        self.touch.cfg["ad_fix"] = not self.touch.cfg.get("ad_fix", False)
                        try: CFG_PATH.write_text(json.dumps(self.touch.cfg))
                        except Exception: pass
                        txt = "AD FIX ON" if self.touch.cfg["ad_fix"] else "AD FIX OFF"
                        img=Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img); draw_grid(d,self.W,self.H)
                        d.text((8,8), txt, fill=GOOD); self.lcd.ShowImage(img); time.sleep(0.8)
                        self.hold_tr=None; self.screen_idle(); touching=False; time.sleep(0.2)
                else:
                    self.hold_tr=None

            else:
                self.hold_tl=self.hold_tr=None
                if touching:
                    touching=False; self.screen_idle(); last_idle=time.time()
                else:
                    if time.time()-last_idle > 1.0:
                        self.screen_idle(); last_idle=time.time()
            time.sleep(0.01)

if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
