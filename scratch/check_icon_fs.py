import sys
import os
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

# Try loading from file system
# Script is in scratch/, icons is in scratch/../icons/
icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons", "program.ico")
print(f"Absolute path: {icon_path}")
print(f"File exists: {os.path.exists(icon_path)}")

icon = QIcon(icon_path)
print(f"Icon isNull (from file): {icon.isNull()}")
if not icon.isNull():
    print(f"  Sizes: {icon.availableSizes()}")
