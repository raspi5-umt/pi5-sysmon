#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Raspberry Pi 5 • 1.69" ST7789 (240x280) • Çok Yönlü Telemetri Paneli
# - Ekran:   lib/LCD_1inch69.py (üretici sürücüsü)
# - Touch:   CST816S @ 0x15 (I2C bus otomatik bulur: 1,13,14)
# - Sayfalar (2x3 grid, dört yöne kaydırma):
#     [0,0] Thermal & Fan
#     [0,1] RAM
#     [0,2] CPU
#     [1,0] Disk
#     [1,1] Network
#     [1,2] Processes
# - Tema: sağ üste dokun → koyu/açık
# - Çizim: halka, bar, sparkline; NumPy yok; NaN güvenliği; pürüzsüz anim

import os, sys, time, math, threading, socket, subprocess
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

CAND_BUSES = [1, 13, 14]
CST816_ADDR = 0x15

# ---------- FAN Okuyucu ----------
class FanReader:
    def __init__(self):
        self.fan_input = None
        self.pwm1 = None
        self.cool_cur = None
        self.cool_max = None
        self._discover()

    def _ls(self, root):
        try:
            return [os.path.join(root, x) for x in os.listdir(root)]
        except Exception:
            return []

    def _discover(self):
        for hw in self._ls("/sys/class/hwmon"):
            for node in self._ls(hw):
                for f in self._ls(node):
                    b = os.path.basename(f)
                    if b.startswith("fan") and b.endswith("_input"):
                        self.fan_input = f
                p = os.path.join(node, "pwm1")
                if os.path.exists(p): self.pwm1 = p
            if self.fan_input or self.pwm1: return
        for cd in self._ls("/sys/class/thermal"):
            if not os.path.basename(cd).startswith("cooling_device"): continue
            cur = os.path.join(cd, "cur_state")
            mx  = os.path.join(cd, "max_state")
            if os.path.exists(cur) and os.path.exists(mx):
                self.cool_cur, self.cool_max = cur, mx
                return

    def _read_int(self, path):
        try:
            with open(path) as f: return int(f.read().strip())
        except Exception:
            return None

    def read(self):
        rpm = None; pct = None
        if self.fan_input:
            v = self._read_int(self.fan_input)
            if v is not None: rpm = max(0, v)
        if self.pwm1:
            v = self._read_int(self.pwm1)
            if v is not None: pct = max(0.0, min(100.0, (v/255.0)*100.0))
        if pct is None and self.cool_cur and self.cool_max:
            cur = self._read_int(self.cool_cur); mx = self._read_int(self.cool_max)
            if cur is not None and mx not in (None,0):
                pct = max(0.0, min(100.0, (cur/mx)*100.0))
        return rpm, pct

# ---------- Tema ----------
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

# ---------- Font ----------
def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
    ):
        if os.path.exists(p): return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F10, F11, F12, F14, F16, F18, F22 = (load_font(s) for s in (10,11,12,14,16,18,22))

# ---------- Çizim yardımcıları ----------
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
            vv=float(v); 
            if math.isfinite(vv): vals.append(vv)
        except: pass
    if len(vals)<2 or max(vals)==min(vals):
        py=y+h//2; d.line((x,py,x+w,py), fill=color, width=2); return
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

# ---------- Sistem yardımcıları ----------
def vcgencmd(*args, default=""):
    try:
        out = subprocess.check_output(["vcgencmd", *args], stderr=subprocess.DEVNULL).decode().strip()
        return out
    except Exception:
        return default

def cpu_temp():
    out = vcgencmd("measure_temp")
    if out:
        try: return float(out.split("=")[1].split("'")[0])
        except Exception: pass
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000.0
    except Exception:
        return 0.0

def get_throttled_flags():
    s = vcgencmd("get_throttled")
    # ör: "throttled=0x0" veya "throttled=0x50005"
    try:
        hx = int(s.split("=")[1], 16)
    except Exception:
        return []
    flags=[]
    def bit(b): return (hx & (1<<b)) != 0
    if bit(0):  flags.append("UV")     # undervoltage
    if bit(1):  flags.append("CAPPED") # arm freq capped
    if bit(2):  flags.append("THROTTLE")
    if bit(3):  flags.append("SOFTTMP")
    if bit(16): flags.append("UV(Prev)")
    if bit(17): flags.append("CAP(Prev)")
    if bit(18): flags.append("THR(Prev)")
    if bit(19): flags.append("TMP(Prev)")
    return flags

def cpu_freq_mhz():
    out = vcgencmd("measure_clock","arm")
    try:
        v = int(out.split("=")[1])/1_000_000
        return int(v)
    except Exception:
        f = psutil.cpu_freq()
        return int(f.current if f else 0)

def gpu_freq_mhz():
    out = vcgencmd("measure_clock","core")
    try:
        v = int(out.split("=")[1])/1_000_000
        return int(v)
    except Exception:
        return 0

def get_ip_primary():
    try:
        ip = subprocess.check_output(["hostname","-I"], stderr=subprocess.DEVNULL).decode().strip().split()[0]
        return ip
    except Exception:
        return "0.0.0.0"

def net_connected(timeout=0.5):
    try:
        sock = socket.create_connection(("1.1.1.1",53), timeout=timeout); sock.close(); return True
    except Exception:
        return False

def wifi_info():
    # SSID ve sinyal
    for cmd in (["iw","dev","wlan0","link"], ["iwconfig","wlan0"]):
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            ssid=None; sig=None
            if "SSID:" in out:
                # iw dev link
                for line in out.splitlines():
                    if "SSID:" in line:
                        ssid=line.split("SSID:")[-1].strip()
                    if "signal:" in line and "dBm" in line:
                        try: sig=int(line.split("signal:")[1].split("dBm")[0].strip())
                        except: pass
            else:
                # iwconfig
                for tok in out.replace("=", " ").replace("/", " ").split():
                    if tok.lower().startswith("essid"):
                        pass
                if "ESSID" in out:
                    try:
                        ssid = out.split('ESSID:"')[1].split('"')[0]
                    except: pass
                if "Signal level" in out:
                    try:
                        sig = int(out.split("Signal level=")[1].split(" dBm")[0])
                    except: pass
            if ssid or sig is not None:
                return ssid or "-", sig
        except Exception:
            continue
    return "-", None

# ---------- Metrics ----------
class Metrics:
    def __init__(self, hist_len=120):
        self.cpu=0.0; self.ram=0.0; self.temp=0.0
        self.disk_root=0.0
        self.net_up=0.0; self.net_dn=0.0
        self.hcpu=deque(maxlen=hist_len)
        self.hram=deque(maxlen=hist_len)
        self.htmp=deque(maxlen=hist_len)
        self.hup=deque(maxlen=hist_len)
        self.hdn=deque(maxlen=hist_len)
        self.last_net = psutil.net_io_counters()
        self.last_disk = psutil.disk_io_counters() if hasattr(psutil, "disk_io_counters") else None
        self.disk_r_kbs = 0.0; self.disk_w_kbs = 0.0
        self.fan = FanReader(); self.fan_rpm=0; self.fan_pct=0.0

    def update(self):
        self.cpu = clamp(psutil.cpu_percent(interval=None),0,100)
        vm = psutil.virtual_memory()
        self.ram = clamp(vm.percent,0,100)
        self.temp = clamp(cpu_temp(), 0, 120)
        self.disk_root = clamp(psutil.disk_usage("/").percent,0,100)

        now = psutil.net_io_counters()
        self.net_up = max(0.0, (now.bytes_sent - self.last_net.bytes_sent)/1024.0)
        self.net_dn = max(0.0, (now.bytes_recv - self.last_net.bytes_recv)/1024.0)
        self.last_net = now

        if self.last_disk:
            nd = psutil.disk_io_counters()
            self.disk_r_kbs = max(0.0, (nd.read_bytes  - self.last_disk.read_bytes)/1024.0)
            self.disk_w_kbs = max(0.0, (nd.written_bytes - self.last_disk.written_bytes)/1024.0)
            self.last_disk = nd

        rpm, pct = self.fan.read()
        if rpm is not None: self.fan_rpm = int(rpm)
        if pct is not None: self.fan_pct = clamp(pct,0,100)

        self.hcpu.append(self.cpu)
        self.hram.append(self.ram)
        self.htmp.append(self.temp)
        self.hup.append(self.net_up)
        self.hdn.append(self.net_dn)

# ---------- Dokunmatik ----------
class Touch:
    def __init__(self):
        self.available=False; self.bus=None
        self.start=None
        self.thresh=24
        if not _SMBUS_OK: return
        for b in CAND_BUSES:
            try:
                SMBus(b).read_i2c_block_data(CST816_ADDR, 0x00, 1)
                self.bus = SMBus(b)
                self.available=True
                break
            except Exception:
                continue

    def _point(self, W,H):
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x00, 7)
            if (d[1] & 0x0F) == 0: return None
            x = ((d[2]&0x0F)<<8) | d[3]
            y = ((d[4]&0x0F)<<8) | d[5]
            return (max(0,min(W-1,x)), max(0,min(H-1,y)))
        except Exception:
            return None

    def read_gesture(self, W,H):
        if not self.available: return 0, None
        pt = self._point(W,H)
        if not pt:
            self.start=None; return 0, None
        if self.start is None:
            self.start = pt; return 0, pt
        x0,y0 = self.start; x,y = pt
        dx = x - x0; dy = y - y0
        if abs(dx) < self.thresh and abs(dy) < self.thresh:
            return 0, pt
        # yön seçimi: baskın eksen
        if abs(dx) >= abs(dy):
            self.start=None
            return (1 if dx>0 else -1), pt   # sağ: +1, sol: -1
        else:
            self.start=None
            return (2 if dy>0 else -2), pt   # aşağı: +2, yukarı: -2

# ---------- Sayfalar ----------
def page_thermal(img, d, m, C, W, H):
    header(d,C,W,"THERMAL")
    t_pct = clamp((m.temp-30)*(100/60),0,100)
    ring(d, 120, 118, 64, t_pct, C, width=14)
    d.text((120,118), f"{m.temp:.1f}°C", font=F18, fill=C["FG"], anchor="mm")
    d.text((120,144), "CPU TEMP", font=F12, fill=C["ACC2"], anchor="mm")

    # fan
    ring(d, 120, 204, 28, m.fan_pct if m.fan_pct else 0, C, width=10)
    ftxt = "FAN "
    ftxt += f"{m.fan_pct:.0f}%" if m.fan_pct else "N/A"
    if m.fan_rpm: ftxt += f"  {m.fan_rpm} RPM"
    d.text((120,204), ftxt, font=F12, fill=C["FG"], anchor="mm")

    # throttled & frekanslar
    flags = get_throttled_flags()
    d.text((12,228), f"ARM {cpu_freq_mhz()} MHz  GPU {gpu_freq_mhz()} MHz", font=F12, fill=C["FG"])
    if flags:
        d.text((12,246), "Flags: " + " ".join(flags), font=F12, fill=C["WARN"])

def page_ram(img, d, m, C, W, H):
    header(d,C,W,"RAM")
    vm = psutil.virtual_memory(); sm = psutil.swap_memory()
    ring(d, 120, 110, 66, m.ram, C, width=14)
    used = (vm.total - vm.available)/1024/1024
    d.text((120,110), f"{m.ram:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    d.text((120,136), f"{used:.0f}/{vm.total/1024/1024:.0f} MB", font=F12, fill=C["ACC1"], anchor="mm")

    d.text((12,178), f"Swap {sm.percent:.0f}%", font=F12, fill=C["FG"])
    bar(d, 12, 194, W-24, 12, sm.percent, C)
    d.text((12,214), "RAM HISTORY", font=F12, fill=C["MUTED"])
    spark(d, 12, 230, W-24, 36, m.hram, C["ACC1"], C)

def page_cpu(img, d, m, C, W, H):
    header(d,C,W,"CPU")
    ring(d, 120, 96, 56, m.cpu, C, width=12)
    d.text((120,96), f"{m.cpu:.0f}%", font=F18, fill=C["FG"], anchor="mm")
    la1, la5, la15 = os.getloadavg()
    d.text((12,130), f"Load: {la1:.2f} {la5:.2f} {la15:.2f}", font=F12, fill=C["FG"])
    d.text((12,148), f"Freq: {cpu_freq_mhz()} MHz", font=F12, fill=C["FG"])

    d.text((12,170), "Per-core", font=F12, fill=C["MUTED"])
    y=186
    for i,p in enumerate(psutil.cpu_percent(percpu=True)):
        d.text((12,y), f"C{i}", font=F12, fill=C["FG"])
        bar(d, 36, y-2, W-48, 10, p, C)
        y += 16
        if y>H-16: break

def page_disk(img, d, m, C, W, H):
    header(d,C,W,"DISK")
    d.text((12,44), f"/ usage {m.disk_root:.0f}%", font=F16, fill=C["FG"]); 
    bar(d, 12,62,W-24,12,m.disk_root,C)

    d.text((12,86), f"IO R {m.disk_r_kbs:.0f} KB/s  W {m.disk_w_kbs:.0f} KB/s", font=F12, fill=C["FG"])
    d.text((12,106), "Mounts:", font=F12, fill=C["MUTED"])
    y=122
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            d.text((12,y), f"{part.mountpoint} {u.percent:.0f}%", font=F12, fill=C["FG"]); 
            bar(d, 108, y-2, W-120, 10, u.percent, C)
            y += 18
            if y>H-16: break
        except Exception:
            continue

def page_net(img, d, m, C, W, H):
    header(d,C,W,"NETWORK")
    ip = get_ip_primary()
    ssid, sig = wifi_info()
    con = net_connected()
    d.text((12,44), f"IP: {ip}", font=F16, fill=C["FG"])
    d.text((12,66), f"WLAN: {ssid}  {'' if sig is None else str(sig)+' dBm'}", font=F12, fill=C["FG"])
    d.text((W-12,66), "ONLINE" if con else "OFFLINE", font=F12, fill=(C["OK"] if con else C["BAD"]), anchor="ra")

    d.text((12,92), f"UP {m.net_up:.0f} KB/s", font=F12, fill=C["ACC1"])
    spark(d, 12,108, W-24, 36, m.hup, C["ACC1"], C)
    d.text((12,150), f"DN {m.net_dn:.0f} KB/s", font=F12, fill=C["ACC2"])
    spark(d, 12,166, W-24, 36, m.hdn, C["ACC2"], C)

    # interface listesi
    y=208
    d.text((12,y), "Interfaces:", font=F12, fill=C["MUTED"]); y+=14
    for name, addrs in psutil.net_if_addrs().items():
        ip4 = next((a.address for a in addrs if getattr(a, "family", None) == socket.AF_INET), None)
        if ip4:
            d.text((12,y), f"{name}: {ip4}", font=F10, fill=C["FG"]); y+=14
            if y>H-10: break

def page_proc(img, d, m, C, W, H):
    header(d,C,W,"PROCESSES")
    procs=[]
    for p in psutil.process_iter(attrs=["pid","name","cpu_percent","memory_percent"]):
        try: procs.append(p.info)
        except Exception: pass
    procs.sort(key=lambda x: x.get("cpu_percent",0.0), reverse=True)
    y=44
    d.text((12,y), "Name", font=F12, fill=C["MUTED"]); 
    d.text((W-12,y), "CPU  MEM", font=F12, fill=C["MUTED"], anchor="ra"); y+=16
    for row in procs[:8]:
        name=str(row.get("name",""))[:16]
        cpu = clamp(row.get("cpu_percent",0.0),0,100)
        mem = clamp(row.get("memory_percent",0.0),0,100)
        d.text((12,y), name, font=F12, fill=C["FG"])
        d.text((W-12,y), f"{cpu:>3.0f}%  {mem:>3.0f}%", font=F12, fill=C["ACC1"], anchor="ra")
        y+=16
        if y>H-10: break

# grid 2x3
PAGES = [
    [page_thermal, page_ram, page_cpu],
    [page_disk,    page_net, page_proc],
]

# ---------- Uygulama ----------
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
        self.row=0; self.col=0
        self.t_row=0; self.t_col=0
        self.anim=1.0; self.move_dir="X"  # "L","R","U","D"

        self.running=True
        threading.Thread(target=self._metrics_loop, daemon=True).start()

    def _metrics_loop(self):
        while self.running:
            self.metrics.update()
            time.sleep(0.5)

    def _render(self, r, c):
        img=Image.new("RGB",(self.W,self.H), self.C["BG"])
        d=ImageDraw.Draw(img)
        for gy in range(0,self.H,28):
            d.line((0,gy,self.W,gy), fill=self.C["GRID"])
        # tema ipucu
        d.text((self.W-16,8), "◑", font=F12, fill=self.C["MUTED"], anchor="ra")
        PAGES[r][c](img,d,self.metrics,self.C,self.W,self.H)
        return img

    def _toggle_theme(self): 
        self.theme_dark = not self.theme_dark
        self.C = DARK if self.theme_dark else LIGHT

    def _switch(self, move):
        R, Cn = len(PAGES), len(PAGES[0])
        r, c = self.row, self.col
        if move=="L": c = (c-1) % Cn
        elif move=="R": c = (c+1) % Cn
        elif move=="U": r = (r-1) % R
        elif move=="D": r = (r+1) % R
        else: return
        self.t_row, self.t_col = r, c
        self.move_dir = move
        self.anim = 0.0

    def _handle_touch(self):
        code, pt = self.touch.read_gesture(self.W,self.H)
        if not pt: return
        x,y = pt
        if x>self.W-40 and y<40:
            self._toggle_theme(); time.sleep(0.2); return
        if   code == -1: self._switch("L")
        elif code ==  1: self._switch("R")
        elif code == -2: self._switch("U")
        elif code ==  2: self._switch("D")

    def loop(self):
        fps=30; dt=1.0/fps; last=time.time()
        while True:
            now=time.time()
            if now-last<dt: time.sleep(dt-(now-last))
            last=now
            if self.touch.available: self._handle_touch()

            if self.anim < 1.0:
                self.anim = min(1.0, self.anim + 0.12)
                t = ease_out_cubic(self.anim)
                cur = self._render(self.row, self.col)
                nxt = self._render(self.t_row, self.t_col)
                frame = Image.new("RGB",(self.W,self.H), self.C["BG"])
                if self.move_dir in ("L","R"):
                    dirn = -1 if self.move_dir=="L" else 1
                    off = int((-dirn*self.W)*t)
                    frame.paste(cur,(off,0))
                    frame.paste(nxt,(off+dirn*self.W,0))
                else:
                    dirn = -1 if self.move_dir=="U" else 1
                    off = int((-dirn*self.H)*t)
                    frame.paste(cur,(0,off))
                    frame.paste(nxt,(0,off+dirn*self.H))
                self.disp.ShowImage(frame)
                if self.anim>=1.0:
                    self.row, self.col = self.t_row, self.t_col
            else:
                self.disp.ShowImage(self._render(self.row, self.col))

if __name__=="__main__":
    try:
        App().loop()
    except KeyboardInterrupt:
        pass
