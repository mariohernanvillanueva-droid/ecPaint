import sys
import traceback
from PySide6.QtGui import QImage, QColor, QPainter, QBitmap, QRegion, QGuiApplication
from PySide6.QtCore import Qt, QRect

try:
    app = QGuiApplication(sys.argv)
    img = QImage(100, 100, QImage.Format.Format_ARGB32)
    img.fill(QColor("red"))
    img.setPixelColor(50, 50, QColor("blue"))
    img.setPixelColor(51, 51, QColor("blue"))

    bbox = QRect(0, 0, 100, 100)
    region_img = img.copy(bbox)
    mask = region_img.createMaskFromColor(QColor("blue").rgba(), Qt.MaskMode.MaskOutColor)

    bitmap = QBitmap.fromImage(mask)
    region = QRegion(bitmap)

    img2 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img2.fill(QColor("black"))
    p = QPainter(img2)
    p.setClipRegion(region)
    p.fillRect(0, 0, 100, 100, QColor("white"))
    p.end()

    print(img2.pixelColor(50, 50).name()) # Should be white?
    print(img2.pixelColor(0, 0).name())   # Should be black?
except Exception as e:
    traceback.print_exc()
