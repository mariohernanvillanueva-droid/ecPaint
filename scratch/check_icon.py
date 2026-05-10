import sys
import os
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

# Add parent dir to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resources_rc

app = QApplication(sys.argv)

paths = [
    ":/icons/program.ico",
    ":/icons/icons/program.ico",
    ":/icons/switch.png",
    ":/icons/icons/switch.png"
]

for path in paths:
    icon = QIcon(path)
    print(f"Path: {path} | isNull: {icon.isNull()}")
    if not icon.isNull():
        print(f"  Sizes: {icon.availableSizes()}")
