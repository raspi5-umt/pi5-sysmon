#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Raspberry Pi 5 • 1.69" (240x280) • 4 sayfa • sağa/sola swipe
# Sayfalar: 1) Sıcaklık  2) RAM  3) CPU  4) Hepsi
# Görüntü: lib/LCD_1inch69.py  • Dokunmatik: CST816S (0x15)
# UI: halka göstergeler + bar + sparkline, koyu/açık tema (sağ üst dokun)

import os, sys, time, math, threading, subprocess
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import psutil

# ---------- SÜRÜCÜ ----------
from lib.LCD_1inch69 import LCD_1inch69

# ---------- TOUCH ----------
try:
    from smbus2 import SMBus
    _SMBUS_OK = True
except Exception:
    _SMBUS_OK = False

# Hangi I2C bus? Dokunmatiği düzelttiğini söyledin, ama yine de oto dene.
CAND_BUSES = [1, 13, 14]
CST816_ADDR = 0x15

# ---------- TEMA ----------
DARK = dict(
    BG=(6,10,16), FG=(235,235,240), ACC1=(120,180,255), ACC2=(255,120,180),
    OK=(90,210,120), WARN=(255,180,60), BAD=(255,85,85),
    GRID=(20,26,34), BARBG=(18,22,28), MUTED=(150,155,165)
)
LIGHT = dict(
    BG=(242,245,250), FG=(24,28,34), ACC1=(40,110,240), ACC2=(200,70,120),
    OK=(60,170,90), WARN=(230,150,40), BAD=(210,60,60),
    GRID=(210,216,224), BARBG=(210,216,224), MUTED=(90,95,105)
)

# ---------- FONT ----------
def load_font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, sz)
    return ImageFont.load_default()
F11, F12, F14, F16, F18, F22 = (load_font(s) for s in (11,12,14,16,18,22))

# ---------- ÇİZİM ARAÇLARI ----------
def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def pick_color(p, C):
    p = clamp(p,0,100)
    return C["OK"] if p < 70 else (C["WARN"] if p < 85 else C["BAD"])

def bar(d, x,y,w,h,pct,C):
    pct = clamp(pct,0,100)
    d.rounded_rectangle([x,y,x+w,y+h], radius=6, fill=C["BARBG"])
    d.rounded_rectangle([x,y,x+int(w*pct/100.0),y+h], radius=6, fill=pick_color(pct,C))

def ring(d, cx, cy, r, pct, C, width=12):
    pct = clamp(pct,0,100)/100.0
    box=[cx-r, cy-r, cx+r, cy+r]
    d.arc(box, start=135, end=405, width=width, fill=C["BARBG"])
    d.arc(box, start=135, end=135+int(270*pct), width=width, fill=pick_color(pct*100,C))

def spark(d, x,y,w,h,series,color,C,grid=True):
    if grid:
        for gy in range(1,4):
            gy_y = y + int(gy*h/4)
            d.line((x, gy_y, x+w, gy_y), fill=C["GRID"])
    vals=[]
    for v in list(series):
        try:
            vv=float(v)
            if math.isfinite(vv): vals.append(vv)
        except: pass
    if len(vals)<2 or max(vals)==min(vals):
        py = y + h//2
        d.line((x,py,x+w,py), fill=color, width=2); return
    n=len(vals); mn=min(vals); mx=max(vals); prev=None
    for i,v in enumerate(vals):
        t=(v-mn)/(mx-mn)
        px = x + int(i*(w-1)/max(1,n-1))
        py = y + h - 1 - int(t*(h-1))
        if prev: d.line((prev[0],prev[1],px,py), fill=color, width=2)
        prev=(px,py)

def header(d, C, W, title):
    d.text((12,8), title, font=F22, fill=C["FG"])
    d.text((W-12,8), time.strftime("%H:%M"), font=F16, fill=C["ACC1"], anchor="ra")

# ---------- METRİKLER ----------
def _cpu_temp():
    try:
        out = subprocess.check_output(["vcgencmd","measure_temp"], stderr=subprocess.DEVNULL).decode()
        return float(out.split("=")[1].split("'")[0])
    except Exception:
        try:
            return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000.0
        except Exception:
            return 0.0

class Metrics:
    def __init__(self, hist_len=120):
        self.cpu = 0.0
        self.ram = 0.0
        self.temp = 0.0
        self.hcpu = deque(maxlen=hist_len)
        self.hram = deque(maxlen=hist_len)
        self.htmp = deque(maxlen=hist_len)
    def update(self):
        self.cpu = clamp(psutil.cpu_percent(interval=None),0,100)
        self.ram = clamp(psutil.virtual_memory().percent,0,100)
        self.temp = clamp(_cpu_temp(), 0, 120)
        self.hcpu.append(self.cpu)
        self.hram.append(self.ram)
        self.htmp.append(self.temp)

# ---------- DOKUNMATİK ----------
class Touch:
    def __init__(self):
        self.available=False
        self.bus=None
        self.start_x=None
        self.swipe=28  # piksel
        if not _SMBUS_OK: return
        # bulduğu ilk bus'ı kullan
        for b in CAND_BUSES:
            try:
                SMBus(b).read_i2c_block_data(CST816_ADDR, 0x00, 1)
                self.bus = SMBus(b)
                self.available=True
                break
            except Exception:
                continue

    def read_point(self, W,H):
        if not self.available: return None
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 7)
            if (d[1] & 0x0F) == 0:
                self.start_x=None
                return None
            x = ((d[2]&0x0F)<<8) | d[3]
            y = ((d[4]&0x0F)<<8) | d[5]
            # ekrana sığdır
            return (max(0,min(W-1,x)), max(0,min(H-1,y)))
        except Exception:
            return None

    def detect_swipe_lr(self, x):
        if self.start_x is None:
            self.start_x = x; return 0
        dx = x - self.start_x
        if dx <= -self.swipe: self.start_x=None; return -1   # sola kaydır
        if dx >=  self.swipe: self.start_x=None; return  1   # sağa kaydır
        return 0

# ---------- SAYFALAR ----------
def page_temp(img, d, m, C, W, H):
    header(d, C, W, "SICAKLIK")
    # yüzdeye normalize et: 30..90 C aralığı
    t_pct = clamp((m.temp-30)*(100/60),0,100)
    ring(d, 120, 120, 64, t_pct, C, width=14)
    d.text((120,120), f"{m.temp:.1f}°C", font=F18, fill=C["FG"], anchor="mm")
    d.text((120,146), "CPU TEMP", font=F12, fill=C["ACC2"], anchor="mm")
    d.text((12,198), "TEMP HISTORY", font=F12, fill=C["MUTED"])
    spark(d, 12,214, W-24, 52, m.htmp, C["ACC2"], C)

def page_ram(img, d, m, C, W, H):
    header(d, C, W, "RAM")
    ring(d, 120, 120, 64, m.ram, C, width=14)
    vm = psutil.virtual_memory()
    used_mb = (vm.total - vm.available)/1024/1024
    d.text((120,120), f"{m.ram:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    d.text((120,146), f"{used_mb:.0f}/{vm.total/1024/1024:.0f} MB", font=F12, fill=C["ACC1"], anchor="mm")
    d.text((12,198), "RAM HISTORY", font=F12, fill=C["MUTED"])
    spark(d, 12,214, W-24, 52, m.hram, C["ACC1"], C)

def page_cpu(img, d, m, C, W, H):
    header(d, C, W, "CPU")
    ring(d, 120, 120, 64, m.cpu, C, width=14)
    d.text((120,120), f"{m.cpu:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    # per-core barlar
    d.text((12,168), "PER-CORE", font=F12, fill=C["MUTED"])
    y=184
    for idx, p in enumerate(psutil.cpu_percent(percpu=True)[:6]):
        d.text((12,y), f"C{idx}", font=F12, fill=C["FG"])
        bar(d, 36, y-2, W-48, 10, p, C)
        y += 16
        if y > H-18: break

def page_all(img, d, m, C, W, H):
    header(d, C, W, "GENEL")
    # üstte üç halka
    ring(d, 52, 96, 34, m.cpu, C, width=10)
    d.text((52,96), f"{m.cpu:.0f}%", font=F12, fill=C["FG"], anchor="mm"); d.text((52,114), "CPU", font=F11, fill=C["ACC1"], anchor="mm")
    ring(d, 120,96, 34, m.ram, C, width=10)
    d.text((120,96), f"{m.ram:.0f}%", font=F12, fill=C["FG"], anchor="mm"); d.text((120,114), "RAM", font=F11, fill=C["ACC1"], anchor="mm")
    t_pct = clamp((m.temp-30)*(100/60),0,100)
    ring(d, 188,96, 34, t_pct, C, width=10)
    d.text((188,96), f"{m.temp:.0f}°", font=F12, fill=C["FG"], anchor="mm"); d.text((188,114), "TEMP", font=F11, fill=C["ACC2"], anchor="mm")
    # altta tarihçeler
    d.text((12,136), "CPU", font=F12, fill=C["MUTED"]); spark(d, 48,132, W-60, 28, m.hcpu, C["ACC1"], C)
    d.text((12,168), "RAM", font=F12, fill=C["MUTED"]); spark(d, 48,164, W-60, 28, m.hram, C["ACC1"], C)
    d.text((12,200), "TEMP",font=F12, fill=C["MUTED"]); spark(d, 48,196, W-60, 28, m.htmp, C["ACC2"], C)

PAGES = [page_temp, page_ram, page_cpu, page_all]

# ---------- UYGULAMA ----------
def ease_out_cubic(t): return 1 - (1 - t) ** 3

class App:
    def __init__(self):
        self.disp = LCD_1inch69(); self.disp.Init()
        try: self.disp.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.disp.width, self.disp.height

        self.theme_dark=True; self.C=DARK
        self.metrics=Metrics()
        for _ in range(4):
            self.metrics.update(); time.sleep(0.1)

        self.touch=Touch()
        self.cur=0; self.tgt=0; self.anim=1.0
        self.running=True
        threading.Thread(target=self._metrics_loop, daemon=True).start()

    def _metrics_loop(self):
        while self.running:
            self.metrics.update()
            time.sleep(0.5)

    def _render(self, idx):
        img=Image.new("RGB",(self.W,self.H), self.C["BG"])
        d=ImageDraw.Draw(img)
        # grid
        for gy in range(0,self.H,28):
            d.line((0,gy,self.W,gy), fill=self.C["GRID"])
        # sağ üst: tema toggle ipucu
        d.text((self.W-16,8), "◑", font=F12, fill=self.C["MUTED"], anchor="ra")
        PAGES[idx](img,d,self.metrics,self.C,self.W,self.H)
        return img

    def _switch(self, idx):
        if idx==self.cur: return
        self.tgt=idx; self.anim=0.0

    def _toggle_theme(self):
        self.theme_dark = not self.theme_dark
        self.C = DARK if self.theme_dark else LIGHT

    def _handle_touch(self):
        pt = self.touch.read_point(self.W,self.H)
        if not pt: return
        x,y = pt
        # sağ üst dokun: tema
        if x>self.W-40 and y<40:
            self._toggle_theme(); time.sleep(0.2); return
        s = self.touch.detect_swipe_lr(x)
        if s==-1: self._switch((self.cur+1) % len(PAGES))   # sola kaydır → ileri
        elif s==1: self._switch((self.cur-1) % len(PAGES))  # sağa kaydır → geri

    def loop(self):
        fps=30; dt=1.0/fps; last=time.time()
        while True:
            now=time.time()
            if now-last<dt: time.sleep(dt-(now-last))
            last=now
            if self.touch.available: self._handle_touch()
            if self.anim<1.0:
                self.anim=min(1.0,self.anim+0.12)
                t = ease_out_cubic(self.anim)
                # yatay animasyon (sağa/sola)
                dirn = 1 if ((self.tgt - self.cur) % len(PAGES))==1 else -1
                off=int(( -dirn*self.W ) * t)
                cur=self._render(self.cur); nxt=self._render(self.tgt)
                frame=Image.new("RGB",(self.W,self.H), self.C["BG"])
                frame.paste(cur,(off,0)); frame.paste(nxt,(off+dirn*self.W,0))
                self.disp.ShowImage(frame)
                if self.anim>=1.0: self.cur=self.tgt
            else:
                self.disp.ShowImage(self._render(self.cur))

if __name__=="__main__":
    try:
        App().loop()
    except KeyboardInterrupt:
        pass
