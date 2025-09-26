# file: touch_probe.py
import time
import sys
import RPi.GPIO as GPIO

# smbus (Debian paketinden) kullan
import smbus

# Donanım pinleri
IRQ_PIN = 4    # GPIO4  (Pin 7)
RST_PIN = 17   # GPIO17 (Pin 11)

# Deneyeceğimiz I2C bus'lar
BUSES = [1, 13, 14]  # sende hangileri varsa listeyi genişletebilirsin

GPIO.setmode(GPIO.BCM)
GPIO.setup(IRQ_PIN, GPIO.IN)
GPIO.setup(RST_PIN, GPIO.OUT)

def hard_reset():
    # Aktif-düşük reset: kısa LOW, sonra HIGH
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.02)
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.05)

def scan_buses():
    found = []
    for b in BUSES:
        try:
            bus = smbus.SMBus(b)
        except FileNotFoundError:
            continue
        for addr in range(0x03, 0x78):
            try:
                bus.read_byte(addr)
                found.append((b, addr))
            except OSError:
                pass
        bus.close()
    return found

def try_read(b, addr):
    try:
        bus = smbus.SMBus(b)
        # pek çok dokunmatik denetleyici 0 register'ından blok okuma verir
        # burada 1 byte dene, hata vermezse cihaz canlıdır
        bus.read_byte(addr)
        bus.close()
        return True
    except Exception:
        return False

try:
    print("TP denetleyici resetleniyor...")
    hard_reset()

    print("I2C taraması (dokunmadan):")
    base = scan_buses()
    print("Bulunan adresler:", ["bus%d:0x%02X" % x for x in base] or ["yok"])

    print("Şimdi ekrana DOKUN ve 5 sn içinde tekrar tarayacağız...")
    t0 = time.time()
    time.sleep(0.5)

    while time.time() - t0 < 5.0:
        if GPIO.input(IRQ_PIN) == 0:
            print("IRQ aktif (dokunma algılandı). Tarama yapılıyor...")
            active = scan_buses()
            new = [x for x in active if x not in base]
            print("Yeni görünenler:", ["bus%d:0x%02X" % x for x in new] or ["yok"])
            # İlk görüneni dene
            candidates = new or active
            for b, addr in candidates:
                if try_read(b, addr):
                    print(f"OK: bus{b} addr=0x{addr:02X} üzerinden okuma başarılı.")
                else:
                    print(f"Uyarı: bus{b} addr=0x{addr:02X} okuma hatası.")
            break
        time.sleep(0.05)

finally:
    GPIO.cleanup()
