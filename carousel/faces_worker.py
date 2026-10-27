"""Face detection in its own process.

OpenCV ships its own libjpeg, and importing cv2 into the same process as Pillow
breaks Pillow's JPEG decoding on some Windows installs. So layout.py calls this
script as a subprocess: argv[1] = PNG path, prints a JSON list of [x, y, w, h].
"""
import json
import sys

import cv2

img = cv2.imread(sys.argv[1], cv2.IMREAD_GRAYSCALE)
cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
faces = cascade.detectMultiScale(img, scaleFactor=1.15, minNeighbors=6, minSize=(35, 35))
print(json.dumps([[int(v) for v in f] for f in faces]))
