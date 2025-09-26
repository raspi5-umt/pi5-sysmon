python3 - <<'PY'
import sys,os,time
sys.path.insert(0,"lib")
from LCD_1inch69 import LCD_1inch69
from PIL import Image, ImageDraw
lcd=LCD_1inch69(); lcd.Init()
W,H=lcd.width,lcd.height
def show(c):
    img=Image.new("RGB",(W,H),c)
    d=ImageDraw.Draw(img); d.text((10,10),str(c),fill=(255,255,255))
    lcd.ShowImage(img); time.sleep(0.6)
for c in [(255,0,0),(0,255,0),(0,0,255),(255,255,255)]: show(c)
print("OK")
PY
