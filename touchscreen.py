#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Pi 5 • 1.69" ST7789 ekran + (opsiyonel) CST816S dokunmatik
# Güvenli sparkline, NaN yok, begin() kullanır.

import os, time, math, threading, subprocess
from collections import deque

import psutil
from PIL import Image, ImageDraw, ImageFont

# --- ST7789 ---
try:
    import ST7789  # from python-st7789
except Exception as e:
    raise SystemExit("ST7789 modülü yok. `pip3 install python-st7789` kur ve tekrar dene.")

# --- Dokunmatik (opsiyonel CST816S) ---
TOUCH_ENABLED = True
try:
    from smbus2 import SMBus
except Exception:
    TOUCH_ENABLED = False

I2C_BUS = 1
CST816_ADDR = 0x15

# --- Donanım ayarları ---
WIDTH, HEIGHT = 240, 280   # çoğu 1.69" dikey ekran
ROTATION = 0               # tersse 90/180/270 dene
SPI_PORT, SPI_CS = 0, 0
RST, DC, BACKLIGHT = 27, 25, 24   # kartına göre değişebilir

# --- Renkler / font ---
BG = (5, 8, 12)
FG = (240, 240, 240)
ACCENT = (120, 180, 255)
ACCENT2 = (255, 120, 180)
OK = (90, 200, 120)
WARN = (255, 170, 0)
BAD = (255, 80, 80)
GRID = (25, 30, 36)
BAR_BG = (20, 24, 30)

def load_font(sz):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

F_SM, F_MD, F_LG = load_font(14), load_font(18), load_font(24)

# --- yardımcılar ---
def clamp(v, lo, hi):
    try:
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            v = 0.0
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def pick_color_pct(p):
    p = clamp(p, 0, 100)
    if p < 70: return OK
    if p < 85: return WARN
    return BAD

def map_range(x, a1, a2, b1, b2):
    if a2 == a1:
        return b1
    t = (x - a1) / (a2 - a1)
    return b1 + (b2 - b1) * t

def draw_bar(draw, x, y, w, h, pct):
    pct = clamp(pct, 0, 100)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=BAR_BG)
    v = int(w * pct / 100.0)
    draw.rounded_rectangle([x, y, x + v, y + h], radius=6, fill=pick_color_pct(pct))

def draw_sparkline(draw, x, y, w, h, data, color):
    # güvenli küçük çizgi; veri yoksa bozmaz
    draw.rectangle([x, y, x + w, y + h], fill=(0, 0, 0, 0))
    if not data:
        py = y + h // 2
        draw.line((x, py, x + w, py), fill=color, width=2)
        return

    vals = []
    for v in list(data):
        try:
            v = float(v)
            if not math.isfinite(v):
                continue
            vals.append(v)
        except Exception:
            continue

    if len(vals) == 0:
        py = y + h // 2
        draw.line((x, py, x + w, py), fill=color, width=2)
        return

    mn, mx = min(vals), max(vals)
    # grid
    for gy in range(3):
        gy_y = y + int(gy * h / 3)
        draw.line((x, gy_y, x + w, gy_y), fill=GRID, width=1)

    if mx == mn:
        # düz hat
        py = y + h // 2
        draw.line((x, py, x + w, py), fill=color, width=2)
        return

    n = len(vals)
    prev = None
    for i, v in enumerate(vals):
        norm = (v - mn) / (mx - mn)  # 0..1
        px = x + int(i * (w - 1) / max(1, n - 1))
        py = y + h - 1 - int(norm * (h - 1))
        if prev is not None:
            draw.line((prev[0], prev[1], px, py), fill=color, width=2)
        prev = (px, py)

# --- metrik toplayıcı ---
class Metrics:
    def __init__(self, history_len=90):
        self.cpu = self.ram = self.temp = self.disk = 0.0
        self.net_up = self.net_down = 0.0

        self.hist_cpu = deque(maxlen=history_len)
        self.hist_ram = deque(maxlen=history_len)
        self.hist_temp = deque(maxlen=history_len)
        self.hist_up = deque(maxlen=history_len)
        self.hist_dn = deque(maxlen=history_len)

        self.last_net = psutil.net_io_counters()
        self.lock = threading.Lock()

    def read_temp(self):
        try:
            out = subprocess.check_output(["vcgencmd", "measure_temp"]).decode()
            return float(out.split("=")[1].split("'")[0])
        except Exception:
            try:
                with open("/sys/class/thermal/thermal_zone0/temp") as f:
                    return int(f.read().strip()) / 1000.0
            except Exception:
                return 0.0

    def update(self):
        with self.lock:
            self.cpu = clamp(psutil.cpu_percent(interval=None), 0, 100)
            self.ram = clamp(psutil.virtual_memory().percent, 0, 100)
            self.temp = clamp(self.read_temp(), 0, 120)
            self.disk = clamp(psutil.disk_usage("/").percent, 0, 100)

            now_net = psutil.net_io_counters()
            up = now_net.bytes_sent - self.last_net.bytes_sent
            dn = now_net.bytes_recv - self.last_net.bytes_recv
            self.last_net = now_net
            self.net_up = max(0.0, up / 1024.0)   # KB/s
            self.net_down = max(0.0, dn / 1024.0)

            self.hist_cpu.append(self.cpu)
            self.hist_ram.append(self.ram)
            self.hist_temp.append(self.temp)
            self.hist_up.append(self.net_up)
            self.hist_dn.append(self.net_down)

# --- dokunmatik ---
class Touch:
    def __init__(self):
        self.available = TOUCH_ENABLED
        self.bus = None
        if self.available:
            try:
                self.bus = SMBus(I2C_BUS)
                self._read(0x00, 1)
            except Exception:
                self.available = False
        self.start_y = None
        self.swipe_thresh = 30

    def _read(self, reg, length):
        return self.bus.read_i2c_block_data(CST816_ADDR, reg, length)

    def read_point(self):
        if not self.available: return None
        try:
            d = self._read(0x00, 7)
            event = d[1] & 0x0F
            if event == 0:
                self.start_y = None
                return None
            x = ((d[2] & 0x0F) << 8) | d[3]
            y = ((d[4] & 0x0F) << 8) | d[5]
            x = max(0, min(WIDTH - 1, x))
            y = max(0, min(HEIGHT - 1, y))
            return (x, y)
        except Exception:
            return None

    def detect_swipe(self, y):
        if self.start_y is None:
            self.start_y = y
            return 0
        dy = y - self.start_y
        if dy <= -self.swipe_thresh:
            self.start_y = None
            return -1  # up
        if dy >= self.swipe_thresh:
            self.start_y = None
            return 1   # down
        return 0

# --- sayfa çizimleri ---
def page_summary(img, draw, m: Metrics, _anim_t):
    draw.text((12, 10), "SYSTEM", font=F_LG, fill=FG)
    draw.text((WIDTH - 12, 10), time.strftime("%H:%M"), font=F_MD, fill=ACCENT, anchor="ra")

    draw.text((12, 45), f"CPU {m.cpu:4.0f}%", font=F_MD, fill=FG)
    draw_bar(draw, 12, 66, WIDTH - 24, 14, m.cpu)
    draw_sparkline(draw, 12, 84, WIDTH - 24, 28, m.hist_cpu, ACCENT)

    draw.text((12, 120), f"RAM {m.ram:4.0f}%", font=F_MD, fill=FG)
    draw_bar(draw, 12, 141, WIDTH - 24, 14, m.ram)
    draw_sparkline(draw, 12, 159, WIDTH - 24, 28, m.hist_ram, ACCENT2)

    draw.text((12, 195), f"TEMP {m.temp:4.1f}°C", font=F_MD, fill=FG)
    t_pct = clamp(map_range(m.temp, 30, 90, 0, 100), 0, 100)
    draw_bar(draw, 12, 216, WIDTH - 24, 14, t_pct)
    draw_sparkline(draw, 12, 234, WIDTH - 24, 28, m.hist_temp, BAD)

def page_disk_net(img, draw, m: Metrics, _anim_t):
    draw.text((12, 10), "DISK & NET", font=F_LG, fill=FG)

    draw.text((12, 50), f"DISK {m.disk:4.0f}%", font=F_MD, fill=FG)
    draw_bar(draw, 12, 71, WIDTH - 24, 14, m.disk)

    draw.text((12, 110), f"UP {m.net_up:5.0f} KB/s", font=F_MD, fill=ACCENT)
    draw.text((12, 140), f"DN {m.net_down:5.0f} KB/s", font=F_MD, fill=ACCENT2)
    draw_sparkline(draw, 12, 168, WIDTH - 24, 28, m.hist_up, ACCENT)
    draw_sparkline(draw, 12, 206, WIDTH - 24, 28, m.hist_dn, ACCENT2)

    draw.text((WIDTH - 12, HEIGHT - 10), "kaydır →", font=F_SM, fill=(160,160,160), anchor="rs")

def page_about(img, draw, m: Metrics, _anim_t):
    draw.text((WIDTH//2, 30), "pi5-sysmon", font=F_LG, fill=FG, anchor="ma")
    upt = time.time() - psutil.boot_time()
    d, r = divmod(int(upt), 86400)
    h, r = divmod(r, 3600)
    mi, _ = divmod(r, 60)
    draw.text((WIDTH//2, 70), "Yukarı/Aşağı kaydır", font=F_MD, fill=ACCENT, anchor="ma")
    draw.text((WIDTH//2, 110), f"Uptime: {d}g {h}s {mi}d", font=F_MD, fill=FG, anchor="ma")
    draw.text((WIDTH//2, 140), f"CPU Cores: {psutil.cpu_count()}", font=F_MD, fill=FG, anchor="ma")
    draw.text((WIDTH//2, HEIGHT-20), "© Ümit için turbo mod", font=F_SM, fill=(140,140,140), anchor="ma")

PAGES = [page_summary, page_disk_net, page_about]

# --- uygulama ---
def ease_out_cubic(t): return 1 - (1 - t) ** 3
def lerp(a, b, t): return a + (b - a) * t

class App:
    def __init__(self):
        self.st = ST7789.ST7789(
            height=HEIGHT, width=WIDTH,
            port=SPI_PORT, cs=SPI_CS,
            rst=RST, dc=DC, backlight=BACKLIGHT,
            rotation=ROTATION, spi_speed_hz=80_000_000
        )
        self.st.begin()

        self.metrics = Metrics(history_len=90)
        self.touch = Touch()

        # İlk karede histogramların boş kalmaması için biraz tohumla
        for _ in range(4):
            self.metrics.update()
            time.sleep(0.1)

        self.current_page = 0
        self.target_page = 0
        self.anim_progress = 1.0

        self.img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        self.draw = ImageDraw.Draw(self.img)

        self.running = True
        self.t_metrics = threading.Thread(target=self.metrics_loop, daemon=True)
        self.t_metrics.start()

    def metrics_loop(self):
        while self.running:
            self.metrics.update()
            time.sleep(0.5)

    def handle_touch(self):
        pt = self.touch.read_point()
        if pt is None:
            return
        _, y = pt
        swipe = self.touch.detect_swipe(y)
        if swipe == -1:
            self.switch_page((self.current_page - 1) % len(PAGES))
        elif swipe == 1:
            self.switch_page((self.current_page + 1) % len(PAGES))

    def switch_page(self, idx):
        if idx == self.current_page:
            return
        self.target_page = idx
        self.anim_progress = 0.0

    def render_page(self, page_index):
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        drw = ImageDraw.Draw(img)
        for gy in range(0, HEIGHT, 28):
            drw.line((0, gy, WIDTH, gy), fill=GRID)
        PAGES[page_index](img, drw, self.metrics, self.anim_progress)
        return img

    def main_loop(self):
        fps = 30.0
        dt = 1.0 / fps
        last = time.time()
        while True:
            now = time.time()
            if now - last < dt:
                time.sleep(dt - (now - last))
            last = now

            if self.touch.available:
                self.handle_touch()

            if self.anim_progress < 1.0:
                self.anim_progress = min(1.0, self.anim_progress + 0.12)
                t = ease_out_cubic(self.anim_progress)
                direction = 1 if (self.target_page > self.current_page or
                                  (self.current_page == len(PAGES)-1 and self.target_page == 0)) else -1
                offset = int(lerp(0, -direction * HEIGHT, t))

                img_cur = self.render_page(self.current_page)
                img_tar = self.render_page(self.target_page)

                frame = Image.new("RGB", (WIDTH, HEIGHT), BG)
                frame.paste(img_cur, (0, offset))
                frame.paste(img_tar, (0, offset + direction * HEIGHT))
                self.st.display(frame)

                if self.anim_progress >= 1.0:
                    self.current_page = self.target_page
            else:
                img = self.render_page(self.current_page)
                self.st.display(img)

    def stop(self):
        self.running = False

if __name__ == "__main__":
    try:
        app = App()
        app.main_loop()
    except KeyboardInterrupt:
        pass
