#!/usr/bin/env python3
# 1.69" 240x280 LCD_1inch69 + CST816S touch
# Basıldığında ekranda "TOUCHED!" yazısı gösterir, parmak kalkınca kaybolur.

import time, json
from pathlib import Path
from PIL import Image, ImageDraw

from lib.LCD_1inch69 import LCD_1inch69

try:
    from smbus2 import SMBus
    SMBUS_OK = True
except Exception:
    SMBUS_OK = False

I2C_BUS = 1
CST816_ADDR = 0x15
CALIB_PATH = Path.home() / ".config" / "pi169_touch.json"
CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)

C = {
    "BG":   (0, 0, 0),
    "FG":   (230, 230, 230),
    "OK":   (90, 200, 120),
    "BAD":  (255, 80, 80),
}

def default_calib():
    return {"swap_xy": True, "invert_x": True, "invert_y": False,
            "xmin": 0, "xmax": 3840, "ymin": 0, "ymax": 3840}

class Touch:
    def __init__(self):
        self.available = SMBUS_OK
        self.bus = None
        self.calib = default_calib()
        if CALIB_PATH.exists():
            try: self.calib.update(json.loads(CALIB_PATH.read_text()))
            except Exception: pass
        if self.available:
            try:
                self.bus = SMBus(I2C_BUS)
                self.bus.read_i2c_block_data(CST816_ADDR, 0x01, 1)
            except Exception:
                self.available = False
                self.bus = None

    def read_raw(self):
        if not self.available: return None
        try:
            d = self.bus.read_i2c_block_data(CST816_ADDR, 0x01, 7)
        except Exception:
            return None
        if (d[1] & 0x0F) == 0:
            return None
        return True  # sadece dokunuldu bilgisini döndür

class App:
    def __init__(self):
        self.lcd = LCD_1inch69()
        self.lcd.Init()
        try: self.lcd.bl_DutyCycle(100)
        except Exception: pass
        self.W, self.H = self.lcd.width, self.lcd.height
        self.touch = Touch()

    def draw_idle(self):
        img = Image.new("RGB", (self.W, self.H), C["BG"])
        d = ImageDraw.Draw(img)
        d.text((8, 8), "Dokun ekran test", fill=C["FG"])
        self.lcd.ShowImage(img)

    def draw_touched(self):
        img = Image.new("RGB", (self.W, self.H), C["BG"])
        d = ImageDraw.Draw(img)
        d.text((self.W//2, self.H//2), "TOUCHED!", fill=C["OK"], anchor="mm")
        self.lcd.ShowImage(img)

    def run(self):
        touching = False
        while True:
            if self.touch.read_raw():
                if not touching:
                    self.draw_touched()
                    touching = True
            else:
                if touching:
                    self.draw_idle()
                    touching = False
            time.sleep(0.05)

if __name__ == "__main__":
    try:
        App().run()
    except KeyboardInterrupt:
        pass
