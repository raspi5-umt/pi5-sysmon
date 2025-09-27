import time
from PIL import Image, ImageDraw
from LCD_1inch69 import LCD_1inch69
from Touch_1inch69 import Touch_1inch69

W, H = 240, 280

lcd = LCD_1inch69()
lcd.Init()
touch = Touch_1inch69()

print("Dokunmatik test başlıyor... (CTRL+C ile çık)")

try:
    while True:
        pos = None
        try:
            pos = touch.Touch_Read()   # varsa Touch_Read kullan
        except AttributeError:
            try:
                pos = touch.read()     # bazı sürümlerde read()
            except AttributeError:
                pass

        if pos:
            x, y = pos
            print(f"TOUCH: ({x},{y})")

            img = Image.new("RGB", (W, H), "black")
            d = ImageDraw.Draw(img)
            d.ellipse((x-4, y-4, x+4, y+4), fill="red")
            lcd.ShowImage(img)

        time.sleep(0.05)

except KeyboardInterrupt:
    print("Çıkış yapıldı.")
