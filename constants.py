from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen

BRUSH_MULT = 1
SPRAY_PAINT_MULT = 0.2
SPRAY_PAINT_N = 1

COLORS = [
    "#000000",
    "#808285",
    "#880015",
    "#ffc90e",
    "#008055",
    "#00a2e8",
    "#3f48cc",
    "#81067a",
    "#ffc680",
    "#a2e61b",
    "#940D55",
    "#a349a4",
    "#7e07f9",
    "#8e562e",
    "#ffffff",
    "#c1c1c1",
    "#ed1c24",
    "#fff200",
    "#22b14c",
    "#80d6ff",
    "#004de6",
    "#b92fc2",
    "#ffff80",
    "#80ff9e",
    "#dc137d",
    "#e86aec",
    "#bcb3ff",
    "#ff7f27"
]

FONT_SIZES = [
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    18,
    24,
    36,
    48,
    64,
    72,
    96,
    144,
    288,
]

MODES = [
    "move",
    "eraser",
    "fill",
    "dropper",
    "stamp",
    "pen",
    "brush",
    "spray",
    "text",
    "line",
    "polyline",
    "spline",
    "rect",
    "marker",
    "polygon",
    "ellipse",
    "roundrect",
    "magnifier",
    "regularpoly",
    "simpleline",
    "smudge",
    "gradient",
]

CANVAS_DIMENSIONS = 760, 500

import os
STAMPS_DIR = os.path.join(os.path.dirname(__file__), "stamps")
if os.path.exists(STAMPS_DIR):
    STAMPS = [os.path.join(STAMPS_DIR, f) for f in os.listdir(STAMPS_DIR) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
else:
    STAMPS = []

SELECTION_PEN = QPen(QColor(0xFF, 0xFF, 0xFF), 1, Qt.PenStyle.DashLine)
PREVIEW_PEN = QPen(QColor(0xFF, 0xFF, 0xFF), 1, Qt.PenStyle.SolidLine)
