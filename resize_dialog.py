from PySide6.QtWidgets import QDialog
from PySide6.QtCore import Qt
from ResizeDialog import Ui_ResizeDialog

class ResizeDialogWindow(QDialog, Ui_ResizeDialog):
    def __init__(self, current_w, current_h, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.current_w = current_w
        self.current_h = current_h
        self.aspect_ratio = current_w / current_h if current_h != 0 else 1

        self.radioPercentage.toggled.connect(self._on_mode_changed)
        self.checkAspectRatio.toggled.connect(self._on_aspect_toggled)
        self.spinHorizontal.valueChanged.connect(self._on_horizontal_changed)
        self.spinVertical.valueChanged.connect(self._on_vertical_changed)
        
        self.is_updating = False

    def _on_mode_changed(self):
        self.is_updating = True
        if self.radioPercentage.isChecked():
            # Switching to percentage
            w_val = (self.spinHorizontal.value() / self.current_w) * 100 if self.current_w != 0 else 100
            h_val = (self.spinVertical.value() / self.current_h) * 100 if self.current_h != 0 else 100
            self.spinHorizontal.setValue(round(w_val))
            self.spinVertical.setValue(round(h_val))
        else:
            # Switching to pixels
            w_val = (self.spinHorizontal.value() / 100.0) * self.current_w
            h_val = (self.spinVertical.value() / 100.0) * self.current_h
            self.spinHorizontal.setValue(round(w_val))
            self.spinVertical.setValue(round(h_val))
        self.is_updating = False

    def _on_aspect_toggled(self, checked):
        if checked:
            self._on_horizontal_changed(self.spinHorizontal.value())

    def _on_horizontal_changed(self, value):
        if self.is_updating or not self.checkAspectRatio.isChecked():
            return
        self.is_updating = True
        if self.radioPercentage.isChecked():
            self.spinVertical.setValue(value)
        else:
            # Mode is pixels
            self.spinVertical.setValue(round(value / self.aspect_ratio))
        self.is_updating = False

    def _on_vertical_changed(self, value):
        if self.is_updating or not self.checkAspectRatio.isChecked():
            return
        self.is_updating = True
        if self.radioPercentage.isChecked():
            self.spinHorizontal.setValue(value)
        else:
            # Mode is pixels
            self.spinHorizontal.setValue(round(value * self.aspect_ratio))
        self.is_updating = False
        
    def get_new_size(self):
        if self.radioPercentage.isChecked():
            w = round((self.spinHorizontal.value() / 100.0) * self.current_w)
            h = round((self.spinVertical.value() / 100.0) * self.current_h)
            return w, h
        else:
            return self.spinHorizontal.value(), self.spinVertical.value()

    def get_method(self):
        return self.comboMethod.currentIndex()
