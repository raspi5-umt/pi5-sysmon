#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Raspberry Pi 5 • 1.69" (240x280) ST7789 • Temiz, toleranslı dashboard
# - Görüntü sürücüsü: lib/LCD_1inch69.py
# - Dokunmatik: CST816S @ 0x15 (bus oto: 1,13,14)
# - 6 sayfa (2x3 grid): Thermal/Fan • RAM • CPU • Disk • Network • Processes
# - Sağa/sola/yukarı/aşağı swipe → sayfa değiştir, sağ üst dokun → tema
# - Panik yok: tüm metrikler try/except ile güvenli, NaN/inf filtresi var

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
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, sz)
    return ImageFont.load_default()
F10, F11, F12, F14, F16, F18, F22 = (load_font(s) for s in (10,11,12,14,16,18,22))

# --------- YARDIMCILAR ----------
def clamp(v, lo, hi):
    try:
        v=float(v)
        if math.isnan(v) or math.isinf(v): v=0.0
    except Exception:
        v=0.0
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
    d.arc(box, 135, 405, width=width, fill=C["BARBG"])
    d.arc(box, 135, 135+int(270*pct), width=width, fill=pick_color(pct*100, C))

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
        py=y+h//2; d.line((x,py,x+w,py), fill=color, width=2); return
    n=len(vals); mn=min(vals); mx=max(vals); prev=None
    for i,v in enumerate(vals):
        t=(v-mn)/(mx-mn) if mx>mn else 0
        px = x + int(i*(w-1)/max(1,n-1))
        py = y + h - 1 - int(t*(h-1))
        if prev: d.line((prev[0],prev[1],px,py), fill=color, width=2)
        prev=(px,py)

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

def gpu_freq_mhz():
    s = vcgencmd("measure_clock","core")
    try: return int(int(s.split("=")[1])/1_000_000)
    except Exception: return 0

def throttled_flags():
    s = vcgencmd("get_throttled")
    try: val = int(s.split("=")[1],16)
    except Exception: return []
    flags=[]
    def bit(b): return (val & (1<<b)) != 0
    if bit(0): flags.append("UV")
    if bit(1): flags.append("CAP")
    if bit(2): flags.append("THR")
    if bit(3): flags.append("TMP")
    return flags

def fan_read():
    # RPM ve/veya yüzde için birkaç yol; ne bulunursa onu dön
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
    # thermal cooling_device fallback
    if pct is None:
        for root,_,files in os.walk("/sys/class/thermal"):
            if "cooling_device" not in root: continue
            try:
                cur=int(open(os.path.join(root,"cur_state")).read().strip())
                mx =int(open(os.path.join(root,"max_state")).read().strip())
                if mx>0: pct = clamp((cur/mx)*100.0,0,100)
                break
            except: pass
    return rpm, pct

def ip_primary():
    try:
        return subprocess.check_output(["hostname","-I"], stderr=subprocess.DEVNULL).decode().strip().split()[0]
    except Exception: return "0.0.0.0"

def net_connected(timeout=0.5):
    try:
        s=socket.create_connection(("1.1.1.1",53), timeout); s.close(); return True
    except Exception: return False

# psutil bazı platformlarda alan adlarını farklı verebilir diye güvenli fark
def diff_disk_io(prev, now):
    """KB/s benzeri fark döndürür. Alan adları 'read_bytes' / 'write_bytes' olmalı.
       Bazı psutil sürümlerinde 'written_bytes' yoktur; bu yüzden getattr ile alınır."""
    if not prev or not now:
        return 0.0, 0.0
    rb_prev = getattr(prev, "read_bytes", None)
    wb_prev = getattr(prev, "write_bytes", getattr(prev, "written_bytes", None))
    rb_now  = getattr(now,  "read_bytes", None)
    wb_now  = getattr(now,  "write_bytes", getattr(now,  "written_bytes", None))
    if None in (rb_prev, wb_prev, rb_now, wb_now):
        return 0.0, 0.0
    return max(0.0, (rb_now - rb_prev)/1024.0), max(0.0, (wb_now - wb_prev)/1024.0)

# --------- METRİKLER ----------
class Metrics:
    def __init__(self, hist_len=120):
        self.cpu=0.0; self.ram=0.0; self.temp=0.0
        self.disk_root=0.0
        self.net_up=0.0; self.net_dn=0.0
        self.disk_r_kbs=0.0; self.disk_w_kbs=0.0
        self.fan_rpm=0; self.fan_pct=0.0

        self.hcpu=deque(maxlen=hist_len)
        self.hram=deque(maxlen=hist_len)
        self.htmp=deque(maxlen=hist_len)
        self.hup=deque(maxlen=hist_len)
        self.hdn=deque(maxlen=hist_len)

        self._last_net = psutil.net_io_counters()
        self._last_disk = psutil.disk_io_counters() if hasattr(psutil, "disk_io_counters") else None

    def update(self):
        self.cpu = clamp(psutil.cpu_percent(interval=None),0,100)
        self.ram = clamp(psutil.virtual_memory().percent,0,100)
        self.temp = clamp(cpu_temp(),0,120)
        self.disk_root = clamp(psutil.disk_usage("/").percent,0,100)

        now_net = psutil.net_io_counters()
        self.net_up = max(0.0, (now_net.bytes_sent - self._last_net.bytes_sent)/1024.0)
        self.net_dn = max(0.0, (now_net.bytes_recv - self._last_net.bytes_recv)/1024.0)
        self._last_net = now_net

        if self._last_disk:
            now_disk = psutil.disk_io_counters()
            r,w = diff_disk_io(self._last_disk, now_disk)
            self.disk_r_kbs, self.disk_w_kbs = r, w
            self._last_disk = now_disk

        rpm,pct = fan_read()
        if rpm is not None: self.fan_rpm = int(rpm)
        if pct is not None: self.fan_pct = clamp(pct,0,100)

        self.hcpu.append(self.cpu); self.hram.append(self.ram); self.htmp.append(self.temp)
        self.hup.append(self.net_up); self.hdn.append(self.net_dn)

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
    header(d,C,W,"THERMAL222")
    t_pct = clamp((m.temp-30)*(100/60),0,100)
    ring(d, 120, 108, 64, t_pct, C, width=14)
    d.text((120,108), f"{m.temp:.1f}°C", font=F18, fill=C["FG"], anchor="mm")
    d.text((120,134), "CPU TEMP", font=F12, fill=C["ACC2"], anchor="mm")
    ring(d, 120, 196, 28, m.fan_pct if m.fan_pct else 0, C, width=10)
    txt = "FAN " + (f"{m.fan_pct:.0f}%" if m.fan_pct else "N/A")
    if m.fan_rpm: txt += f"  {m.fan_rpm} RPM"
    d.text((120,196), txt, font=F12, fill=C["FG"], anchor="mm")
    d.text((12,226), f"ARM {cpu_freq_mhz()} MHz  GPU {gpu_freq_mhz()} MHz", font=F12, fill=C["FG"])
    flags = throttled_flags()
    if flags: d.text((12,244), "Flags: "+" ".join(flags), font=F12, fill=C["WARN"])

def page_ram(img,d,m,C,W,H):
    header(d,C,W,"RAM")
    vm = psutil.virtual_memory(); sm = psutil.swap_memory()
    ring(d, 120, 110, 66, m.ram, C, width=14)
    used = (vm.total - vm.available)/1024/1024
    d.text((120,110), f"{m.ram:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    d.text((120,136), f"{used:.0f}/{vm.total/1024/1024:.0f} MB", font=F12, fill=C["ACC1"], anchor="mm")
    d.text((12,178), f"Swap {sm.percent:.0f}%", font=F12, fill=C["FG"])
    bar(d, 12, 194, W-24, 12, sm.percent, C)
    d.text((12,214), "RAM HISTORY", font=F12, fill=C["MUTED"])
    spark(d, 12, 230, W-24, 36, list(m.hram), C["ACC1"], C)

def page_cpu(img,d,m,C,W,H):
    header(d,C,W,"CPU")
    ring(d, 120, 96, 56, m.cpu, C, width=12)
    d.text((120,96), f"{m.cpu:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    la1,la5,la15 = os.getloadavg()
    d.text((12,130), f"Load {la1:.2f} {la5:.2f} {la15:.2f}", font=F12, fill=C["FG"])
    d.text((12,148), f"Freq {cpu_freq_mhz()} MHz", font=F12, fill=C["FG"])
    d.text((12,170), "Per-core", font=F12, fill=C["MUTED"])
    y=186
    for i,p in enumerate(psutil.cpu_percent(percpu=True)):
        d.text((12,y), f"C{i}", font=F12, fill=C["FG"])
        bar(d, 36, y-2, W-48, 10, p, C)
        y+=16
        if y>H-16: break

def page_disk(img,d,m,C,W,H):
    header(d,C,W,"DISK")
    d.text((12,44), f"/ usage {m.disk_root:.0f}%", font=F16, fill=C["FG"])
    bar(d, 12,62, W-24,12, m.disk_root, C)
    d.text((12,88), f"IO  R {m.disk_r_kbs:.0f} KB/s   W {m.disk_w_kbs:.0f} KB/s", font=F12, fill=C["FG"])
    d.text((12,108), "Mounts:", font=F12, fill=C["MUTED"])
    y=124
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            d.text((12,y), f"{part.mountpoint} {u.percent:.0f}%", font=F12, fill=C["FG"])
            bar(d, 108, y-2, W-120, 10, u.percent, C)
            y+=18
            if y>H-14: break
        except Exception: continue

def page_net(img,d,m,C,W,H):
    header(d,C,W,"NETWORK")
    ip = ip_primary(); online = net_connected()
    d.text((12,44), f"IP: {ip}", font=F16, fill=C["FG"])
    d.text((W-12,44), "ONLINE" if online else "OFFLINE", font=F12, fill=(C["OK"] if online else C["BAD"]), anchor="ra")
    d.text((12,72), f"UP {m.net_up:.0f} KB/s", font=F12, fill=C["ACC1"]); spark(d, 12,88, W-24, 36, list(m.hup), C["ACC1"], C)
    d.text((12,130), f"DN {m.net_dn:.0f} KB/s", font=F12, fill=C["ACC2"]); spark(d, 12,146, W-24, 36, list(m.hdn), C["ACC2"], C)
    y=190
    d.text((12,y), "Interfaces:", font=F12, fill=C["MUTED"]); y+=14
    for name, addrs in psutil.net_if_addrs().items():
        ip4 = next((a.address for a in addrs if getattr(a, "family", None)==socket.AF_INET), None)
        if ip4:
            d.text((12,y), f"{name}: {ip4}", font=F10, fill=C["FG"]); y+=14
            if y>H-10: break

def page_proc(img,d,m,C,W,H):
    header(d,C,W,"PROCESSES")
    procs=[]
    for p in psutil.process_iter(attrs=["pid","name","cpu_percent","memory_percent"]):
        try: procs.append(p.info)
        except Exception: pass
    procs.sort(key=lambda x:x.get("cpu_percent",0.0), reverse=True)
    y=44
    d.text((12,y), "Name", font=F12, fill=C["MUTED"])
    d.text((W-12,y), "CPU  MEM", font=F12, fill=C["MUTED"], anchor="ra"); y+=16
    for row in procs[:8]:
        name=str(row.get("name",""))[:16]
        cpu=clamp(row.get("cpu_percent",0.0),0,100)
        mem=clamp(row.get("memory_percent",0.0),0,100)
        d.text((12,y), name, font=F12, fill=C["FG"])
        d.text((W-12,y), f"{cpu:>3.0f}%  {mem:>3.0f}%", font=F12, fill=C["ACC1"], anchor="ra")
        y+=16
        if y>H-10: break

PAGES = [
    [page_thermal, page_ram, page_cpu],   # satır 0
    [page_disk, page_net, page_proc],     # satır 1
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
    img = Image.new("RGB", (self.W, self.H), self.C["BG"])
    d = ImageDraw.Draw(img)

    for gy in range(0, self.H, 28):
        d.line((0, gy, self.W, gy), fill=self.C["GRID"])

    d.text((self.W-16, 8), "◑", font=F12, fill=self.C["MUTED"], anchor="ra")
    PAGES[r][c](img, d, self.metrics, self.C, self.W, self.H)
    return img

    def _toggle_theme(self):
        self.theme_dark=not self.theme_dark
        self.C = DARK if self.theme_dark else LIGHT

    def _switch(self, move):
    R, Cn = len(PAGES), len(PAGES[0])
    r, c = self.row, self.col
    if move == "L":   # sola
        c = (c - 1) % Cn
    elif move == "R": # sağa
        c = (c + 1) % Cn
    elif move == "U": # yukarı
        r = (r - 1) % R
    elif move == "D": # aşağı
        r = (r + 1) % R
    self.t_row, self.t_col = r, c
    self.move_dir = move
    self.anim = 0.0

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
