#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Raspberry Pi 5 • 1.69" (240x280) "NASA telemetrisi" paneli (güzelleştirilmiş)
# - Görüntü: lib/LCD_1inch69 (üretici sürücüsü)
# - Dokunmatik: önce vendor Touch_1inch69; o yoksa I2C (0x15) bus auto-scan
# - Sayfalar: Dashboard • Performance • Network • Processes • System
# - Özellikler:
#     • Per-core CPU barları, CPU/RAM/TEMP/GPU sıcaklık & saat
#     • Throttling bayrakları (vcgencmd get_throttled)
#     • Load average, disk I/O KB/s, ağ Up/Down, toplam veri
#     • Wi-Fi SSID ve RSSI, IP adresleri (eth/wlan)
#     • Swipe ile sayfa, sağ üst tap ile tema, sol üst tap ile parlaklık (PWM varsa)
# - NumPy yok; NaN/inf güvenlikli; yumuşak sayfa animasyonu.

import os, sys, time, math, threading, subprocess, socket
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import psutil

# ---------- Sürücüler ----------
from lib.LCD_1inch69 import LCD_1inch69

# ---------- Tema Renkleri ----------
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
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F11, F12, F14, F16, F18, F22 = (load_font(s) for s in (11,12,14,16,18,22))

# ---------- Yardımcılar ----------
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

def ring(d, cx, cy, r, pct, C, width=10):
    pct = clamp(pct,0,100)/100.0
    box=[cx-r, cy-r, cx+r, cy+r]
    d.arc(box, start=135, end=405, width=width, fill=C["BARBG"])
    d.arc(box, start=135, end=135+int(270*pct), width=width, fill=pick_color(pct*100,C))

def badge(d, x,y, txt, C, fill=None):
    t = txt
    w,h = d.textbbox((0,0), t, font=F12)[2:]
    pad=6
    d.rounded_rectangle([x,y,x+w+pad*2,y+h+pad], radius=8, fill=fill or C["BARBG"])
    d.text((x+pad,y+pad//2), t, font=F12, fill=C["FG"])

def sh(txt, n):
    s=str(txt); return s if len(s)<=n else s[:n-1]+"…"

def get_cmd(*args):
    try:
        out = subprocess.check_output(args, stderr=subprocess.DEVNULL).decode().strip()
        return out
    except Exception:
        return ""

# ---------- Dokunmatik ----------
try:
    from lib import Touch_1inch69 as TP
    _VENDOR_TOUCH = True
except Exception:
    _VENDOR_TOUCH = False

try:
    from smbus2 import SMBus
    _SMBUS_OK = True
except Exception:
    _SMBUS_OK = False

class Touch:
    def __init__(self):
        self.available=False
        self.mode=None  # 'vendor'|'i2c'
        self.dev=None
        self.bus_no=None
        self.start_y=None
        self.swipe=28
        # 1) vendor
        if _VENDOR_TOUCH:
            try:
                # modülde ilk sınıf
                klass=None
                for name in dir(TP):
                    obj=getattr(TP,name)
                    if isinstance(obj,type): klass=obj; break
                if klass:
                    self.dev=klass(); self.mode='vendor'; self.available=True; return
            except Exception: pass
        # 2) i2c autoscan
        if _SMBUS_OK:
            for b in (1,13,14,0,10,11):
                try:
                    with SMBus(b) as bus:
                        bus.read_i2c_block_data(0x15,0x00,1)
                        self.bus_no=b; self.mode='i2c'; self.available=True; return
                except Exception: continue

    def read_point(self, W,H):
        if not self.available: return None
        try:
            if self.mode=='vendor':
                for m in ("Read_TouchPoint","read","get_point","read_point"):
                    if hasattr(self.dev, m):
                        r=getattr(self.dev,m)()
                        if isinstance(r,(list,tuple)) and len(r)>=2:
                            x,y=int(r[0]),int(r[1])
                            return (max(0,min(W-1,x)), max(0,min(H-1,y)))
                        if isinstance(r,dict) and "x" in r and "y" in r:
                            x,y=int(r["x"]),int(r["y"])
                            return (max(0,min(W-1,x)), max(0,min(H-1,y)))
                return None
            else:
                with SMBus(self.bus_no) as bus:
                    d = bus.read_i2c_block_data(0x15,0x00,7)
                event=d[1]&0x0F
                if event==0: self.start_y=None; return None
                x=((d[2]&0x0F)<<8)|d[3]; y=((d[4]&0x0F)<<8)|d[5]
                return (max(0,min(W-1,x)), max(0,min(H-1,y)))
        except Exception:
            return None

    def detect_swipe(self, y):
        if self.start_y is None:
            self.start_y=y; return 0
        dy = y - self.start_y
        if dy <= -self.swipe: self.start_y=None; return -1
        if dy >=  self.swipe: self.start_y=None; return  1
        return 0

# ---------- Metrikler ----------
class Metrics:
    def __init__(self, hist_len=120):
        self.cpu=self.ram=self.disk=self.temp=0.0
        self.up=self.dn=0.0
        self.gpu_temp=0.0; self.gpu_clk=0.0
        self.load1=self.load5=self.load15=0.0
        self.hcpu=deque(maxlen=hist_len)
        self.hram=deque(maxlen=hist_len)
        self.htmp=deque(maxlen=hist_len)
        self.hup=deque(maxlen=hist_len)
        self.hdn=deque(maxlen=hist_len)
        self.hdio=deque(maxlen=hist_len)
        self.last_net = psutil.net_io_counters()
        self.last_disk = psutil.disk_io_counters()

    def _temp_cpu(self):
        t = get_cmd("vcgencmd","measure_temp")
        if t and "=" in t: 
            try: return float(t.split("=")[1].split("'")[0])
            except: pass
        try:
            return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000.0
        except Exception:
            return 0.0

    def _gpu_temp_clk(self):
        gt = get_cmd("vcgencmd","measure_temp")
        gc = get_cmd("vcgencmd","measure_clock","core")
        if gc and "=" in gc:
            try: self.gpu_clk = int(gc.split("=")[1])/1_000_000
            except: self.gpu_clk=0.0
        if gt and "=" in gt:
            try: self.gpu_temp=float(gt.split("=")[1].split("'")[0])
            except: self.gpu_temp=0.0

    def update(self):
        self.cpu = clamp(psutil.cpu_percent(interval=None), 0, 100)
        self.ram = clamp(psutil.virtual_memory().percent, 0, 100)
        self.disk = clamp(psutil.disk_usage("/").percent, 0, 100)
        self.temp = clamp(self._temp_cpu(), 0, 120)
        self._gpu_temp_clk()

        now = psutil.net_io_counters()
        self.up = max(0.0, (now.bytes_sent - self.last_net.bytes_sent)/1024.0)
        self.dn = max(0.0, (now.bytes_recv - self.last_net.bytes_recv)/1024.0)
        self.last_net = now

        dnow = psutil.disk_io_counters()
        dio = ((dnow.read_bytes - self.last_disk.read_bytes) +
               (dnow.write_bytes - self.last_disk.write_bytes))/1024.0
        self.last_disk = dnow

        la = os.getloadavg() if hasattr(os, "getloadavg") else (0,0,0)
        self.load1, self.load5, self.load15 = (float(la[0]), float(la[1]), float(la[2]))

        self.hcpu.append(self.cpu); self.hram.append(self.ram); self.htmp.append(self.temp)
        self.hup.append(self.up);   self.hdn.append(self.dn);   self.hdio.append(dio)

    def throttled_flags(self):
        s = get_cmd("vcgencmd","get_throttled")
        # format: throttled=0x50005 vb.
        try:
            val = int(s.split("=")[1], 16)
        except Exception:
            val = 0
        flags = dict(
            under_volt = bool(val & (1<<0)),
            freq_cap   = bool(val & (1<<1)),
            throttled  = bool(val & (1<<2)),
            temp_limit = bool(val & (1<<3)),
        )
        return flags

# ---------- Sayfa çizimleri ----------
def header(d, C, W, title):
    d.text((12,10), title, font=F22, fill=C["FG"])
    d.text((W-12,10), time.strftime("%H:%M"), font=F16, fill=C["ACC1"], anchor="ra")

def page_dashboard(img, d, m, C, W, H):
    header(d,C,W,"DASHBOARD")
    # üç halka: CPU/RAM/TEMP
    ring(d, 60, 86, 42, m.cpu, C); d.text((60,86), f"{m.cpu:0.0f}%", font=F14, fill=C["FG"], anchor="mm"); d.text((60,110),"CPU", font=F11, fill=C["ACC1"], anchor="mm")
    ring(d, 180,86, 42, m.ram, C); d.text((180,86),f"{m.ram:0.0f}%",font=F14,fill=C["FG"],anchor="mm"); d.text((180,110),"RAM", font=F11, fill=C["ACC1"], anchor="mm")
    t_pct = clamp((m.temp-30)*(100/60),0,100)
    ring(d, 120,160,48, t_pct, C); d.text((120,160), f"{m.temp:0.1f}°C", font=F14, fill=C["FG"], anchor="mm"); d.text((120,184),"CPU TEMP", font=F11, fill=C["ACC2"], anchor="mm")

    # CPU geçmişi
    d.text((12,202), "CPU history", font=F12, fill=C["MUTED"])
    spark(d, 12,218, W-24,36, m.hcpu, C["ACC1"], C)
    # Throttling rozetleri
    flags=m.throttled_flags()
    x=12; y=260
    for k,lab in [("under_volt","UV"),("freq_cap","CAP"),("throttled","THR"),("temp_limit","HOT")]:
        color = C["BAD"] if flags[k] else C["BARBG"]
        d.rounded_rectangle([x,y-14,x+38,y], radius=6, fill=color)
        d.text((x+6,y-12), lab, font=F11, fill=C["FG"])
        x += 44

def page_performance(img, d, m, C, W, H):
    header(d,C,W,"PERFORMANCE")
    # Per-core barlar
    y=44
    percs = psutil.cpu_percent(percpu=True)
    for i,p in enumerate(percs[:6]):  # 6 çekirdeği sığdır
        d.text((12,y), f"C{i}", font=F12, fill=C["FG"])
        bar(d, 38,y-2, W-50, 10, p, C)
        y += 16
        if y > 140: break

    # Load avg + GPU
    d.text((12,150), f"Load: {m.load1:.2f} {m.load5:.2f} {m.load15:.2f}", font=F12, fill=C["FG"])
    d.text((12,168), f"GPU: {m.gpu_temp:.1f}°C {m.gpu_clk:.0f}MHz", font=F12, fill=C["FG"])

    # RAM detay
    vm = psutil.virtual_memory()
    used = (vm.total - vm.available)/1024/1024
    d.text((12,186), f"Mem: {used:.0f}/{vm.total/1024/1024:.0f} MB", font=F12, fill=C["FG"])
    bar(d, 12,204, W-24, 12, m.ram, C)

    # Disk I/O ve CPU/RAM/TEMP spark
    d.text((12,222), "Disk I/O (KB/s)", font=F12, fill=C["MUTED"])
    spark(d, 12,236, W-24, 16, m.hdio, C["ACC2"], C, grid=False)

def page_network(img, d, m, C, W, H):
    header(d,C,W,"NETWORK")
    # UP/DN canlı ve geçmiş
    d.text((12,48), f"UP {m.up:0.0f} KB/s", font=F14, fill=C["ACC1"])
    spark(d, 12,64, W-24, 28, m.hup, C["ACC1"], C)
    d.text((12,98), f"DN {m.dn:0.0f} KB/s", font=F14, fill=C["ACC2"])
    spark(d, 12,114, W-24, 28, m.hdn, C["ACC2"], C)

    # Toplam veri
    nio = psutil.net_io_counters()
    d.text((12,152), f"TX {nio.bytes_sent/1024/1024:.1f} MB  RX {nio.bytes_recv/1024/1024:.1f} MB", font=F12, fill=C["FG"])

    # SSID & RSSI
    ssid = get_cmd("iwgetid","-r")
    try:
        iw = subprocess.check_output(["iw","dev"], stderr=subprocess.DEVNULL).decode()
        rssi = ""
        for line in iw.splitlines():
            line=line.strip()
            if line.startswith("signal:"):
                rssi = line.split()[1]; break
    except Exception:
        rssi = ""
    d.text((12,172), f"Wi-Fi: {sh(ssid,14)}  {rssi+' dBm' if rssi else ''}", font=F12, fill=C["FG"])

    # IP'ler
    ip_eth = get_cmd("bash","-lc","ip -4 addr show | awk '/eth/{f=1} f&&/inet/{print $2; exit}' | cut -d/ -f1")
    ip_wlan= get_cmd("bash","-lc","ip -4 addr show | awk '/wlan/{f=1} f&&/inet/{print $2; exit}' | cut -d/ -f1")
    d.text((12,190), f"ETH: {ip_eth or '-'}", font=F12, fill=C["FG"])
    d.text((12,208), f"WLAN: {ip_wlan or '-'}", font=F12, fill=C["FG"])

    # Küçük CPU/RAM barları
    d.text((12,228), f"CPU {m.cpu:0.0f}%   RAM {m.ram:0.0f}%", font=F12, fill=C["FG"])
    bar(d, 12,244, (W-24)//2-4, 10, m.cpu, C)
    bar(d, 12+(W-24)//2+4,244, (W-24)//2-4, 10, m.ram, C)

def page_processes(img, d, m, C, W, H):
    header(d,C,W,"PROCESSES")
    procs=[]
    for p in psutil.process_iter(attrs=["pid","name","cpu_percent","memory_percent"]):
        try: procs.append(p.info)
        except Exception: pass
    procs.sort(key=lambda x: (x.get("cpu_percent",0.0), x.get("memory_percent",0.0)), reverse=True)
    y=44
    for row in procs[:8]:
        name=sh(row.get("name",""),16)
        cpu=clamp(row.get("cpu_percent",0.0),0,100)
        mem=clamp(row.get("memory_percent",0.0),0,100)
        d.text((12,y), name, font=F12, fill=C["FG"])
        d.text((W-12,y), f"{cpu:0.0f}%  {mem:0.0f}%", font=F11, fill=C["ACC1"], anchor="ra")
        y+=22

def page_system(img, d, m, C, W, H):
    header(d,C,W,"SYSTEM")
    # Uptime
    upt = time.time() - psutil.boot_time()
    dys,r = divmod(int(upt),86400); hrs,r=divmod(r,3600); mins,_ = divmod(r,60)
    d.text((12,44), f"Uptime: {dys}g {hrs}s {mins}d", font=F14, fill=C["FG"])
    # CPU frekansı
    try:
        arm=get_cmd("vcgencmd","measure_clock","arm")
        arm=int(arm.split("=")[1])/1_000_000
    except Exception:
        cf=psutil.cpu_freq()
        arm=cf.current if cf else 0
    d.text((12,64), f"CPU Freq: {arm:.0f} MHz", font=F14, fill=C["FG"])
    d.text((12,82), f"GPU Freq: {m.gpu_clk:.0f} MHz", font=F14, fill=C["FG"])
    d.text((12,100),f"Cores: {psutil.cpu_count()}  Python: {'.'.join(map(str,sys.version_info[:3]))}", font=F14, fill=C["FG"])
    # Diskler
    y=122
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            d.text((12,y), f"{part.mountpoint} {u.percent:.0f}%", font=F12, fill=C["FG"])
            bar(d, 112, y-2, W-124, 10, u.percent, C)
            y+=20
            if y>H-20: break
        except Exception: continue

PAGES=[page_dashboard, page_performance, page_network, page_processes, page_system]

# ---------- Uygulama ----------
class App:
    def __init__(self):
        self.disp = LCD_1inch69(); self.disp.Init()
        self.W,self.H = self.disp.width, self.disp.height
        # parlaklık kontrolü varsa yüzde100 başla
        self._bl = 100
        if hasattr(self.disp, "bl_DutyCycle"):
            try: self.disp.bl_DutyCycle(self._bl)
            except Exception: pass
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
        for gy in range(0,self.H,28):
            d.line((0,gy,self.W,gy), fill=self.C["GRID"])
        PAGES[idx](img,d,self.metrics,self.C,self.W,self.H)
        # header ikon ipuçları
        d.text((8,10), "◼", font=F11, fill=self.C["MUTED"])  # sol üst: BL toggle
        d.text((self.W-18,10), "◑", font=F11, fill=self.C["MUTED"])  # sağ üst: tema
        return img

    def _switch(self, idx):
        if idx==self.cur: return
        self.tgt=idx; self.anim=0.0

    def _toggle_theme(self):
        self.theme_dark = not self.theme_dark
        self.C = DARK if self.theme_dark else LIGHT

    def _toggle_bl(self):
        self._bl = 30 if self._bl>50 else 100
        if hasattr(self.disp,"bl_DutyCycle"):
            try: self.disp.bl_DutyCycle(self._bl)
            except Exception: pass

    def _handle_touch(self):
        pt = self.touch.read_point(self.W,self.H)
        if not pt: return
        x,y = pt
        # sol üst tap: parlaklık
        if x<40 and y<40: self._toggle_bl(); time.sleep(0.2); return
        # sağ üst tap: tema
        if x>self.W-40 and y<40: self._toggle_theme(); time.sleep(0.2); return
        s = self.touch.detect_swipe(y)
        if s==-1: self._switch((self.cur-1) % len(PAGES))
        elif s==1: self._switch((self.cur+1) % len(PAGES))

    def loop(self):
        fps=30; dt=1.0/fps; last=time.time()
        while True:
            now=time.time()
            if now-last<dt: time.sleep(dt-(now-last))
            last=now
            if self.touch.available: self._handle_touch()
            if self.anim<1.0:
                self.anim=min(1.0,self.anim+0.12)
                t = 1 - (1 - self.anim)**3
                dirn = 1 if (self.tgt > self.cur or (self.cur==len(PAGES)-1 and self.tgt==0)) else -1
                off=int(( -dirn*self.H ) * t)
                cur=self._render(self.cur); nxt=self._render(self.tgt)
                frame=Image.new("RGB",(self.W,self.H), self.C["BG"])
                frame.paste(cur,(0,off)); frame.paste(nxt,(0,off+dirn*self.H))
                self.disp.ShowImage(frame)
                if self.anim>=1.0: self.cur=self.tgt
            else:
                self.disp.ShowImage(self._render(self.cur))

if __name__=="__main__":
    try:
        App().loop()
    except KeyboardInterrupt:
        pass
