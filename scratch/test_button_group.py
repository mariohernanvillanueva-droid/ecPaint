import sys
import os
# Add the parent directory containing resources_rc to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from main import MainWindow

app = QApplication(sys.argv)
window = MainWindow()

# Verify that gradientButton exists
assert hasattr(window, "gradientButton"), "gradientButton is missing from MainWindow!"
gradient_btn = window.gradientButton

# Verify that it is checkable
assert gradient_btn.isCheckable(), "gradientButton should be checkable!"

# Verify that it is in the mode_group
buttons_in_group = window.mode_group.buttons()
assert gradient_btn in buttons_in_group, "gradientButton was not added to the exclusive QButtonGroup mode_group!"

print("Exclusivity Verification Passed successfully!")
print(f"Total buttons in mode_group: {len(buttons_in_group)}")
print("Gradient button is present and checkable!")
