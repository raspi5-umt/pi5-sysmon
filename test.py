import time
from PIL import Image, ImageDraw
from lib.LCD_1inch69 import LCD_1inch69
from lib.Touch_1inch69 import Touch_1inch69

# Ekran boyutu
W, H = 240, 280  

# LCD ve Touch başlat
lcd = LCD_1inch69()
lcd.Init()
touch = Touch_1inch69()

def scale_map(x, y, w, h, raw_max_x=4095, raw_max_y=4095):
    """Ham koordinatları ekrana ölçekle"""
    sx = int(x / raw_max_x * w)
    sy = int(y / raw_max_y * h)
    return sx, sy

print("Dokunmatik test başlıyor... (CTRL+C ile çık)")

try:
    while True:
        raw = touch.read_raw()
        if raw:
            rx, ry = raw
            x, y = scale_map(rx, ry, W, H)

            print(f"RAW=({rx},{ry})  SCALED=({x},{y})")

            img = Image.new("RGB", (W, H), "black")
            d = ImageDraw.Draw(img)
            d.ellipse((x-3, y-3, x+3, y+3), fill="red")  # Dokunulan yere kırmızı nokta
            lcd.ShowImage(img)

        time.sleep(0.05)

except KeyboardInterrupt:
    print("Çıkış yapıldı.")
