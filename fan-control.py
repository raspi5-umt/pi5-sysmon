#!/usr/bin/env python3
import time
import glob

# Fan kontrol dosyasını bul
hwmon_path = glob.glob("/sys/devices/platform/cooling_fan/hwmon/hwmon*/pwm1")[0]

def set_fan_speed(value: int):
    """0-255 arası hız değeri yazar (0=kapalı, 128=yarı hız, 255=full hız)"""
    with open(hwmon_path, "w") as f:
        f.write(str(value))

def get_temp() -> float:
    """CPU sıcaklığını °C cinsinden döndürür"""
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        return int(f.read()) / 1000.0

while True:
    temp = get_temp()

    if temp < 33:
        set_fan_speed(0)      # 40°C altı: kapalı
    elif temp < 36:
        set_fan_speed(128)    # 40–60°C arası: yarı hız
    else:
        set_fan_speed(255)    # 60°C üstü: tam hız

    print(f"Sıcaklık: {temp:.1f}°C")
    time.sleep(5)  # Her 5 saniyede bir tekrar kontrol et
