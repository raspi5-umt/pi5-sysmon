import smbus2
import RPi.GPIO as GPIO
import time

bus = smbus2.SMBus(1)   # i2c-1 kullanıyoruz
TOUCH_ADDR = 0x15       # i2cdetect çıktındaki adresi yaz

IRQ_PIN = 4             # GPIO4 (Pin 7)
GPIO.setmode(GPIO.BCM)
GPIO.setup(IRQ_PIN, GPIO.IN)

while True:
    if GPIO.input(IRQ_PIN) == 0:  # IRQ aktif
        try:
            data = bus.read_i2c_block_data(TOUCH_ADDR, 0, 16)
            print("Touch data:", data)
        except Exception as e:
            print("I2C error:", e)
    time.sleep(0.1)
