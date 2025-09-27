#!/usr/bin/env python3
# Waveshare 1.69" (240x280) + CST816S "polling-only" touch detector
# No IRQ needed. It continuously probes 0x15 and shows "TOUCHED!" when any touch event is detected.

import time
from PIL import Image, ImageDraw

# Vendor display driver (provided by Waveshare repo)
from lib.LCD_1inch69 import LCD_1inch69

# I2C (polling)
try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15  # CST816S default address

# Simple colors
BG   = (0, 0, 0)
FG   = (230, 230, 230)
GOOD = (90, 200, 120)
BAD  = (255, 80, 80)
GRID = (30, 34, 40)

def draw_idle(lcd, W, H, msg="Touch: waiting"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # grid to see refresh
    for gy in range(0, H, 28):
        d.line((0, gy, W, gy), fill=GRID)
    d.text((8, 8), msg, fill=FG)
    lcd.ShowImage(img)

def draw_touched(lcd, W, H):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for gy in range(0, H, 28):
        d.line((0, gy, W, gy), fill=GRID)
    d.text((W//2, H//2), "TOUCHED!", fill=GOOD, anchor="mm")
    lcd.ShowImage(img)

def probe_touch(bus):
    """
    Return True if a finger is detected by CST816S.
    Robust polling:
      - read_i2c_block_data(0x01, 7) -> d[1] lower nibble = fingers count
      - any I/O error: treat as "no data yet"
    """
    try:
        # Some panels wake on first access; if asleep, this may fail once or twice.
        d = bus.read_i2c_block_data(CST816_ADDR, 0x01, 7)
    except OSError:
        # 121 Remote I/O, or not ready: no touch
        return False
    except Exception:
        return False

    # d[1] low 4 bits = number of fingers
    fingers = d[1] & 0x0F
    return fingers > 0

class App:
    def __init__(self):
        # Display init
        self.lcd = LCD_1inch69()
        self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height

        # I2C init (optional—app still runs and shows "I2C OFF" if not available)
        self.bus = None
        self.i2c_ok = SMBUS_OK
        if self.i2c_ok:
            try:
                self.bus = SMBus(I2C_BUS)
            except Exception:
                self.i2c_ok = False

        self.touching = False
        self.last_idle_redraw = 0

    def run(self):
        # Initial screen
        msg = "Touch: waiting" if self.i2c_ok else "I2C OFF: check wiring"
        draw_idle(self.lcd, self.W, self.H, msg)

        # Main loop
        while True:
            touched = False
            if self.i2c_ok and self.bus is not None:
                # Try a few quick probes to catch short taps
                for _ in range(3):
                    if probe_touch(self.bus):
                        touched = True
                        break
                    time.sleep(0.005)
            # Update UI on edge changes
            if touched and not self.touching:
                draw_touched(self.lcd, self.W, self.H)
                self.touching = True
            elif (not touched) and self.touching:
                draw_idle(self.lcd, self.W, self.H, "Touch: waiting")
                self.touching = False
                self.last_idle_redraw = time.time()
            else:
                # Refresh idle text every second so you see the app isn't frozen
                if not self.touching and time.time() - self.last_idle_redraw > 1.0:
                    draw_idle(self.lcd, self.W, self.H, "Touch: waiting")
                    self.last_idle_redraw = time.time()

            time.sleep(0.01)

if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
