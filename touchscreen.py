#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, traceback

# lib/ yolunu ekle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

CST_ADDR = 0x15
CANDIDATE_BUSES = [1, 0, 10, 11]  # Pi 5'te sık görülenler

def try_vendor():
    """Üreticinin Touch_1inch69 sürücüsünü deneyip bir 'read' fonksiyonu bul."""
    try:
        from lib import Touch_1inch69 as TP
    except Exception as e:
        return None, f"vendor import yok: {e}"

    # Modüldeki sınıfı bul
    klass = None
    for name in dir(TP):
        obj = getattr(TP, name)
        if isinstance(obj, type):
            klass = obj
            break
    if not klass:
        return None, "vendor modülde uygun sınıf bulunamadı"

    try:
        dev = klass()
    except Exception as e:
        return None, f"vendor sınıf init hatası: {e}"

    # Olası okuma metodları
    for meth in ("Read_TouchPoint", "read", "get_point", "read_point"):
        if hasattr(dev, meth):
            fn = getattr(dev, meth)
            return (dev, fn), None
    return None, "vendor sınıfta tanınan okuma metodu yok"

def pulse_rst_from_config():
    """lib/config içinden TP_RST pinini bulup libgpiod ile resetle."""
    try:
        from lib import config as cfg
    except Exception as e:
        return f"config import yok: {e}"

    rst_pin = None
    for name in dir(cfg):
        if "TP" in name and "RST" in name:
            rst_pin = getattr(cfg, name)
            break
    if rst_pin is None:
        return "config içinde TP_RST pin bulunamadı"

    try:
        import gpiod
        chip = gpiod.Chip("gpiochip0")
        line = chip.get_line(rst_pin)
        line.request(consumer="touchreset", type=gpiod.LINE_REQ_DIR_OUT)
        line.set_value(1); time.sleep(0.01)
        line.set_value(0); time.sleep(0.01)
        line.set_value(1); time.sleep(0.02)
        line.release()
        return f"RST pin {rst_pin} pulse OK"
    except Exception as e:
        return f"RST pulse başarısız: {e}"

def smbus_find_and_read():
    """SMBus ile bus tarayıp 0x15’ten okuma yap. Başarılı olursa (bus_no, read_fn) döndür."""
    try:
        from smbus2 import SMBus
    except Exception as e:
        return None, f"smbus2 yok: {e}"

    # Reset dene (oluyorsa)
    _ = pulse_rst_from_config()

    for b in CANDIDATE_BUSES:
        try:
            with SMBus(b) as bus:
                data = bus.read_i2c_block_data(CST_ADDR, 0x00, 7)
                # basit okuma fonksiyonu döndür
                def reader():
                    with SMBus(b) as _bus:
                        d = _bus.read_i2c_block_data(CST_ADDR, 0x00, 7)
                    event = d[1] & 0x0F
                    if event == 0:
                        return None
                    x = ((d[2] & 0x0F) << 8) | d[3]
                    y = ((d[4] & 0x0F) << 8) | d[5]
                    return (x, y)
                return (b, reader), None
        except Exception:
            continue
    return None, "0x15 hiçbir bus’ta cevap vermedi"

def main():
    print("Dokunmatik teşhisi başlıyor...")

    # 1) Vendor sürücüyü dene
    vres, verr = try_vendor()
    if vres:
        dev, fn = vres
        print("MOD: vendor (Touch_1inch69)")
        print("Ekrana dokun, koordinatlar akacak. Çıkış: CTRL+C")
        try:
            while True:
                try:
                    pt = fn()
                except TypeError:
                    # bazı vendor API'leri (x,y) tuple yerine dict döndürebilir
                    try:
                        r = fn()
                        if isinstance(r, dict) and "x" in r and "y" in r:
                            pt = (int(r["x"]), int(r["y"]))
                        else:
                            pt = None
                    except Exception:
                        pt = None
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    x, y = int(pt[0]), int(pt[1])
                    print(f"TOUCH (vendor): {x}, {y}")
                time.sleep(0.03)
        except KeyboardInterrupt:
            print("\nBitti.")
            return
    else:
        print(f"Vendor yolu olmadı: {verr}")

    # 2) SMBus ile tarama
    sres, serr = smbus_find_and_read()
    if sres:
        bus_no, reader = sres
        print(f"MOD: i2c/smbus (bus {bus_no}, addr 0x15)")
        print("Ekrana dokun, koordinatlar akacak. Çıkış: CTRL+C")
        try:
            while True:
                pt = reader()
                if pt:
                    x,y = pt
                    print(f"TOUCH (i2c b{bus_no}): {x}, {y}")
                time.sleep(0.03)
        except KeyboardInterrupt:
            print("\nBitti.")
            return
    else:
        print(f"I2C yolu da olmadı: {serr}")
        print("Notlar:")
        print("- I2C devre dışı olabilir: sudo raspi-config → Interface Options → I2C = Enable")
        print("- RST pini LOW'da kalmış olabilir; config’teki TP_RST doğru mu?")
        print("- Bazı panellerde dokunmatik farklı bus’a (0/10/11) bağlıdır.")
        print("- Kernel sürücüsü bağlandıysa i2cdetect 'UU' gösterir; bu durumda vendor sürücüsüyle çalışırsın.")

if __name__ == "__main__":
    main()
