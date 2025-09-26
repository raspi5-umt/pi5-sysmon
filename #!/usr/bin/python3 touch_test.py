#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
from smbus2 import SMBus

I2C_BUS = 1       # CST816S genelde i2c-1'de
ADDR = 0x15       # dokunmatik kontrolcü adresi

def read_touch(bus):
    try:
        data = bus.read_i2c_block_data(ADDR, 0x00, 7)
        event = data[1] & 0x0F
        if event == 0:
            return None
        x = ((data[2] & 0x0F) << 8) | data[3]
        y = ((data[4] & 0x0F) << 8) | data[5]
        return (x, y)
    except Exception:
        return None

def main():
    with SMBus(I2C_BUS) as bus:
        print("Dokunmatik test başlıyor... CTRL+C ile çık.")
        while True:
            pt = read_touch(bus)
            if pt:
                print("Dokunma:", pt)
            time.sleep(0.05)

if __name__ == "__main__":
    main()
