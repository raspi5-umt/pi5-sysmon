#!/usr/bin/env python3
# touch_raw_read.py
import time
from smbus2 import SMBus

I2C_BUS = 1
ADDR = 0x15  # CST816S
bus = SMBus(I2C_BUS)

def read_touch():
    # CST816S registers: 0x01=gesture, 0x02=fingers, 0x03..0x06 = XH,XL,YH,YL (12-bit)
    data = bus.read_i2c_block_data(ADDR, 0x01, 7)
    fingers = data[1] & 0x0F
    if fingers == 0:
        return None
    x = ((data[2] & 0x0F) << 8) | data[3]
    y = ((data[4] & 0x0F) << 8) | data[5]
    return x, y, fingers

print("Dokun: ham X,Y değerlerini yazıyorum. Çıkış için Ctrl+C.")
try:
    while True:
        t = read_touch()
        if t:
            x,y,f = t
            print(f"RAW x={x}  y={y}  fingers={f}")
        time.sleep(0.01)
except KeyboardInterrupt:
    pass
