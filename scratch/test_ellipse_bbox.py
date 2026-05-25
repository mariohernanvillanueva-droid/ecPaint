import sys
from PySide6.QtCore import QRectF, QRect, QPoint, QPointF
from PySide6.QtGui import QImage, QPainter, QBitmap, QRegion, QPainterPath, QColor, QBrush, QPen, qRgb

def _get_pixel_perfect_ellipse_path(rect):
    n_rect = rect.normalized()
    if n_rect.isEmpty():
        return QPainterPath()
    
    r = n_rect.toRect()
    r = r.adjusted(-1, -1, 1, 1)
    
    mask = QImage(r.width(), r.height(), QImage.Format.Format_Mono)
    mask.setColor(0, qRgb(255, 255, 255))
    mask.setColor(1, qRgb(0, 0, 0))
    mask.fill(0)
    
    p = QPainter(mask)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    p.setBrush(QBrush(QColor(qRgb(0, 0, 0))))
    p.setPen(QPen(QColor(qRgb(0, 0, 0))))
    
    local_rect = QRectF(n_rect.translated(-r.topLeft()))
    p.drawEllipse(local_rect)
    p.end()
    
    bitmap = QBitmap.fromImage(mask)
    region = QRegion(bitmap)
    path = QPainterPath()
    for rect_part in region:
        path.addRect(QRectF(rect_part))
    
    return path.simplified().translated(r.topLeft())

if __name__ == '__main__':
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    
    rect_f = QRectF(10, 20, 100, 80)
    path = _get_pixel_perfect_ellipse_path(rect_f)
    print("Input Rect:", rect_f.toRect())
    print("Path Bounding Rect:", path.boundingRect().toRect())
