#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ÇALIŞAN panelin minimal+fan sürümü: init/loop ellemiyorum.
# Sadece fan okumayı ekledim ve summary sayfasına bir halka daha koydum.

import os, sys, time, math, threading, subprocess, argparse
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import psutil

# ---------- ÜRETİCİ SÜRÜCÜ ----------
from lib.LCD_1inch69 import LCD_1inch69

# ---------- Dokunmatik (aynen bıraktım) ----------
try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15

# ---------- Tema ----------
DARK = dict(
    BG=(5,8,12), FG=(235,235,235), ACC1=(120,180,255), ACC2=(255,120,180),
    OK=(90,200,120), WARN=(255,170,0), BAD=(255,80,80),
    GRID=(25,30,36), BARBG=(22,26,32)
)
LIGHT = dict(
    BG=(240,242,246), FG=(20,22,28), ACC1=(30,90,220), ACC2=(200,60,120),
    OK=(30,170,90), WARN=(230,140,0), BAD=(210,40,40),
    GRID=(210,214,220), BARBG=(210,214,220)
)

# ---------- Font ----------
def load_font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F12, F14, F16, F18, F22 = (load_font(s) for s in (12,14,16,18,22))

# ---------- Yardımcılar ----------
def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v): v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def pick_color(p, C):
    p = clamp(p, 0, 100)
    return C["OK"] if p < 70 else (C["WARN"] if p < 85 else C["BAD"])

def bar(d, x,y,w,h,pct,C):
    pct = clamp(pct,0,100)
    d.rounded_rectangle([x,y,x+w,y+h], radius=6, fill=C["BARBG"])
    d.rounded_rectangle([x,y,x+int(w*pct/100.0),y+h], radius=6, fill=pick_color(pct,C))

def ring(d, cx, cy, r, pct, C, width=10):
    pct = clamp(pct,0,100)/100.0
    box=[cx-r, cy-r, cx+r, cy+r]
    d.arc(box, start=135, end=405, width=width, fill=C["BARBG"])
    d.arc(box, start=135, end=135+int(270*pct), width=width, fill=pick_color(pct*100,C))

def sparkline(d, x,y,w,h,series,color,C,grid=True):
    if grid:
        for gy in range(3):
            gy_y = y + int(gy*h/3)
            d.line((x, gy_y, x+w, gy_y), fill=C["GRID"])
    vals=[]
    for v in list(series):
        try:
            vv=float(v)
            if math.isfinite(vv): vals.append(vv)
        except: pass
    if len(vals) < 2 or max(vals) == min(vals):
        py = y + h//2
        d.line((x,py,x+w,py), fill=color, width=2)
        return
    n=len(vals); mn=min(vals); mx=max(vals); prev=None
    for i,v in enumerate(vals):
        t=(v-mn)/(mx-mn)
        px = x + int(i*(w-1)/max(1,n-1))
        py = y + h - 1 - int(t*(h-1))
        if prev: d.line((prev[0],prev[1],px,py), fill=color, width=2)
        prev=(px,py)

def get_cmd(*args):
    try:
        out = subprocess.check_output(args, stderr=subprocess.DEVNULL).decode().strip()
        return out
    except Exception:
        return ""

# ---------- FAN Okuyucu (yeni) ----------
class FanReader:
    def __init__(self, log=False):
        self.log = log
        self.fan_input = None
        self.pwm1 = None
        self.cool_cur = None
        self.cool_max = None
        self._discover()

    def _glob(self, root):
        try:
            return [os.path.join(root, x) for x in os.listdir(root)]
        except Exception:
            return []

    def _discover(self):
        # 1) /sys/class/hwmon
        for hw in self._glob("/sys/class/hwmon"):
            for node in self._glob(hw):
                # fan*_input
                for f in self._glob(node):
                    if os.path.basename(f).startswith("fan") and f.endswith("_input"):
                        self.fan_input = f
                        if self.log: print("[fan] fan_input:", f)
                        break
                # pwm1
                p = os.path.join(node, "pwm1")
                if os.path.exists(p):
                    self.pwm1 = p
                    if self.log: print("[fan] pwm1:", p)
            if self.fan_input or self.pwm1:
                return
        # 2) cooling_device
        for cd in self._glob("/sys/class/thermal"):
            if not os.path.basename(cd).startswith("cooling_device"):
                continue
            cur = os.path.join(cd, "cur_state")
            mx  = os.path.join(cd, "max_state")
            if os.path.exists(cur) and os.path.exists(mx):
                self.cool_cur, self.cool_max = cur, mx
                if self.log: print("[fan] cooling_device:", cur, mx)
                return

    def _read_int(self, path):
        try:
            with open(path) as f:
                return int(f.read().strip())
        except Exception:
            return None

    def read(self):
        rpm = None
        pct = None
        if self.fan_input:
            v = self._read_int(self.fan_input)
            if v is not None: rpm = max(0, v)
        if self.pwm1:
            v = self._read_int(self.pwm1)
            if v is not None: pct = clamp((v/255.0)*100.0, 0, 100)
        if pct is None and self.cool_cur and self.cool_max:
            cur = self._read_int(self.cool_cur)
            mx  = self._read_int(self.cool_max)
            if cur is not None and mx not in (None,0):
                pct = clamp((cur/mx)*100.0, 0, 100)
        return rpm, pct

# ---------- Metrikler (çalışan sürümle aynı + fan) ----------
class Metrics:
    def __init__(self, hist_len=90, log=False):
        self.cpu=self.ram=self.disk=self.temp=0.0
        self.up=self.dn=0.0
        self.hcpu=deque(maxlen=hist_len)
        self.hram=deque(maxlen=hist_len)
        self.htmp=deque(maxlen=hist_len)
        self.hup=deque(maxlen=hist_len)
        self.hdn=deque(maxlen=hist_len)
        self.last_net = psutil.net_io_counters()
        self.fan = FanReader(log=log)
        self.fan_rpm = 0
        self.fan_pct = 0.0

    def _temp(self):
        try:
            out = subprocess.check_output(["vcgencmd","measure_temp"]).decode()
            return float(out.split("=")[1].split("'")[0])
        except Exception:
            try:
                return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000.0
            except Exception:
                return 0.0

    def update(self):
        self.cpu = clamp(psutil.cpu_percent(interval=None), 0, 100)
        self.ram = clamp(psutil.virtual_memory().percent, 0, 100)
        self.disk = clamp(psutil.disk_usage("/").percent, 0, 100)
        self.temp = clamp(self._temp(), 0, 120)

        now = psutil.net_io_counters()
        self.up = max(0.0, (now.bytes_sent - self.last_net.bytes_sent)/1024.0)
        self.dn = max(0.0, (now.bytes_recv - self.last_net.bytes_recv)/1024.0)
        self.last_net = now

        rpm, pct = self.fan.read()
        if rpm is not None: self.fan_rpm = int(rpm)
        if pct is not None: self.fan_pct = clamp(pct, 0, 100)

        self.hcpu.append(self.cpu); self.hram.append(self.ram); self.htmp.append(self.temp)
        self.hup.append(self.up);   self.hdn.append(self.dn)

# ---------- Dokunmatik (çalışan sürümle aynı) ----------
class Touch:
    def __init__(self):
        self.available = SMBUS_OK
        self.bus = None
        if self.available:
            try:
                self.bus = SMBus(I2C_BUS)
                self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 1)
            except Exception:
                self.available = False
                self.bus = None
        self.start_y=None
        self.swipe_thresh=30

    def read_point(self, W, H):
        if not self.available: return None
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 7)
            event = d[1] & 0x0F
            if event == 0:
                self.start_y=None
                return None
            x = ((d[2]&0x0F)<<8) | d[3]
            y = ((d[4]&0x0F)<<8) | d[5]
            return (max(0,min(W-1,x)), max(0,min(H-1,y)))
        except Exception:
            return None

    def detect_swipe(self, y):
        if self.start_y is None:
            self.start_y=y; return 0
        dy = y - self.start_y
        if dy <= -self.swipe_thresh:
            self.start_y=None; return -1
        if dy >=  self.swipe_thresh:
            self.start_y=None; return  1
        return 0

# ---------- Sayfalar (summary’ye fan eklendi) ----------
def page_summary(img, d, m, C, W, H):
    d.text((12,10), "SYSTEM", font=F22, fill=C["FG"])
    d.text((W-12,10), time.strftime("%H:%M"), font=F18, fill=C["ACC1"], anchor="ra")

    ring(d, 56, 90, 40, m.cpu, C); d.text((56,90), f"{m.cpu:0.0f}%", font=F14, fill=C["FG"], anchor="mm"); d.text((56,112),"CPU", font=F12, fill=C["ACC1"], anchor="mm")
    ring(d, 184,90, 40, m.ram, C); d.text((184,90),f"{m.ram:0.0f}%",font=F14,fill=C["FG"], anchor="mm"); d.text((184,112),"RAM", font=F12, fill=C["ACC1"], anchor="mm")

    t_pct = clamp((m.temp-30)*(100.0/60.0), 0, 100)
    ring(d, 120,160, 46, t_pct, C); d.text((120,160), f"{m.temp:0.1f}°C", font=F14, fill=C["FG"], anchor="mm"); d.text((120,184),"CPU TEMP", font=F12, fill=C["ACC2"], anchor="mm")

    # FAN: yüzde + RPM (varsa)
    ring(d, 120, 228, 24, m.fan_pct if m.fan_pct else 0, C, width=8)
    fan_txt = f"FAN {m.fan_pct:0.0f}%"
    if m.fan_rpm: fan_txt += f"  {m.fan_rpm} RPM"
    d.text((120,228), fan_txt, font=F12, fill=C["FG"], anchor="mm")

def page_disk_net(img, d, m, C, W, H):
    d.text((12,10), "DISK & NET", font=F22, fill=C["FG"])
    d.text((12,50), f"DISK {m.disk:0.0f}%", font=F18, fill=C["FG"]); bar(d, 12,70, W-24,14, m.disk, C)
    d.text((12,100), f"UP {m.up:0.0f} KB/s", font=F16, fill=C["ACC1"]); sparkline(d, 12,118, W-24,30, m.hup, C["ACC1"], C)
    d.text((12,156), f"DN {m.dn:0.0f} KB/s", font=F16, fill=C["ACC2"]); sparkline(d, 12,174, W-24,30, m.hdn, C["ACC2"], C)
    d.text((W-12,H-10), "yukarı/aşağı kaydır", font=F12, fill=(150,150,150), anchor="rs")

def page_processes(img, d, m, C, W, H):
    d.text((12,10), "TOP PROCESSES", font=F22, fill=C["FG"])
    procs=[]
    for p in psutil.process_iter(attrs=["pid","name","cpu_percent","memory_percent"]):
        try: procs.append(p.info)
        except Exception: pass
    procs.sort(key=lambda x: x.get("cpu_percent",0.0), reverse=True)
    y=44
    for row in procs[:6]:
        name=str(row.get("name",""))[:14]
        cpu=clamp(row.get("cpu_percent",0.0),0,100)
        mem=clamp(row.get("memory_percent",0.0),0,100)
        d.text((12,y), name, font=F14, fill=C["FG"])
        d.text((W-12,y), f"{cpu:0.0f}% CPU  {mem:0.0f}% MEM", font=F12, fill=C["ACC1"], anchor="ra")
        y+=28

def page_system(img, d, m, C, W, H):
    d.text((12,10), "SYSTEM INFO", font=F22, fill=C["FG"])
    upt = time.time() - psutil.boot_time()
    dys, r = divmod(int(upt), 86400); hrs, r = divmod(r, 3600); mins,_ = divmod(r, 60)
    try: ip = subprocess.check_output(["hostname","-I"]).decode().strip().split()[0]
    except Exception: ip = "0.0.0.0"
    try:
        arm = subprocess.check_output(["vcgencmd","measure_clock","arm"]).decode().split("=")[1]
        arm = int(arm)/1_000_000
    except Exception:
        arm = psutil.cpu_freq().current if psutil.cpu_freq() else 0
    lines = [
        f"Uptime : {dys}g {hrs}s {mins}d",
        f"IP     : {ip}",
        f"CPU Hz : {arm:0.0f} MHz",
        f"Fan    : {m.fan_pct:0.0f}%{('  '+str(m.fan_rpm)+' RPM') if m.fan_rpm else ''}",
        f"Cores  : {psutil.cpu_count()}",
        f"Python : {'.'.join(map(str, sys.version_info[:3]))}",
    ]
    y=46
    for t in lines:
        d.text((12,y), t, font=F16, fill=C["FG"]); y += 24
    d.text((12,y), "Mounts:", font=F16, fill=C["FG"]); y+=8
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            y+=18
            d.text((12,y), f"{part.mountpoint} {u.percent:0.0f}%", font=F14, fill=C["FG"])
            bar(d, 112, y-2, W-124, 10, u.percent, C)
            if y > H-24: break
        except Exception:
            continue

PAGES = [page_summary, page_disk_net, page_processes, page_system]

# ---------- App (çalışan sürümle birebir) ----------
class App:
    def __init__(self, log=False):
        self.disp = LCD_1inch69()
        self.disp.Init()
        try: self.disp.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.disp.width, self.disp.height

        self.theme_dark = True
        self.C = DARK
        self.metrics = Metrics(log=log)
        for _ in range(4):
            self.metrics.update(); time.sleep(0.1)

        self.touch = Touch()

        self.cur = 0
        self.tgt = 0
        self.anim = 1.0

        self.running = True
        threading.Thread(target=self._metrics_loop, daemon=True).start()

    def _metrics_loop(self):
        while self.running:
            self.metrics.update()
            time.sleep(0.5)

    def _render_page(self, idx):
        img = Image.new("RGB", (self.W, self.H), self.C["BG"])
        d = ImageDraw.Draw(img)
        for gy in range(0, self.H, 28):
            d.line((0, gy, self.W, gy), fill=self.C["GRID"])
        PAGES[idx](img, d, self.metrics, self.C, self.W, self.H)
        return img

    def _switch(self, idx):
        if idx == self.cur: return
        self.tgt = idx
        self.anim = 0.0

    def _handle_touch(self):
        pt = self.touch.read_point(self.W, self.H)
        if not pt: return
        x,y = pt
        # Sağ üst köşe: tema değiştir
        if x > self.W-52 and y < 40:
            self.theme_dark = not self.theme_dark
            self.C = DARK if self.theme_dark else LIGHT
            time.sleep(0.2)
            return
        swipe = self.touch.detect_swipe(y)
        if swipe == -1: self._switch((self.cur-1) % len(PAGES))
        elif swipe == 1: self._switch((self.cur+1) % len(PAGES))

    def loop(self):
        fps=30.0; dt=1.0/fps; last=time.time()
        while True:
            now=time.time()
            if now-last < dt: time.sleep(dt-(now-last))
            last=now

            if self.touch.available: self._handle_touch()

            if self.anim < 1.0:
                self.anim = min(1.0, self.anim + 0.12)
                t = 1 - (1 - self.anim)**3
                dirn = 1 if (self.tgt > self.cur or (self.cur==len(PAGES)-1 and self.tgt==0)) else -1
                off = int((-dirn*self.H) * t)
                cur_img = self._render_page(self.cur)
                tgt_img = self._render_page(self.tgt)
                frame = Image.new("RGB", (self.W, self.H), self.C["BG"])
                frame.paste(cur_img, (0, off))
                frame.paste(tgt_img, (0, off + dirn*self.H))
                self.disp.ShowImage(frame)
                if self.anim >= 1.0:
                    self.cur = self.tgt
            else:
                img = self._render_page(self.cur)
                self.disp.ShowImage(img)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true", help="fan pathlarını ve hataları yazdır")
    args = ap.parse_args()
    if args.log:
        print("[sysmon] starting with logs on")
    App(log=args.log).loop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
