#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, math, os, threading, psutil, subprocess
from collections import deque
# --- EKRAN SÜRÜCÜSÜ (ST7789) ---
try:
    import ST7789 as st7789  # pimoroni python-st7789 modülü
except Exception as e:
    raise SystemExit("st7789 kütüphanesi yok. `pip3 install st7789` ile kur.")

from PIL import Image, ImageDraw, ImageFont
import numpy as np

# --- DOKUNMATİK (CST816S) ---
TOUCH_ENABLED = True
try:
    from smbus2 import SMBus
except Exception:
    TOUCH_ENABLED = False

I2C_BUS = 1
CST816_ADDR = 0x15

# --- DONANIM AYARLARI ---
# Çoğu 1.69” dikey kullanım: 240x280, rotation=0 ile uzun kenar dikey
WIDTH, HEIGHT = 240, 280
# Bazı modüller 280x240 yatay bekler. Gerekirse şu ikisini değiştir:
# WIDTH, HEIGHT = 280, 240

# ST7789 pinleri, çoğu HAT için default iş görüyor (SPI0: CE0, SCLK, MOSI)
SPI_PORT = 0
SPI_CS = 0
RST = 27   # BCM numaraları; kartına göre ayarla
DC = 25
BACKLIGHT = 24  # varsa

ROTATION = 0  # 0, 90, 180, 270 (görüntü tersse değiştir)

# --- GÖRSEL AYARLAR ---
BG = (5, 8, 12)
FG = (240, 240, 240)
ACCENT = (120, 180, 255)
ACCENT2 = (255, 120, 180)
BAR = (90, 200, 120)
WARN = (255, 170, 0)
DANGER = (255, 80, 80)
GRID = (25, 30, 36)

# Font: DejaVu system font genelde var. Yoksa PIL default kullanır.
def load_font(size):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

FONT_SM = load_font(14)
FONT_MD = load_font(18)
FONT_LG = load_font(24)

# --- BASİT EASING ---
def lerp(a, b, t): return a + (b - a) * t
def ease_out_cubic(t): return 1 - (1 - t) ** 3

# --- METRİK TOPLAYICI ---
class Metrics:
    def __init__(self, history_len=60):
        self.cpu = 0.0
        self.ram = 0.0
        self.temp = 0.0
        self.disk = 0.0
        self.net_up = 0.0
        self.net_down = 0.0

        self.hist_cpu = deque(maxlen=history_len)
        self.hist_ram = deque(maxlen=history_len)
        self.hist_temp = deque(maxlen=history_len)
        self.hist_up = deque(maxlen=history_len)
        self.hist_dn = deque(maxlen=history_len)

        self.last_net = psutil.net_io_counters()
        self.lock = threading.Lock()

    def read_temp(self):
        # Raspberry Pi sıcaklığı
        try:
            out = subprocess.check_output(["vcgencmd", "measure_temp"]).decode()
            # format: temp=42.0'C
            val = float(out.split("=")[1].split("'")[0])
            return val
        except Exception:
            # Linux hwmon fallback
            try:
                with open("/sys/class/thermal/thermal_zone0/temp") as f:
                    return int(f.read().strip()) / 1000.0
            except Exception:
                return 0.0

    def update(self):
        with self.lock:
            self.cpu = psutil.cpu_percent(interval=None)
            self.ram = psutil.virtual_memory().percent
            self.temp = self.read_temp()
            self.disk = psutil.disk_usage("/").percent

            now_net = psutil.net_io_counters()
            dt_up = now_net.bytes_sent - self.last_net.bytes_sent
            dt_dn = now_net.bytes_recv - self.last_net.bytes_recv
            self.last_net = now_net

            # KB/s
            self.net_up = max(0.0, dt_up / 1024.0)
            self.net_down = max(0.0, dt_dn / 1024.0)

            self.hist_cpu.append(self.cpu)
            self.hist_ram.append(self.ram)
            self.hist_temp.append(self.temp)
            self.hist_up.append(self.net_up)
            self.hist_dn.append(self.net_down)

# --- DOKUNMATİK OKUYUCU (CST816S) ---
class Touch:
    def __init__(self):
        self.available = TOUCH_ENABLED
        self.bus = None
        if self.available:
            try:
                self.bus = SMBus(I2C_BUS)
                # Basit okuma testi
                self._read_regs(0x00, 1)
            except Exception:
                self.available = False

        # swipe tespiti
        self.start_y = None
        self.last_y = None
        self.swipe_thresh = 30

    def _read_regs(self, reg, length):
        return self.bus.read_i2c_block_data(CST816_ADDR, reg, length)

    def read_point(self):
        """Dokunma var ise (x,y) döndür, yoksa None."""
        if not self.available:
            return None

        try:
            data = self._read_regs(0x00, 7)
            # data[2]=xH, data[3]=xL, data[4]=yH, data[5]=yL gibi
            event_flag = data[1] & 0x0F
            if event_flag == 0:  # no touch
                self.start_y = None
                return None
            x = ((data[2] & 0x0F) << 8) | data[3]
            y = ((data[4] & 0x0F) << 8) | data[5]
            # Ekran en-boyuna göre sınıra al
            x = max(0, min(WIDTH - 1, x))
            y = max(0, min(HEIGHT - 1, y))
            return (x, y)
        except Exception:
            return None

    def detect_swipe(self, y):
        if self.start_y is None:
            self.start_y = y
            self.last_y = y
            return 0
        self.last_y = y
        dy = y - self.start_y
        if dy <= -self.swipe_thresh:
            self.start_y = None
            return -1  # up swipe
        if dy >= self.swipe_thresh:
            self.start_y = None
            return 1   # down swipe
        return 0

# --- GÖRSEL ARAÇLAR ---
def draw_bar(draw, x, y, w, h, value_pct, color):
    value_pct = max(0.0, min(100.0, value_pct)) / 100.0
    draw.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=(20, 24, 30))
    v = int(w * value_pct)
    draw.rounded_rectangle([x, y, x + v, y + h], radius=6, fill=color)

def draw_sparkline(draw, x, y, w, h, data, color):
    # Arka plan
    draw.rectangle([x, y, x + w, y + h], fill=(0, 0, 0, 0), outline=None)
    if not data:
        return

    arr = np.array(list(data), dtype=float)

    # Geçersiz değerleri at (NaN/inf)
    mask = np.isfinite(arr)
    if not np.any(mask):
        return
    arr = arr[mask]

    # Tek değer ya da sabit dizi: düz çizgi
    mn, mx = float(np.min(arr)), float(np.max(arr))
    if mx - mn == 0:
        norm = np.zeros_like(arr)
    else:
        norm = (arr - mn) / (mx - mn)

    # Noktalar
    n = len(norm)
    if n < 2:
        # En az iki nokta yoksa ortadan kısa bir çizgi
        py = y + h // 2
        draw.line((x, py, x + w, py), fill=color, width=2)
        return

    pts = []
    for i, v in enumerate(norm):
        if not np.isfinite(v):
            v = 0.0
        px = x + int(i * (w - 1) / (n - 1))
        py = y + h - 1 - int(v * (h - 1))
        pts.append((px, py))

    # grid
    for gy in range(3):
        gy_y = y + int(gy * h / 3)
        draw.line((x, gy_y, x + w, gy_y), fill=GRID, width=1)

    # çizgi
    for i in range(1, len(pts)):
        draw.line((pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]), fill=color, width=2)

# --- SAYFALAR ---
def page_summary(img, draw, m: Metrics, anim_t):
    # Başlık
    draw.text((12, 10), "SYSTEM", font=FONT_LG, fill=FG)
    draw.text((WIDTH - 12, 10), time.strftime("%H:%M"), font=FONT_MD, fill=ACCENT, anchor="ra")

    # CPU
    draw.text((12, 45), f"CPU {m.cpu:4.0f}%", font=FONT_MD, fill=FG)
    draw_bar(draw, 12, 66, WIDTH - 24, 14, m.cpu, pick_color_pct(m.cpu))
    draw_sparkline(draw, 12, 84, WIDTH - 24, 28, m.hist_cpu, ACCENT)

    # RAM
    draw.text((12, 120), f"RAM {m.ram:4.0f}%", font=FONT_MD, fill=FG)
    draw_bar(draw, 12, 141, WIDTH - 24, 14, m.ram, pick_color_pct(m.ram))
    draw_sparkline(draw, 12, 159, WIDTH - 24, 28, m.hist_ram, ACCENT2)

    # Temp
    draw.text((12, 195), f"TEMP {m.temp:4.1f}°C", font=FONT_MD, fill=FG)
    t_pct = np.interp(m.temp, [30, 90], [0, 100])
    draw_bar(draw, 12, 216, WIDTH - 24, 14, t_pct, pick_color_pct(t_pct))
    draw_sparkline(draw, 12, 234, WIDTH - 24, 28, m.hist_temp, DANGER)

def page_disk_net(img, draw, m: Metrics, anim_t):
    draw.text((12, 10), "DISK & NET", font=FONT_LG, fill=FG)
    # Disk
    draw.text((12, 50), f"DISK {m.disk:4.0f}%", font=FONT_MD, fill=FG)
    draw_bar(draw, 12, 71, WIDTH - 24, 14, m.disk, pick_color_pct(m.disk))
    # Net
    draw.text((12, 110), f"UP {m.net_up:5.0f} KB/s", font=FONT_MD, fill=ACCENT)
    draw.text((12, 140), f"DN {m.net_down:5.0f} KB/s", font=FONT_MD, fill=ACCENT2)
    draw_sparkline(draw, 12, 168, WIDTH - 24, 28, m.hist_up, ACCENT)
    draw_sparkline(draw, 12, 206, WIDTH - 24, 28, m.hist_dn, ACCENT2)
    # İpucu
    draw.text((WIDTH - 12, HEIGHT - 10), "kaydır →", font=FONT_SM, fill=(160,160,160), anchor="rs")

def page_about(img, draw, m: Metrics, anim_t):
    draw.text((WIDTH//2, 30), "pi5-sysmon", font=FONT_LG, fill=FG, anchor="ma")
    draw.text((WIDTH//2, 70), "Swipe yukarı/aşağı", font=FONT_MD, fill=ACCENT, anchor="ma")
    # Sistem özeti
    upt = time.time() - psutil.boot_time()
    d, r = divmod(int(upt), 86400)
    h, r = divmod(r, 3600)
    mi, _ = divmod(r, 60)
    draw.text((WIDTH//2, 110), f"Uptime: {d}g {h}s {mi}d", font=FONT_MD, fill=FG, anchor="ma")
    draw.text((WIDTH//2, 140), f"CPU Cores: {psutil.cpu_count()}", font=FONT_MD, fill=FG, anchor="ma")
    draw.text((WIDTH//2, 170), f"Python: {'.'.join(map(str, os.sys.version_info[:3]))}", font=FONT_MD, fill=FG, anchor="ma")
    draw.text((WIDTH//2, HEIGHT-20), "© Ümit için turbo mod", font=FONT_SM, fill=(140,140,140), anchor="ma")

PAGES = [page_summary, page_disk_net, page_about]

# --- ANA UYGULAMA ---
class App:
    def __init__(self):
        self.st = st7789.ST7789(
            height=HEIGHT,
            width=WIDTH,
            port=SPI_PORT,
            cs=SPI_CS,
            rst=RST,
            dc=DC,
            backlight=BACKLIGHT,
            rotation=ROTATION,
            spi_speed_hz=80_000_000
        )
        self.st.begin()

        self.metrics = Metrics(history_len=90)
        self.touch = Touch()

        self.current_page = 0
        self.target_page = 0
        self.anim_progress = 1.0  # 1: sayfa sabit, <1: kaydırma animasyonu

        self.img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        self.draw = ImageDraw.Draw(self.img)

        # metrik iş parçacığı
        self.running = True
        self.t_metrics = threading.Thread(target=self.metrics_loop, daemon=True)
        self.t_metrics.start()
        # Hist'leri tohumla ki ilk frame'de boş/NaN olmasın
        for _ in range(3):
            self.metrics.update()
            time.sleep(0.1)
            
    def metrics_loop(self):
        while self.running:
            self.metrics.update()
            time.sleep(0.5)

    def handle_touch(self):
        pt = self.touch.read_point()
        if pt is None:
            return
        x, y = pt
        swipe = self.touch.detect_swipe(y)
        if swipe == -1:   # yukarı
            self.switch_page((self.current_page - 1) % len(PAGES))
        elif swipe == 1:  # aşağı
            self.switch_page((self.current_page + 1) % len(PAGES))

    def switch_page(self, new_index):
        if new_index == self.current_page:
            return
        self.target_page = new_index
        self.anim_progress = 0.0  # animasyon başlat

    def render_page(self, page_index, y_offset=0):
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        drw = ImageDraw.Draw(img)
        # light grid arka plan
        for gy in range(0, HEIGHT, 28):
            drw.line((0, gy, WIDTH, gy), fill=GRID)
        PAGES[page_index](img, drw, self.metrics, self.anim_progress)
        return img, y_offset

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

            # Sayfa animasyonu: current yukarı/aşağı kayar, target ters yönde gelir
            if self.anim_progress < 1.0:
                self.anim_progress = min(1.0, self.anim_progress + 0.12)
                t = ease_out_cubic(self.anim_progress)
                direction = 1 if (self.target_page > self.current_page or
                                  (self.current_page == len(PAGES)-1 and self.target_page == 0)) else -1
                offset = int(lerp(0, -direction * HEIGHT, t))
                img_cur, _ = self.render_page(self.current_page, y_offset=0)
                img_tar, _ = self.render_page(self.target_page, y_offset=0)

                frame = Image.new("RGB", (WIDTH, HEIGHT), BG)
                frame.paste(img_cur, (0, offset))
                frame.paste(img_tar, (0, offset + direction * HEIGHT))
                self.st.display(frame)
                if self.anim_progress >= 1.0:
                    self.current_page = self.target_page
            else:
                # Normal çizim
                img, _ = self.render_page(self.current_page, y_offset=0)
                self.st.display(img)

    def stop(self):
        self.running = False

if __name__ == "__main__":
    try:
        app = App()
        app.main_loop()
    except KeyboardInterrupt:
        pass
