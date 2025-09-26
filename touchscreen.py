#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, subprocess, traceback

# lib/ yolunu ekle ve config'ten RST pinini dene
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

RST_PIN = None
try:
    from lib import config as _cfg
    for name in dir(_cfg):
        if "TP" in name and "RST" in name:
            RST_PIN = getattr(_cfg, name)
            break
except Exception:
    pass

def pulse_reset():
    if RST_PIN is None:
        return "RST pin bulunamadı (config'te isim farklı olabilir)"
    try:
        import gpiod
        chip = gpiod.Chip("gpiochip0")
        line = chip.get_line(RST_PIN)
        line.request(consumer="touchreset", type=gpiod.LINE_REQ_DIR_OUT)
        line.set_value(1); time.sleep(0.01)
        line.set_value(0); time.sleep(0.01)
        line.set_value(1); time.sleep(0.05)
        line.release()
        return f"RST {RST_PIN} pulse OK"
    except Exception as e:
        return f"RST pulse hata: {e}"

CAND_BUSES = [1, 13, 14]  # Pi 5'te yaygın

def scan_buses():
    """0x15'i hangi bus'ta bulursak o numarayı döndür."""
    for b in CAND_BUSES:
        try:
            out = subprocess.check_output(["i2cdetect", "-y", str(b)], text=True, timeout=2)
            if " 15 " in out or " 15" in out or "UU" in out:
                return b
        except Exception:
            pass
    return None

def read_once(bus_no):
    """SMBus ile bir okuma (varsa (x,y) döndür)."""
    from smbus2 import SMBus
    try:
        with SMBus(bus_no) as bus:
            d = bus.read_i2c_block_data(0x15, 0x00, 7)
        if not d: return None
        event = d[1] & 0x0F
        if event == 0: return None
        x = ((d[2] & 0x0F) << 8) | d[3]
        y = ((d[4] & 0x0F) << 8) | d[5]
        return (x, y)
    except Exception:
        return None

def main():
    print("Touch watcher: 0x15 arıyor, bulunduğunda koordinatları akıtacak. CTRL+C ile çık.")
    last_bus = None
    last_state = "INIT"
    lost_count = 0

    while True:
        bus = scan_buses()
        if bus is None:
            if last_state != "LOST":
                print("STATUS: LOST (0x15 görünmüyor). RST deniyorum...")
                print("   ", pulse_reset())
                last_state = "LOST"
                lost_count = 0
            else:
                lost_count += 1
                # çok kaybolduysa arada yeniden resetle
                if lost_count % 20 == 0:
                    print("   tekrar reset:", pulse_reset())
            time.sleep(0.5)
            continue

        if bus != last_bus or last_state != "FOUND":
            print(f"STATUS: FOUND on i2c-{bus} (addr 0x15)")
            last_bus = bus
            last_state = "FOUND"

        pt = read_once(bus)
        if pt:
            print(f"TOUCH b{bus}: {pt[0]}, {pt[1]}")
        time.sleep(0.05)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBitti.")
