#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os, time

# lib yolunu ekle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

# Vendor config içinden RST/INT pinlerini çekmeye çalış
TP_RST_PIN = None
TP_INT_PIN = None

try:
    from lib import config
    # En yaygın isimler; olmayanlar None kalır
    for name in dir(config):
        if "TP" in name and "RST" in name:
            TP_RST_PIN = getattr(config, name)
        if "TP" in name and "INT" in name:
            TP_INT_PIN = getattr(config, name)
except Exception as e:
    print("config import edilemedi:", e)

# GPIOD ile RST pinini pulse’la (RPi.GPIO yerine gpiod daha stabil)
def pulse_reset(pin):
    if pin is None:
        print("RST pini bulunamadı (config’te farklı isimde olabilir). Reset atlamıyorum.")
        return
    try:
        import gpiod
        chip = gpiod.Chip("gpiochip0")
        line = chip.get_line(pin)
        line.request(consumer="touchreset", type=gpiod.LINE_REQ_DIR_OUT)
        line.set_value(1); time.sleep(0.01)
        line.set_value(0); time.sleep(0.01)
        line.set_value(1); time.sleep(0.02)
        line.release()
        print(f"RST pin {pin} pulse ok.")
    except Exception as e:
        print("RST pulse mümkün değil:", e)

pulse_reset(TP_RST_PIN)

# Bus’ları tarayıp 0x15 arayan basit tarayıcı
def find_cst816():
    import subprocess
    candidates = [0,1,10,11]
    found = []
    for b in candidates:
        try:
            out = subprocess.check_output(["i2cdetect","-y",str(b)], text=True)
            if "15" in out or "UU" in out:
                found.append(b)
        except Exception:
            pass
    return found

busses = find_cst816()
if not busses:
    print("0x15 hiçbir bus’ta görünmüyor. Yine de SMBus ile deneyeyim...")
else:
    print("Aday bus’lar:", busses)

# SMBus ile tek okuma dene
try:
    from smbus2 import SMBus
    for b in (busses or [1,0,10,11]):  # hiç bulunmadıysa sırayla dene
        try:
            with SMBus(b) as bus:
                data = bus.read_i2c_block_data(0x15, 0x00, 7)
                print(f"BUS {b}: okuma başarılı:", data)
                print(">> KULLANMAN GEREKEN BUS =", b)
                break
        except Exception as e:
            print(f"BUS {b}: yok/cevap yok: {e}")
except Exception as e:
    print("smbus2 yok veya açılmadı:", e)
