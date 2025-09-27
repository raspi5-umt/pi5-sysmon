#!/usr/bin/env python3
# Waveshare 1.69" (240x280) + CST816S (polling)
# Afine kalibrasyon: (rx,ry) -> (x,y) dönüşümünü 4 köşeden fit eder.
# "TOUCHED!" sabit. Dokunduğun noktayı tam yerinde işaretler.
# TL 1sn: yeniden kalibrasyon. Kalibrasyon ~/.config/pi169_touch_affine.json

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

CFG_PATH = Path.home() / ".config" / "pi169_touch_affine.json"
CFG_PATH.parent.mkdir(parents=True, exist_ok=True)

W_TARGET, H_TARGET = 240, 280  # ekran

BG   = (0, 0, 0)
FG   = (230,230,230)
GOOD = (90,200,120)
BAD  = (255,80,80)
GRID = (30,34,40)
MARK = (255,80,80)

def draw_grid(d, W, H):
    for gy in range(0, H, 28): d.line((0, gy, W, gy), fill=GRID)
    for gx in range(0, W, 24): d.line((gx, 0, gx, H), fill=GRID)

def default_cfg():
    # Affine coeffs yoksa kaba bir tahmin: ölçekle ve tersle
    return {
        "affine": [  # a,b,c,d,e,f
            240.0/3840.0, 0.0, 0.0,   # x ~ rx * k
            0.0, 280.0/3840.0, 0.0    # y ~ ry * k
        ]
    }

# ----------------- küçük linear cebir aletleri (NumPy yok) -----------------
def matT(A):  # transpose
    return list(map(list, zip(*A)))

def matMul(A, B):
    # A: m x n, B: n x p
    m, n = len(A), len(A[0])
    n2, p = len(B), len(B[0])
    assert n == n2
    R = [[0.0]*p for _ in range(m)]
    for i in range(m):
        Ai = A[i]
        for k in range(n):
            aik = Ai[k]
            Bk = B[k]
            for j in range(p):
                R[i][j] += aik * Bk[j][j] if p==1 else aik * Bk[j]
    return R  # bu fonksiyon p==1 ve B tek sütun varken daha verimli; aşağıda özel kullanacağız

def matVec(A, v):
    # A: m x n, v: n
    m, n = len(A), len(A[0])
    assert len(v) == n
    out = [0.0]*m
    for i in range(m):
        s = 0.0
        Ai = A[i]
        for j in range(n):
            s += Ai[j]*v[j]
        out[i] = s
    return out

def vecDot(u, v):
    return sum((u[i]*v[i] for i in range(len(u))))

def matAtA(A):
    # A^T A for A: m x n => n x n
    m, n = len(A), len(A[0])
    AT = matT(A)
    R = [[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            s = 0.0
            for k in range(m):
                s += AT[i][k]*A[k][j]
            R[i][j] = s
    return R

def matAtb(A, b):
    # A^T b for A: m x n, b: m
    m, n = len(A), len(A[0])
    AT = matT(A)
    out = [0.0]*n
    for i in range(n):
        s = 0.0
        for k in range(m):
            s += AT[i][k]*b[k]
        out[i] = s
    return out

def solve3(A, b):
    # 3x3 doğrusal sistem çözücü (Cramér + Gauss karışık)
    a11,a12,a13 = A[0]
    a21,a22,a23 = A[1]
    a31,a32,a33 = A[2]
    det = (a11*(a22*a33 - a23*a32)
          -a12*(a21*a33 - a23*a31)
          +a13*(a21*a32 - a22*a31))
    if abs(det) < 1e-9:
        # kötü koşullu; küçük düzenleme
        det = 1e-9 if det >= 0 else -1e-9
    # adjoint * b / det
    b1,b2,b3 = b
    x = [
        ( (a22*a33 - a23*a32)*b1 - (a12*a33 - a13*a32)*b2 + (a12*a23 - a13*a22)*b3 )/det,
        (-(a21*a33 - a23*a31)*b1 + (a11*a33 - a13*a31)*b2 - (a11*a23 - a13*a21)*b3 )/det,
        ( (a21*a32 - a22*a31)*b1 - (a11*a32 - a12*a31)*b2 + (a11*a22 - a12*a21)*b3 )/det
    ]
    return x

def fit_affine(points_raw, points_scr):
    """
    points_raw: [(rx,ry), ...]
    points_scr: [(sx,sy), ...] hedef ekran noktaları
    En küçük karelerle [a b c] ve [d e f] çözer.
    A = [[rx, ry, 1], ...], solve (A^T A) u = A^T sx ve (A^T A) v = A^T sy
    """
    A = [[float(rx), float(ry), 1.0] for (rx,ry) in points_raw]
    sx = [float(x) for (x,_) in points_scr]
    sy = [float(y) for (_,y) in points_scr]
    ATA = matAtA(A)           # 3x3
    ATsx = matAtb(A, sx)      # 3
    ATsy = matAtb(A, sy)      # 3
    u = solve3(ATA, ATsx)     # a,b,c
    v = solve3(ATA, ATsy)     # d,e,f
    return u+v                # [a,b,c,d,e,f]

# ----------------- dokunmatik -----------------
class Touch:
    def __init__(self):
        self.ok = SMBUS_OK
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

    def map_affine(self, rx, ry, W, H):
        a,b,c,d,e,f = self.cfg["affine"]
        x = a*rx + b*ry + c
        y = d*rx + e*ry + f
        # güvenli clamp
        if x < 0: x = 0
        if y < 0: y = 0
        if x > W-1: x = W-1
        if y > H-1: y = H-1
        return int(round(x)), int(round(y))

    # --------- kalibrasyon: 4 köşe, her köşede medyan ---------
    def calibrate(self, lcd, W, H):
        targets = [("TL", 12,12),
                   ("TR", W-12,12),
                   ("BR", W-12,H-12),
                   ("BL", 12,H-12)]
        raw_pts = []
        for i,(name, tx, ty) in enumerate(targets, 1):
            img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img); draw_grid(d,W,H)
            d.text((8,8), f"Calibrate {i}/4: tap {name}", fill=FG)
            r=12
            d.ellipse((tx-r,ty-r,tx+r,ty+r), outline=GOOD, width=2)
            d.line((tx-16,ty,tx+16,ty), fill=GOOD, width=2)
            d.line((tx,ty-16,tx,ty+16), fill=GOOD, width=2)
            lcd.ShowImage(img)

            rx_list, ry_list = [], []
            t0 = time.time()
            # dokunuşu bekle
            while time.time()-t0 < 8.0:
                rxy = self.read_raw()
                if rxy:
                    # dokunurken birkaç örnek topla
                    t1=time.time()
                    while time.time()-t1 < 0.25:
                        r2 = self.read_raw()
                        if r2:
                            rx_list.append(r2[0]); ry_list.append(r2[1])
                        time.sleep(0.01)
                    break
                time.sleep(0.01)
            if not rx_list:
                self._flash(lcd, W, H, "Calibration failed", BAD)
                return False
            rx_med = int(statistics.median(rx_list))
            ry_med = int(statistics.median(ry_list))
            raw_pts.append((rx_med, ry_med))

        # hedef ekran noktaları
        scr_pts = [(12,12), (W-12,12), (W-12,H-12), (12,H-12)]
        coeffs = fit_affine(raw_pts, scr_pts)
        self.cfg["affine"] = coeffs
        try: CFG_PATH.write_text(json.dumps(self.cfg))
        except Exception: pass
        self._flash(lcd, W, H, "Calibration saved", GOOD)
        return True

    def _flash(self, lcd, W, H, msg, color):
        img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img); draw_grid(d,W,H)
        d.text((8,8), msg, fill=color)
        lcd.ShowImage(img); time.sleep(0.8)

# ----------------- uygulama -----------------
class App:
    def __init__(self):
        self.lcd = LCD_1inch69()
        self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height
        # Ekran çözünürlüğün gerçekten 240x280 olduğundan emin olmak için:
        self.W = W_TARGET
        self.H = H_TARGET

        self.touch = Touch()
        self.hold_tl = None

    def screen_idle(self):
        img = Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img); draw_grid(d,self.W,self.H)
        d.text((8,8), "Touch: waiting  |  TL hold 1s -> recalibrate", fill=FG)
        self.lcd.ShowImage(img)

    def screen_touched(self, x, y):
        img = Image.new("RGB",(self.W,self.H),BG); d=ImageDraw.Draw(img); draw_grid(d,self.W,self.H)
        d.text((self.W//2, 12), "TOUCHED!", fill=GOOD, anchor="mm")
        d.text((8,28), f"x={x} y={y}", fill=FG)
        d.ellipse((x-16,y-16,x+16,y+16), outline=MARK, width=3)
        d.line((x-22,y, x+22,y), fill=MARK, width=3)
        d.line((x,y-22, x,y+22), fill=MARK, width=3)
        self.lcd.ShowImage(img)

    def maybe_calibrate_if_empty(self):
        # Affine koefleri yoksa veya saçma ise kalibrasyon
        aff = self.touch.cfg.get("affine")
        need = (not aff) or (len(aff) != 6)
        if not need:
            # çok garip bir durum: katsayılar sıfıra yakınsa
            a,b,c,d,e,f = aff
            if abs(a)+abs(b)+abs(d)+abs(e) < 1e-6:
                need = True
        if need and self.touch.ok:
            self.touch.calibrate(self.lcd, self.W, self.H)

    def run(self):
        self.maybe_calibrate_if_empty()
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
                x,y = self.touch.map_affine(raw[0], raw[1], self.W, self.H)
                self.screen_touched(x,y); touching=True

                # TL hold 1s -> recalibrate
                if x < 52 and y < 40:
                    if self.hold_tl is None: self.hold_tl=time.time()
                    elif time.time()-self.hold_tl > 1.0:
                        self.touch.calibrate(self.lcd, self.W, self.H)
                        self.hold_tl=None; self.screen_idle(); touching=False; time.sleep(0.2)
                else:
                    self.hold_tl=None
            else:
                self.hold_tl=None
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
