#!/usr/bin/env python3
import time
from smbus2 import SMBus

ADDR = 0x15   # CST816S dokunmatik çip adresi
BUS = 14      # hangi i2c bus’ta olduğunu bulmuştun: 13 veya 14 dene

def read_touch(bus):
    try:
        data = bus.read_i2c_block_data(ADDR, 0x00, 7)
        event = data[1] & 0x0F
        if event == 0:  # dokunma yok
            return None
        x = ((data[2] & 0x0F) << 8) | data[3]
        y = ((data[4] & 0x0F) << 8) | data[5]
        return (x, y)
    except Exception:
        return None

def main():
    with SMBus(BUS) as bus:
        print(f"Dokunmatik test başlıyor... (I2C bus {BUS}, addr 0x{ADDR:X})")
        print("Ekrana dokun, koordinatlar yazılacak. Çıkış: CTRL+C")
        try:
            while True:
                pt = read_touch(bus)
                if pt:
                    print("Dokunma:", pt)
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nBitti.")

if __name__ == "__main__":
    main()
