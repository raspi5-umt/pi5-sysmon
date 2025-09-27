#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Raspberry Pi 5 • 1.69" (240x280) Dashboard
# - 6 sayfa (2x3 grid): Thermal/Fan • RAM • CPU • Disk • Network • Processes
# - Sağa/sola/yukarı/aşağı swipe → sayfa değiştir
# - Sağ üst dokun → tema (Dark/Light)

import os, sys, time, math, threading, socket, subprocess
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import psutil

# --------- LCD SÜRÜCÜ ---------
from lib.LCD_1inch69 import LCD_1inch69

# --------- TOUCH ----------
try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

CST816_ADDR = 0x15
CAND_BUSES = [1, 13, 14]

# --------- TEMA ----------
DARK = dict(
    BG=(8,12,18), FG=(235,238,243), ACC1=(120,180,255), ACC2=(255,120,180),
    OK=(80,210,120), WARN=(255,180,60), BAD=(255,80,80),
    GRID=(24,30,38), BARBG=(18,22,28), MUTED=(150,155,165)
)
LIGHT = dict(
    BG=(242,246,250), FG=(22,26,32), ACC1=(40,110,240), ACC2=(200,70,120),
    OK=(60,170,90), WARN=(230,150,40), BAD=(210,60,60),
    GRID=(210,216,224), BARBG=(210,216,224), MUTED=(90,95,105)
)

# --------- FONT ----------
def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F10, F12, F14, F16, F18, F22 = (load_font(s) for s in (10,12,14,16,18,22))

# --------- YARDIMCILAR ----------
def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def pick_color(p, C):
    p = clamp(p,0,100)
    return C["OK"] if p < 70 else (C["WARN"] if p < 85 else C["BAD"])

def bar(d, x,y,w,h,pct,C):
    pct = clamp(pct,0,100)
    d.rectangle([x,y,x+w,y+h], fill=C["BARBG"])
    d.rectangle([x,y,x+int(w*pct/100.0),y+h], fill=pick_color(pct,C))

def ring(d, cx, cy, r, pct, C, width=12):
    pct = clamp(pct,0,100)/100.0
    box=[cx-r, cy-r, cx+r, cy+r]
    d.arc(box, 135, 405, width=width, fill=C["BARBG"])
    d.arc(box, 135, 135+int(270*pct), width=width, fill=pick_color(pct*100, C))

def header(d, C, W, title):
    d.text((12,8), title, font=F22, fill=C["FG"])
    d.text((W-12,8), time.strftime("%H:%M"), font=F16, fill=C["ACC1"], anchor="ra")

def vcgencmd(*args, default=""):
    try:
        return subprocess.check_output(["vcgencmd", *args], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return default

# --------- SİSTEM OKUYUCULAR ----------
def cpu_temp():
    s = vcgencmd("measure_temp")
    if s:
        try: return float(s.split("=")[1].split("'")[0])
        except Exception: pass
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000.0
    except Exception:
        return 0.0

def cpu_freq_mhz():
    s = vcgencmd("measure_clock","arm")
    try: return int(int(s.split("=")[1])/1_000_000)
    except Exception:
        f = psutil.cpu_freq()
        return int(f.current if f else 0)

def fan_read():
    rpm=None; pct=None
    for root,_,files in os.walk("/sys/class/hwmon"):
        for f in files:
            p=os.path.join(root,f)
            if f.startswith("fan") and f.endswith("_input"):
                try: rpm = int(open(p).read().strip())
                except: pass
            if f=="pwm1":
                try:
                    v=int(open(p).read().strip())
                    pct = clamp((v/255.0)*100.0,0,100)
                except: pass
    return rpm,pct

def ip_primary():
    try:
        return subprocess.check_output(["hostname","-I"], stderr=subprocess.DEVNULL).decode().strip().split()[0]
    except Exception: return "0.0.0.0"

# --------- METRİKLER ----------
class Metrics:
    def __init__(self, hist_len=120):
        self.cpu=0.0; self.ram=0.0; self.temp=0.0
        self.disk_root=0.0
        self.net_up=0.0; self.net_dn=0.0
        self.fan_rpm=0; self.fan_pct=0.0

        self.hcpu=deque(maxlen=hist_len)
        self.hram=deque(maxlen=hist_len)
        self.htmp=deque(maxlen=hist_len)

    def update(self):
        self.cpu = clamp(psutil.cpu_percent(interval=None),0,100)
        self.ram = clamp(psutil.virtual_memory().percent,0,100)
        self.temp = clamp(cpu_temp(),0,120)
        self.disk_root = clamp(psutil.disk_usage("/").percent,0,100)

        rpm,pct = fan_read()
        if rpm is not None: self.fan_rpm = int(rpm)
        if pct is not None: self.fan_pct = clamp(pct,0,100)

        self.hcpu.append(self.cpu)
        self.hram.append(self.ram)
        self.htmp.append(self.temp)

# --------- DOKUNMATİK ----------
class Touch:
    def __init__(self):
        self.available=False; self.bus=None
        self.start=None; self.th=24
        if not SMBUS_OK: return
        for b in CAND_BUSES:
            try:
                SMBus(b).read_i2c_block_data(CST816_ADDR,0x00,1)
                self.bus=SMBus(b); self.available=True; break
            except Exception:
                continue

    def _point(self, W,H):
        if not self.available: return None
        try:
            d=self.bus.read_i2c_block_data(CST816_ADDR,0x00,7)
            if (d[1]&0x0F)==0: return None
            x=((d[2]&0x0F)<<8)|d[3]; y=((d[4]&0x0F)<<8)|d[5]
            return (max(0,min(W-1,x)), max(0,min(H-1,y)))
        except Exception: return None

    def read_gesture(self, W,H):
        pt=self._point(W,H)
        if not pt: self.start=None; return 0, None
        if self.start is None:
            self.start=pt; return 0, pt
        x0,y0=self.start; x,y=pt
        dx=x-x0; dy=y-y0
        if abs(dx)<self.th and abs(dy)<self.th:
            return 0, pt
        self.start=None
        if abs(dx)>=abs(dy):
            return (1 if dx>0 else -1), pt    # sağ/sol
        else:
            return (2 if dy>0 else -2), pt    # aşağı/yukarı

# --------- SAYFALAR ----------
def page_thermal(img,d,m,C,W,H):
    header(d,C,W,"THERMAL")
    t_pct = clamp((m.temp-30)*(100/60),0,100)
    ring(d, 120, 108, 64, t_pct, C, width=14)
    d.text((120,108), f"{m.temp:.1f}°C", font=F18, fill=C["FG"], anchor="mm")
    ring(d, 120, 196, 28, m.fan_pct if m.fan_pct else 0, C, width=10)
    txt = "FAN " + (f"{m.fan_pct:.0f}%" if m.fan_pct else "N/A")
    if m.fan_rpm: txt += f"  {m.fan_rpm} RPM"
    d.text((120,196), txt, font=F12, fill=C["FG"], anchor="mm")

def page_ram(img,d,m,C,W,H):
    header(d,C,W,"RAM")
    ring(d, 120, 110, 66, m.ram, C, width=14)
    vm = psutil.virtual_memory()
    used = (vm.total - vm.available)/1024/1024
    d.text((120,110), f"{m.ram:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    d.text((120,136), f"{used:.0f}/{vm.total/1024/1024:.0f} MB", font=F12, fill=C["ACC1"], anchor="mm")

def page_cpu(img,d,m,C,W,H):
    header(d,C,W,"CPU")
    ring(d, 120, 96, 56, m.cpu, C, width=12)
    d.text((120,96), f"{m.cpu:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    la1,la5,la15 = os.getloadavg()
    d.text((12,140), f"Load {la1:.2f} {la5:.2f} {la15:.2f}", font=F12, fill=C["FG"])
    d.text((12,160), f"Freq {cpu_freq_mhz()} MHz", font=F12, fill=C["FG"])

def page_disk(img,d,m,C,W,H):
    header(d,C,W,"DISK")
    d.text((12,44), f"/ usage {m.disk_root:.0f}%", font=F16, fill=C["FG"])
    bar(d, 12,62, W-24,12, m.disk_root, C)

def page_net(img,d,m,C,W,H):
    header(d,C,W,"NETWORK")
    ip = ip_primary()
    d.text((12,44), f"IP: {ip}", font=F16, fill=C["FG"])

def page_proc(img,d,m,C,W,H):
    header(d,C,W,"PROCESSES")
    procs=[]
    for p in psutil.process_iter(attrs=["pid","name","cpu_percent","memory_percent"]):
        try: procs.append(p.info)
        except Exception: pass
    procs.sort(key=lambda x:x.get("cpu_percent",0.0), reverse=True)
    y=44
    for row in procs[:6]:
        name=str(row.get("name",""))[:14]
        cpu=clamp(row.get("cpu_percent",0.0),0,100)
        mem=clamp(row.get("memory_percent",0.0),0,100)
        d.text((12,y), name, font=F12, fill=C["FG"])
        d.text((W-12,y), f"{cpu:.0f}% {mem:.0f}%", font=F12, fill=C["ACC1"], anchor="ra")
        y+=16

PAGES = [
    [page_thermal, page_ram, page_cpu],
    [page_disk,    page_net, page_proc],
]

# --------- APP ----------
def ease_out_cubic(t): return 1 - (1 - t) ** 3

class App:
    def __init__(self):
        self.disp=LCD_1inch69(); self.disp.Init()
        try: self.disp.bl_DutyCycle(100)
        except Exception: pass
        self.W,self.H=self.disp.width,self.disp.height

        self.metrics=Metrics()
        self.touch=Touch()
        self.theme_dark=True; self.C=DARK

        self.row=0; self.col=0
        self.t_row=0; self.t_col=0
        self.anim=1.0; self.move_dir="X"

        threading.Thread(target=self._metrics_loop, daemon=True).start()

    def _metrics_loop(self):
        while True:
            self.metrics.update()
            time.sleep(0.5)

    def _render(self, r, c):
        img=Image.new("RGB",(self.W,self.H), self.C["BG"])
        d=ImageDraw.Draw(img)
        for gy in range(0,self.H,28):
            d.line((0,gy,self.W,gy), fill=self.C["GRID"])
        d.text((self.W-16,8), "◑", font=F12, fill=self.C["MUTED"], anchor="ra")
        PAGES[r][c](img,d,self.metrics,self.C,self.W,self.H)
        return img

    def _toggle_theme(self):
        self.theme_dark=not self.theme_dark
        self.C = DARK if self.theme_dark else LIGHT

    def _switch(self, move):
        R, Cn = len(PAGES), len(PAGES[0])
        r, c = self.row, self.col
        if move=="L": c=(c-1)%Cn
        elif move=="R": c=(c+1)%Cn
        elif move=="U": r=(r-1)%R
        elif move=="D": r=(r+1)%R
        else: return
        self.t_row,self.t_col=r,c
        self.move_dir=move
        self.anim=0.0

    def _handle_touch(self):
        code, pt = self.touch.read_gesture(self.W,self.H)
        if not pt: return
        x,y = pt
        if x>self.W-40 and y<40:
            self._toggle_theme(); time.sleep(0.2); return
        if code==-1: self._switch("L")
        elif code==1: self._switch("R")
        elif code==-2: self._switch("U")
        elif code==2: self._switch("D")

    def loop(self):
        fps=30; dt=1.0/fps; last=time.time()
        while True:
            now=time.time()
            if now-last<dt: time.sleep(dt-(now-last))
            last=now

            if self.touch.available: self._handle_touch()

            if self.anim<1.0:
                self.anim=min(1.0, self.anim+0.12)
                t=ease_out_cubic(self.anim)
                cur=self._render(self.row,self.col)
                nxt=self._render(self.t_row,self.t_col)
                frame=Image.new("RGB",(self.W,self.H), self.C["BG"])
                if self.move_dir in ("L","R"):
                    dirn = -1 if self.move_dir=="L" else 1
                    off=int((-dirn*self.W)*t)
                    frame.paste(cur,(off,0)); frame.paste(nxt,(off+dirn*self.W,0))
                else:
                    dirn = -1 if self.move_dir=="U" else 1
                    off=int((-dirn*self.H)*t)
                    frame.paste(cur,(0,off)); frame.paste(nxt,(0,off+dirn*self.H))
                self.disp.ShowImage(frame)
                if self.anim>=1.0:
                    self.row,self.col=self.t_row,self.t_col
            else:
                self.disp.ShowImage(self._render(self.row,self.col))

if __name__=="__main__":
    try:
        App().loop()
    except KeyboardInterrupt:
        pass
