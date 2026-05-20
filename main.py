import random
import sys
import types

import constants
import resources_rc
from canvas import Canvas
from MainWindow import Ui_MainWindow
from resize_dialog import ResizeDialogWindow
import os

from PySide6.QtCore import QPoint, QRect, Qt, QSize, QTimer, QEvent, QSettings
from PySide6.QtGui import (
    QFont,
    QIcon,
    QImage,
    QPixmap,
    QTransform,
    QKeySequence,
    QAction,
    QActionGroup,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QLabel,
    QMainWindow,
    QSlider,
    QScrollArea,
    QPushButton,
    QToolButton,
    QToolBar,
    QMenu,
    QSpinBox,
    QCheckBox,
)

# Not actually required, the import triggers this already.
resources_rc.qInitResources()


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.current_file_path = None
        self.update_window_title()

        # Set the window icon explicitly for this window
        self.setWindowIcon(QIcon(":/icons/program.ico"))

        # Enforce a sane minimum size so the centralWidget never collapses or
        # shifts left when the window is dragged to its narrowest position.
        # toolsDock (85px) + canvas_scroll (200px) + some padding.
        self.setMinimumSize(300, 300)
        
        # Set initial window height
        self.resize(900, 700)



        # Replace canvas placeholder from QtDesigner: put Canvas inside a scroll area
        self.verticalLayoutC.removeWidget(self.canvas)
        self.canvas = Canvas()
        self.canvas.initialize()
        # We need to enable mouse tracking to follow the mouse without the button pressed.
        self.canvas.setMouseTracking(True)
        # Enable focus to capture key inputs.
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Put canvas inside a scroll area so large images can be scrolled
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidget(self.canvas)
        self.canvas_scroll.setWidgetResizable(False)
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.canvas_scroll.setMinimumSize(200, 200)
        self.canvas_scroll.viewport().setStyleSheet("background-color: #888;")
        self.canvas_scroll.viewport().installEventFilter(self)
        self.verticalLayoutC.insertWidget(0, self.canvas_scroll)

        # Setup the mode buttons
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)

        for mode in constants.MODES:
            try:
                # Special case: line mode is triggered by arrowButton
                btn_name = "arrowButton" if mode == "line" else f"{mode}Button"
                btn = getattr(self, btn_name)
                btn.clicked.connect(lambda checked=False, mode=mode: self.set_mode(mode))
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.mode_group.addButton(btn)
            except AttributeError:
                # Some modes like selection might be grouped differently
                continue

        # Setup the color selection buttons.
        self.primaryButton.pressed.connect(
            lambda: self.choose_color(self.set_primary_color, self.canvas.primary_color)
        )
        self.secondaryButton.pressed.connect(
            lambda: self.choose_color(self.set_secondary_color, self.canvas.secondary_color)
        )
        self.primaryButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.secondaryButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.switchColors.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.switchColors.clicked.connect(self.invert_colors)
        self.bnButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bnButton.clicked.connect(lambda: (self.set_primary_color("#000000"), self.set_secondary_color("#ffffff")))

        # Initialize button colours.
        for n, hex in enumerate(constants.COLORS, 1):
            btn = getattr(self, "colorButton_%d" % n)
            self._update_button_color(btn, hex)
            btn.hex = hex  # For use in the event below

            def patch_mousePressEvent(self_, e):
                if e.button() == Qt.MouseButton.LeftButton:
                    self.set_primary_color(self_.hex)

                elif e.button() == Qt.MouseButton.RightButton:
                    self.set_secondary_color(self_.hex)

            btn.mousePressEvent = types.MethodType(patch_mousePressEvent, btn)

        # Edit menu signals
        self.actionUndo.triggered.connect(lambda: self.canvas.undo())
        self.actionRedo.triggered.connect(lambda: self.canvas.redo())
        self.actionCopy.triggered.connect(self.copy_to_clipboard)
        self.actionCut.triggered.connect(lambda: self.canvas.cut_selection())
        # Ensure Paste actions are part of the menu so shortcuts work
        self.menuEdit.insertAction(self.actionSelectAll, self.actionPaste)
        self.menuEdit.insertAction(self.actionSelectAll, self.actionPasteAsNew)
        self.menuEdit.insertSeparator(self.actionSelectAll)

        self.actionPaste.triggered.connect(self.paste_from_clipboard)
        self.actionPasteAsNew.triggered.connect(self.paste_as_new_image)
        self.actionSelectAll.triggered.connect(self.select_all)
        self.actionInvertSelection.triggered.connect(self.canvas.invert_selection)
        self.actionDeselect.triggered.connect(self.canvas.deselect)

        self.actionInvertSelection.setShortcut("Ctrl+Shift+I")
        self.actionRotateRight.setShortcut("Ctrl+R")

        # Ensure shortcuts work regardless of which widget has focus
        for action in [
            self.actionUndo, self.actionRedo, self.actionCopy, self.actionCut,
            self.actionPaste, self.actionPasteAsNew, self.actionSelectAll, 
            self.actionInvertSelection, self.actionDeselect, self.actionRotateRight
        ]:
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)

        # Initialize animation timer.
        self.timer = QTimer()
        self.timer.timeout.connect(self.canvas.on_timer)
        self.timer.setInterval(100)
        self.timer.start()
        
        self.setup_canvas_shortcuts()

    def setup_canvas_shortcuts(self):
        # Create shortcuts for arrow keys to ensure they work regardless of focus
        def move_left():
             step = 10 if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 1
             if getattr(self.canvas, "is_moving_shape", False) or self.canvas.mode in ["paste", "text"]:
                  self.canvas._move_active_shape(-step, 0)
        
        def move_right():
             step = 10 if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 1
             if getattr(self.canvas, "is_moving_shape", False) or self.canvas.mode in ["paste", "text"]:
                  self.canvas._move_active_shape(step, 0)
        
        def move_up():
             step = 10 if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 1
             if getattr(self.canvas, "is_moving_shape", False) or self.canvas.mode in ["paste", "text"]:
                  self.canvas._move_active_shape(0, -step)
        
        def move_down():
             step = 10 if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 1
             if getattr(self.canvas, "is_moving_shape", False) or self.canvas.mode in ["paste", "text"]:
                  self.canvas._move_active_shape(0, step)
        
        def abort():
             if getattr(self.canvas, "is_moving_shape", False) or self.canvas.mode in ["paste", "text"]:
                  self.canvas.abort_operation()
                  self.canvas.reset_mode()

        QShortcut(QKeySequence(Qt.Key_Left), self, move_left)
        QShortcut(QKeySequence(Qt.Key_Right), self, move_right)
        QShortcut(QKeySequence(Qt.Key_Up), self, move_up)
        QShortcut(QKeySequence(Qt.Key_Down), self, move_down)
        QShortcut(QKeySequence(Qt.Key_Escape), self, abort)

        # Setup to agree with Canvas.
        self.set_primary_color("#22b14c")
        self.set_secondary_color("#ffffff")

        # Signals for canvas-initiated color changes (dropper).
        self.canvas.primary_color_updated.connect(self.set_primary_color)
        self.canvas.secondary_color_updated.connect(self.set_secondary_color)

        # Setup the stamp state.
        self.current_stamp_n = -1
        # self.next_stamp()
        # self.stampnextButton.pressed.connect(self.next_stamp)
        # self.stampnextButton.hide()  # Invisible until stamp tool is selected

        self.previous_tool_mode = "pen" # Memory for auto-revert logic
        self.canvas.color_picked.connect(self.revert_tool)

        # Menu options connections are handled below in the File Menu setup section
        
        self.actionClearImage.triggered.connect(self.canvas.reset)
        self.actionInvertColors.triggered.connect(self.invert)
        self.actionFlipHorizontal.triggered.connect(self.flip_horizontal)
        self.actionFlipVertical.triggered.connect(self.flip_vertical)
        self.actionRotateRight.triggered.connect(self.rotate_right)
        self.actionZoomIn.triggered.connect(self.canvas.zoom_in)
        self.actionZoomOut.triggered.connect(self.canvas.zoom_out)
        self.actionNoZoom.triggered.connect(self.canvas.zoom_reset)


        # Enable/disable based on canvas signals
        self.canvas.undo_available.connect(lambda v: self.actionUndo.setEnabled(v))
        self.canvas.redo_available.connect(lambda v: self.actionRedo.setEnabled(v))
        # Ensure menu buttons reflect current canvas state
        self.actionUndo.setEnabled(self.canvas.can_undo())
        self.actionRedo.setEnabled(self.canvas.can_redo())
 
        # Setup File Menu additions: Recent Files listed directly before Exit
        self.recent_actions = []
        # Identify the separators we'll use to sandwich the recent files list
        separators = [a for a in self.menuFIle.actions() if a.isSeparator()]
        # Header is the second to last separator, Footer is the last one (before Exit)
        self.recent_header_sep = separators[-2] if len(separators) >= 2 else None
        self.recent_footer_sep = separators[-1] if len(separators) >= 1 else None
        
        # Connect actions defined in UI
        self.actionNewImage.triggered.connect(self.new_image)
        self.actionOpenImage.triggered.connect(self.open_file)
        self.actionSave.triggered.connect(self.save_file)
        self.actionSaveAs.triggered.connect(self.save_file_as)
        self.actionExit.triggered.connect(self.close)
        self.actionResize.triggered.connect(self.show_resize_dialog)

        self.update_recent_files_menu()

        # Setup the drawing toolbar.
        self.fontselect = QFontComboBox()
        self.fontselect.setFontFilters(QFontComboBox.FontFilter.ScalableFonts)
        self.fontToolbar.addWidget(self.fontselect)
        self.fontselect.currentFontChanged.connect(
            lambda f: self.canvas.set_config("font", f)
        )
        self.fontselect.setCurrentFont(QFont("Times"))

        self.fontsize = QComboBox()
        self.fontsize.addItems([str(s) for s in constants.FONT_SIZES])
        self.fontsize.currentTextChanged.connect(
            lambda f: self.canvas.set_config("fontsize", int(f))
        )
        self.fontsize.setCurrentText("18")
        # Connect to the signal producing the text of the current selection. Convert the string to float
        # and set as the pointsize. We could also use the index + retrieve from FONT_SIZES.
        self.fontToolbar.addWidget(self.fontsize)

        self.actionBold.triggered.connect(lambda s: self.canvas.set_config("bold", s))
        self.actionItalic.triggered.connect(
            lambda s: self.canvas.set_config("italic", s)
        )
        self.actionUnderline.triggered.connect(
            lambda s: self.canvas.set_config("underline", s)
        )

        # Setup the existing stroke size UI from the .ui file (bottom toolbar)
        self.stroke_values = [1, 4, 8, 16, 32, 64, 96, 128]
        self.strokesize.setRange(0, len(self.stroke_values) - 1)
        self.strokesize.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.strokesize.setTickInterval(1)
        
        def update_stroke(index):
            size = self.stroke_values[index]
            self.canvas.set_config("size", size)
            # 'label' is the name of the QLabel next to 'strokesize' in the bottom widget_2
            self.label.setText(f"Stroke: {size}")
            
            # If smudge tool is selected, sync stroke size to smudge radius
            if self.canvas.mode == "smudge" and hasattr(self, "smudgeRadiusSpin"):
                self.smudgeRadiusSpin.setValue(size)

        self.strokesize.valueChanged.connect(update_stroke)
        
        # Add "Arrow?" checkbox below stroke slider
        self.arrowCheckbox = QCheckBox("Arrow?")
        self.arrowCheckbox.setStyleSheet("font-size: 12px; margin-left: 2px; margin-top: 16px; spacing: 4px;")
        self.backLayout.insertWidget(self.backLayout.indexOf(self.strokesize) + 1, self.arrowCheckbox)
        self.arrowCheckbox.toggled.connect(lambda checked: self.canvas.set_config("line_type", 1 if checked else 0))
        self.arrowCheckbox.hide()

        # Add "Antialias?" checkbox below stroke slider
        self.antialiasCheckbox = QCheckBox("Antialias?")
        self.antialiasCheckbox.setStyleSheet("font-size: 12px; margin-left: 2px; margin-top: 16px; spacing: 4px;")
        self.backLayout.insertWidget(self.backLayout.indexOf(self.strokesize) + 1, self.antialiasCheckbox)
        self.antialiasCheckbox.toggled.connect(lambda checked: self.canvas.set_config("antialias", checked))
        self.antialiasCheckbox.hide()

        # Add "Smooth?" checkbox below Antialias checkbox
        self.smoothCheckbox = QCheckBox("Smooth?")
        self.smoothCheckbox.setStyleSheet("font-size: 12px; margin-left: 2px; margin-top: 16px; spacing: 4px;")
        self.backLayout.insertWidget(self.backLayout.indexOf(self.antialiasCheckbox) + 1, self.smoothCheckbox)
        self.smoothCheckbox.toggled.connect(lambda checked: self.canvas.set_config("smooth", checked))
        self.smoothCheckbox.hide()

        # Add "No Fill" checkbox below smooth checkbox
        self.textNoFillCheckbox = QCheckBox("No Fill")
        self.textNoFillCheckbox.setStyleSheet("font-size: 12px; margin-left: 2px; margin-top: 16px; spacing: 4px;")
        self.backLayout.insertWidget(self.backLayout.indexOf(self.smoothCheckbox) + 1, self.textNoFillCheckbox)
        self.textNoFillCheckbox.toggled.connect(lambda checked: self.canvas.set_config("text_no_fill", checked))
        self.textNoFillCheckbox.hide()

        # Add Gradient Type label and combo box below strokesize slider
        self.gradientLabel = QLabel("Type:")
        self.gradientLabel.setStyleSheet("font-size: 11px; font-weight: bold; margin-left: 2px; margin-top: 16px; color: #495057;")
        self.backLayout.insertWidget(self.backLayout.indexOf(self.strokesize) + 1, self.gradientLabel)

        self.gradientCombo = QComboBox()
        self.gradientCombo.addItems(["Linear", "Radial", "Conical", "Rect"])
        self.gradientCombo.setCurrentIndex(0)
        self.gradientCombo.setFixedWidth(66)
        self.gradientCombo.setStyleSheet("""
            QComboBox {
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 2px 20px 2px 4px;
                background: white;
                font-size: 11px;
                color: #495057;
            }
            QComboBox:focus {
                border-color: #80bdff;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                selection-background-color: #0078d7;
                selection-color: #ffffff;
            }
        """)
        self.backLayout.insertWidget(self.backLayout.indexOf(self.gradientLabel) + 1, self.gradientCombo)

        def change_gradient_type(text):
            self.canvas.set_config("gradient_type", text.lower())

        self.gradientCombo.currentTextChanged.connect(change_gradient_type)
        self.gradientLabel.hide()
        self.gradientCombo.hide()

        # Hide arrowButton as it is no longer needed
        # self.arrowButton.hide()

        # Initial state
        self.strokesize.setValue(3) # Default to 16
        update_stroke(3)

        # Setup opacity slider and label
        def update_opacity(value):
            self.opacityLabel.setText(f"Opacity: {value}%")
            self.canvas.set_config("opacity", value)

        self.opacitySlider.valueChanged.connect(update_opacity)
        self.opacitySlider.setValue(100)
        update_opacity(100)

        # Setup shape mode group (exclusive)
        self.shape_mode_group = QActionGroup(self)
        self.shape_mode_group.addAction(self.actionContour)
        self.shape_mode_group.addAction(self.actionOnlyFill)
        self.shape_mode_group.addAction(self.actionFillShapes)
        self.shape_mode_group.setExclusive(True)

        # Setup button group for the dock buttons
        self.fill_mode_button_group = QButtonGroup(self)
        self.fill_mode_button_group.addButton(self.contourButton)
        self.fill_mode_button_group.addButton(self.onlyFillButton)
        self.fill_mode_button_group.addButton(self.fillShapesButton)
        self.fill_mode_button_group.setExclusive(True)

        def set_fill_mode(contour, fill, action, button):
             self.canvas.set_config_multiple({"contour": contour, "fill": fill})
             action.setChecked(True)
             button.setChecked(True)

        self.actionContour.triggered.connect(lambda: set_fill_mode(True, False, self.actionContour, self.contourButton))
        self.contourButton.clicked.connect(lambda: set_fill_mode(True, False, self.actionContour, self.contourButton))
        
        self.actionOnlyFill.triggered.connect(lambda: set_fill_mode(False, True, self.actionOnlyFill, self.onlyFillButton))
        self.onlyFillButton.clicked.connect(lambda: set_fill_mode(False, True, self.actionOnlyFill, self.onlyFillButton))
        
        self.actionFillShapes.triggered.connect(lambda: set_fill_mode(True, True, self.actionFillShapes, self.fillShapesButton))
        self.fillShapesButton.clicked.connect(lambda: set_fill_mode(True, True, self.actionFillShapes, self.fillShapesButton))
        
        # Initial state: Only Fill
        self.actionOnlyFill.setChecked(True)
        self.onlyFillButton.setChecked(True)
        self.canvas.set_config_multiple({"contour": False, "fill": True})
        
        # self.drawingToolbar.hide()
        self.actionShowToolProperties.setChecked(False)
        self.actionShowToolProperties.triggered.connect(lambda: self.set_mode(self.canvas.mode))

        # Setup background fill buttons group for pasting functionality (Exclusive)
        paste_fill_group = QButtonGroup(self)
        paste_fill_group.addButton(self.solidBack)
        paste_fill_group.addButton(self.transBack)
        paste_fill_group.setExclusive(True)
        
        paste_btn_style = "QPushButton { border: 0; left: 0; margin: 0; border-radius: 0px; } QPushButton:checked { background-color: #6284FF; } QPushButton:pressed { background-color: #d6d6d6; }"
        self.solidBack.setStyleSheet(paste_btn_style)
        self.transBack.setStyleSheet(paste_btn_style)

        # Connect buttons to canvas paste_fill config
        self.solidBack.toggled.connect(lambda s: self.canvas.set_config("paste_fill", s))
        
        # Initial state sync for pasting
        self.canvas.set_config("paste_fill", self.solidBack.isChecked())

        # Set focus policy for extra buttons to ensure focus stays on canvas
        self.selectToolButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.solidBack.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.transBack.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # self.stampnextButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Final cleanup: Hide viewToolbar as requested
        # self.viewToolbar.hide()

        # Setup Magic Wand Widgets at Runtime (No Sliders)
        self.toleranceSpin = QSpinBox()
        self.toleranceSpin.setRange(0, 255)
        self.toleranceSpin.setValue(32)
        self.toleranceSpin.setFixedWidth(80)
        self.toleranceSpin.setStyleSheet("""
            QSpinBox {
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 2px 20px 2px 4px; /* Leave space for arrows */
                background: white;
            }
            QSpinBox:focus {
                border-color: #80bdff;
            }
        """)

        # Rebuild toolbar to ensure order: Label -> Spin
        self.wandToolbar.clear()
        self.labelTolerance = QLabel("Tolerance: ")
        self.labelTolerance.setStyleSheet("font-weight: bold; color: #495057;")
        self.wandToolbar.addWidget(self.labelTolerance)
        self.wandToolbar.addWidget(self.toleranceSpin)

        self.toleranceSpin.valueChanged.connect(lambda v: self.canvas.set_config("tolerance", v))
        self.wandToolbar.hide()



        # Setup Poly Widgets at Runtime (No Sliders)
        self.polyVerticesSpin = QSpinBox()
        self.polyVerticesSpin.setRange(3, 100)
        self.polyVerticesSpin.setValue(5)
        self.polyVerticesSpin.setFixedWidth(80)
        self.polyVerticesSpin.setStyleSheet(self.toleranceSpin.styleSheet())
        
        # Rebuild toolbar to ensure order: Label -> Spin
        self.polyToolbar.clear()
        self.labelVertices = QLabel("Vertices: ")
        self.labelVertices.setStyleSheet("font-weight: bold; color: #495057;")
        self.polyToolbar.addWidget(self.labelVertices)
        self.polyToolbar.addWidget(self.polyVerticesSpin)
        
        self.polyVerticesSpin.valueChanged.connect(lambda v: self.canvas.set_config("poly_vertices", v))
        self.canvas.set_config("poly_vertices", 5)
        self.polyToolbar.hide()

        # Setup Smudge Widgets at Runtime
        self.smudgeRadiusSlider = QSlider(Qt.Orientation.Horizontal)
        self.smudgeRadiusSlider.setRange(1, 150)
        self.smudgeRadiusSlider.setValue(20)
        self.smudgeRadiusSlider.setFixedWidth(80)
        # Correct placement in smudgeToolbar:
        # Currently in UI: [labelSmudgeRadius] [separator] [labelSmudgePressure]
        # We want: [labelSmudgeRadius] [smudgeRadiusSlider] [smudgeRadiusSpin] [separator] [labelSmudgePressure] [smudgePressureSlider] [smudgePressureSpin]
        
        # I'll find the separator index.
        # Actually, I'll just clear the toolbar and re-add everything? No, user wants labels in UI.
        
        # I'll use insertWidget to place them correctly.
        # For wand and poly, it was easy because there's only one label.
        
        # For smudge:
        self.smudgeRadiusSlider = QSlider(Qt.Orientation.Horizontal)
        self.smudgeRadiusSlider.setRange(1, 150)
        self.smudgeRadiusSlider.setValue(20)
        # Setup Smudge Widgets at Runtime (No Sliders)
        self.smudgeRadiusSpin = QSpinBox()
        self.smudgeRadiusSpin.setRange(1, 150)
        self.smudgeRadiusSpin.setValue(20)
        self.smudgeRadiusSpin.setFixedWidth(80)
        self.smudgeRadiusSpin.setStyleSheet(self.toleranceSpin.styleSheet())
        
        self.smudgePressureSpin = QSpinBox()
        self.smudgePressureSpin.setRange(1, 100)
        self.smudgePressureSpin.setValue(50)
        self.smudgePressureSpin.setFixedWidth(80)
        self.smudgePressureSpin.setStyleSheet(self.toleranceSpin.styleSheet())

        # Create labels at runtime
        self.labelSmudgeRadius = QLabel("Radius: ")
        self.labelSmudgeRadius.setStyleSheet("font-weight: bold; color: #495057;")
        self.labelSmudgePressure = QLabel("Pressure: ")
        self.labelSmudgePressure.setStyleSheet("font-weight: bold; color: #495057;")

        # Rebuild toolbar to ensure order: Label -> Spin -> Separator -> Label -> Spin
        self.smudgeToolbar.clear()
        self.smudgeToolbar.addWidget(self.labelSmudgeRadius)
        self.smudgeToolbar.addWidget(self.smudgeRadiusSpin)
        self.smudgeToolbar.addSeparator()
        self.smudgeToolbar.addWidget(self.labelSmudgePressure)
        self.smudgeToolbar.addWidget(self.smudgePressureSpin)
        
        self.smudgeRadiusSpin.valueChanged.connect(lambda v: self.canvas.set_config("smudge_radius", v))
        self.smudgePressureSpin.valueChanged.connect(lambda v: self.canvas.set_config("smudge_pressure", v))
        
        self.smudgeToolbar.hide()

        # Selection Tool Grouping Logic - NOW MANAGED VIA UI FILE
        self.last_selection_mode = "selectrect"
        
        selection_configs = [
            ("selectrect", self.actionSelectRect),
            ("selectellipse", self.actionSelectEllipse),
            ("selectpoly", self.actionSelectPoly),
            ("selectfree", self.actionSelectFree),
            ("selectwand", self.actionSelectWand),
        ]
        
        self.selection_action_group = QActionGroup(self)
        self.selection_action_group.setExclusive(True)

        for mode, action in selection_configs:
            self.selection_action_group.addAction(action)
            def make_callback(m, a):
                return lambda: self.change_selection_tool(m, a.icon(), a)
            action.triggered.connect(make_callback(mode, action))

        # Initial state sync
        self.actionSelectRect.setChecked(True)

        # Hide the flyout menu from the main button as requested
        self.selectToolButton.setMenu(None) 
        self.selectToolButton.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.selectToolButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.selectToolButton.clicked.connect(lambda: self.set_mode(self.last_selection_mode))
        self.mode_group.addButton(self.selectToolButton)

        # Shape Tool Grouping Logic - NOW MANAGED VIA UI FILE
        self.last_shape_mode = "rect"
        
        # Map modes to their UI actions for connection
        shape_configs = [
            ("rect", self.actionRect),
            ("ellipse", self.actionEllipse),
            ("roundrect", self.actionRoundRect),
            ("polygon", self.actionPolygon),
            ("regularpoly", self.actionRegularPoly),
        ]
        
        self.shape_action_group = QActionGroup(self)
        self.shape_action_group.setExclusive(True)

        for i, (mode, action) in enumerate(shape_configs):
            self.shape_action_group.addAction(action)
            # Use a closure to capture variables correctly
            def make_callback(m, a):
                return lambda: self.change_shape_tool(m, a.icon(), a)
            action.triggered.connect(make_callback(mode, action))

        # Initial state sync
        self.actionRect.setChecked(True)

        # Hide the flyout menu from the main button as requested
        self.shapeToolButton.setMenu(None) 
        self.shapeToolButton.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.shapeToolButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # self.mode_group.addButton(self.shapeToolButton) # Removed as it is now hidden and replaced by individual buttons
        
        # Ensure the toolbar is hidden initially unless a shape tool is active
        # self.shapeSelectionToolbar.hide()

        # Line Tool Grouping Logic
        self.last_line_mode = "simpleline"
        
        line_configs = [
            ("spline", self.actionSpline),
            ("simpleline", self.actionSimpleLine),
            ("line", self.actionLine),
            ("polyline", self.actionPolyline),
        ]
        
        self.line_action_group = QActionGroup(self)
        self.line_action_group.setExclusive(True)

        for mode, action in line_configs:
            self.line_action_group.addAction(action)
            def make_callback(m, a):
                return lambda: self.change_line_tool(m, a.icon(), a)
            action.triggered.connect(make_callback(mode, action))

        self.actionSimpleLine.setChecked(True)
        self.lineToolButton.setMenu(None)
        self.lineToolButton.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.lineToolButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # self.mode_group.addButton(self.lineToolButton) # Removed as it is now hidden and replaced by individual buttons
        
        if hasattr(self, 'lineSelectionToolbar'):
            self.lineSelectionToolbar.hide()

        # Setup statusbar with cursor position, zoom, and canvas dimensions
        self.status_coords = QLabel("    (0, 0)")
        self.statusBar.addWidget(self.status_coords)
        
        self.status_color = QLabel("")
        self.statusBar.addWidget(self.status_color)
        
        self.status_mode = QLabel("")
        self.statusBar.addWidget(self.status_mode, stretch=1)
        
        self.status_zoom = QLabel("   1:1")
        self.statusBar.addPermanentWidget(self.status_zoom)

        self.status_dimensions = QLabel("")
        self.statusBar.addPermanentWidget(self.status_dimensions)
        
        # Connect canvas signals to statusbar updates
        self.canvas.mouse_pos_changed.connect(
            lambda x, y: self.status_coords.setText(f"    ({x}, {y})")
        )
        self.canvas.zoom_level_changed.connect(
            lambda _: self.status_zoom.setText(f"   {int(self.canvas.scale)}:1" if self.canvas.scale >= 1.0 else f"   1:{int(1.0/self.canvas.scale)}")
        )
        self.canvas.canvas_dimensions_changed.connect(
            lambda w, h: self.status_dimensions.setText(f"    Image: {w} x {h} pixels")
        )
        self.canvas.color_hovered.connect(self.update_status_color)
        self.canvas.selection_dimensions_changed.connect(self.update_status_selection_dimensions)
        
        # Guide messages update
        self.canvas.status_message_changed.connect(
            lambda text: self.status_mode.setText(text)
        )
        
        # Show initial canvas dimensions and zoom
        if self.canvas._image_pixmap:
            w = self.canvas._image_pixmap.width()
            h = self.canvas._image_pixmap.height()
            self.status_dimensions.setText(f"    Image: {w} × {h} pixels")
        
        if hasattr(self.canvas, "scale"):
            scale = self.canvas.scale
            self.status_zoom.setText(f"   {int(scale)}:1" if scale >= 1.0 else f"   1:{int(1.0/scale)}")

        self.transFrame.hide()

        # Connect topLevelChanged to force the toolsDock to shrink when detached
        self.toolsDock.topLevelChanged.connect(
            lambda floating: self.toolsDock.adjustSize() if floating else None
        )

        # Default mode: select the selection tool which defaults to rectangle
        self.selectToolButton.click()

        # Ensure Fill Modes (drawingToolbar) appears after any other toolbar
        # self.removeToolBar(self.drawingToolbar)
        # self.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.drawingToolbar)

        self.show()

    def eventFilter(self, source, event):
        # If clicking the empty background of the scroll area (outside the canvas)
        if hasattr(self, 'canvas_scroll') and source == self.canvas_scroll.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                # Commit any active selection or floating paste operation
                if self.canvas.selectionActive or self.canvas.locked or self.canvas.mode == "paste":
                     self.canvas.finalize_operation()
                
                # Relay focus and grab to canvas for seamless off-canvas interaction
                self.canvas.setFocus()
                self.canvas.grabMouse()
        
        if source == self.selectToolButton:
            return False
                
        return super().eventFilter(source, event)

    def keyPressEvent(self, event):
        # Dispatch specific keys to canvas if it has an active moving shape
        is_moving = getattr(self.canvas, "is_moving_shape", False) or self.canvas.mode in ["paste", "text"]
        if is_moving:
            self.canvas.keyPressEvent(event)
            if event.isAccepted():
                return
        
        super().keyPressEvent(event)

    def change_selection_tool(self, mode, icon, action=None):
        self.last_selection_mode = mode # Save the choice in memory
        self.set_mode(mode)
        self.selectToolButton.setIcon(icon)
        self.selectToolButton.setChecked(True)
        if action:
            action.setChecked(True)
        else:
            mode_to_action = {
                "selectrect": self.actionSelectRect,
                "selectellipse": self.actionSelectEllipse,
                "selectpoly": self.actionSelectPoly,
                "selectfree": self.actionSelectFree,
                "selectwand": self.actionSelectWand,
            }
            if mode in mode_to_action:
                mode_to_action[mode].setChecked(True)

    def change_shape_tool(self, mode, icon, action=None):
        self.last_shape_mode = mode # Save the choice in memory
        self.set_mode(mode)
        # Sync the action check state if triggered from outside the toolbar
        if action:
            action.setChecked(True)
        else:
            # Sync toolbar action state based on the mode
            mode_to_action = {
                "rect": self.actionRect,
                "ellipse": self.actionEllipse,
                "roundrect": self.actionRoundRect,
                "polygon": self.actionPolygon,
                "regularpoly": self.actionRegularPoly,
            }
            if mode in mode_to_action:
                mode_to_action[mode].setChecked(True)

    def change_line_tool(self, mode, icon, action=None):
        self.last_line_mode = mode # Save the choice in memory
        self.set_mode(mode)
        if action:
            action.setChecked(True)
        else:
            mode_to_action = {
                "spline": self.actionSpline,
                "simpleline": self.actionSimpleLine,
                "line": self.actionLine,
                "polyline": self.actionPolyline,
            }
            if mode in mode_to_action:
                mode_to_action[mode].setChecked(True)

    def choose_color(self, callback, initial_color=None):
        dlg = QColorDialog()
        if initial_color:
            # Ensure we pass a QColor or valid color string to setCurrentColor
            dlg.setCurrentColor(initial_color)
        if dlg.exec():
            callback(dlg.selectedColor().name())

    def set_mode(self, mode):
        self.canvas.setFocus()
        # Remember previous tool if switching to dropper
        if mode == "dropper" and self.canvas.mode != "dropper":
            self.previous_tool_mode = self.canvas.mode
        
        # When leaving dropper mode, or switching tools, ensure buttons show the actual committed colors
        if self.canvas.mode == "dropper" and mode != "dropper":
            # Revert UI to match actual canvas state (clearing any hover preview)
            self.primaryButton.setStyleSheet("QPushButton { background-color: %s; }" % self.canvas.primary_color.name())
            if self.canvas.secondary_color:
                self.secondaryButton.setStyleSheet("QPushButton { background-color: %s; }" % self.canvas.secondary_color.name())

        self.canvas.set_mode(mode)

        # Sync current stroke size to smudge radius when selecting the tool
        if mode == "smudge" and hasattr(self, "smudgeRadiusSpin"):
            size = self.stroke_values[self.strokesize.value()]
            self.smudgeRadiusSpin.setValue(size)

        # Update Tool Button Selection State
        if "select" in mode:
            self.selectToolButton.setChecked(True)
        elif mode in ["spline", "line", "polyline", "simpleline"]:
            # Line tools logic handled by their respective buttons
            pass
        elif hasattr(self, f"{mode}Button"):
            getattr(self, f"{mode}Button").setChecked(True)

        # Show/Hide Wand Toolbar
        if mode == "selectwand" and self.actionShowToolProperties.isChecked():
            self.wandToolbar.show()
        else:
            self.wandToolbar.hide()

        # Show/Hide Arrow Checkbox (always show when a line tool is active)
        is_line_tool = mode in ["line", "simpleline"]
        self.arrowCheckbox.setVisible(is_line_tool)
        if is_line_tool:
            # Sync checkbox to canvas config without firing the toggled signal
            self.arrowCheckbox.blockSignals(True)
            self.arrowCheckbox.setChecked(self.canvas.config.get("line_type", 0) == 1)
            self.arrowCheckbox.blockSignals(False)

        # Show/Hide Antialias Checkbox
        antialias_modes = ["pen", "brush", "marker", "simpleline", "spline",
                           "ellipse", "rect", "polygon", "roundrect", "regularpoly", "smudge", "spray"]
        is_antialias_tool = mode in antialias_modes
        self.antialiasCheckbox.setVisible(is_antialias_tool)
        if is_antialias_tool:
            self.antialiasCheckbox.blockSignals(True)
            self.antialiasCheckbox.setChecked(self.canvas.config.get("antialias", False))
            self.antialiasCheckbox.blockSignals(False)

        # Show/Hide Smooth Checkbox
        smooth_modes = ["pen", "brush", "marker", "eraser", "spray"]
        is_smooth_tool = mode in smooth_modes
        self.smoothCheckbox.setVisible(is_smooth_tool)
        if is_smooth_tool:
            self.smoothCheckbox.blockSignals(True)
            self.smoothCheckbox.setChecked(self.canvas.config.get("smooth", False))
            self.smoothCheckbox.blockSignals(False)

        # Show/Hide Text No Fill Checkbox
        is_text_tool = mode == "text"
        self.textNoFillCheckbox.setVisible(is_text_tool)
        if is_text_tool:
            self.textNoFillCheckbox.blockSignals(True)
            self.textNoFillCheckbox.setChecked(self.canvas.config.get("text_no_fill", False))
            self.textNoFillCheckbox.blockSignals(False)

        # Show/Hide Gradient widgets
        is_gradient = mode == "gradient"
        self.gradientLabel.setVisible(is_gradient)
        self.gradientCombo.setVisible(is_gradient)
        if is_gradient:
            self.canvas.set_config("gradient_type", self.gradientCombo.currentText().lower())

        # Show/Hide Regular Poly Toolbar
        if mode == "regularpoly" and self.actionShowToolProperties.isChecked():
            self.polyToolbar.show()
        else:
            self.polyToolbar.hide()

        # Show/Hide Smudge Toolbar
        if mode == "smudge" and self.actionShowToolProperties.isChecked():
            self.smudgeToolbar.show()
        else:
            self.smudgeToolbar.hide()

        # Show/Hide Selection Selection Toolbar
        is_selection_mode = "select" in mode
        self.selectionSelectionToolbar.setVisible(is_selection_mode and self.actionShowToolProperties.isChecked())

        # Show/Hide Filling Modes Frame (Contour/Fill options)
        shape_modes = ["rect", "roundrect", "polygon", "regularpoly", "polyline", "ellipse", "text"]
        if mode in shape_modes:
            self.fillingModes.show()
        else:
            self.fillingModes.hide()

        # Show/Hide Font Toolbar
        if mode == "text" and self.actionShowToolProperties.isChecked():
            self.fontToolbar.show()
        else:
            self.fontToolbar.hide()

        self._update_trans_frame_visibility()

    def revert_tool(self):
        # Switch back to the tool being used before the dropper
        if self.canvas.mode == "dropper":
            self.set_mode(self.previous_tool_mode)
            # Find and check the correct button in the UI
            target_btn_name = f"{self.previous_tool_mode}Button"
            if "select" in self.previous_tool_mode:
                self.selectToolButton.setChecked(True)
            elif self.previous_tool_mode in ["rect", "roundrect", "polygon", "regularpoly", "ellipse", "spline"]:
                self.shapeToolButton.setChecked(True)
            elif self.previous_tool_mode in ["line", "polyline", "simpleline"]:
                self.lineToolButton.setChecked(True)
            elif hasattr(self, target_btn_name):
                getattr(self, target_btn_name).setChecked(True)

    def _update_button_color(self, button, hex_color):
        """Update button background color while preserving existing styles (borders, etc.) from the UI."""
        import re
        style = button.styleSheet()
        bg_pattern = re.compile(r'background-color\s*:[^;]+;?', re.IGNORECASE)
        if bg_pattern.search(style):
            # Replace existing background-color in-place (works for both flat and block styles)
            new_style = bg_pattern.sub(f'background-color: {hex_color};', style)
        elif '{' in style:
            # Block-style stylesheet: insert before first closing brace
            new_style = re.sub(r'\}', f' background-color: {hex_color}; }}', style, count=1)
        else:
            # Flat stylesheet: append
            sep = '; ' if style.strip().rstrip(';') else ''
            new_style = f"{style.strip().rstrip(';')}{sep}background-color: {hex_color};"
        button.setStyleSheet(new_style)

    def update_status_color(self, color):
        if not color.isValid() or self.canvas.mode != "dropper":
            self.status_color.setText("")
            # If still in dropper mode but moving out of the image area, revert the preview
            if self.canvas.mode == "dropper":
                self._update_button_color(self.primaryButton, self.canvas.primary_color.name())
            return
        
        hex_val = color.name().upper()
        rgb_val = f"{color.red()}, {color.green()}, {color.blue()}"
        self.status_color.setText(f"    Hex: {hex_val} - RGB({rgb_val})")
        
        # Preview color in primaryButton
        self._update_button_color(self.primaryButton, color.name())

    def update_status_selection_dimensions(self, w, h):
        if w == 0 and h == 0:
            # Clear if dimensions are 0 (mouse release or mode reset)
            # but only if we are not in dropper mode (which uses the same label)
            if self.canvas.mode != "dropper":
                self.status_color.setText("")
            return
        
        # Display rectangle size in the color status section as requested
        self.status_color.setText(f"    Selection size: {w} x {h} px")

    def _update_trans_frame_visibility(self, *args, **kwargs):
        if "select" in self.canvas.mode or self.canvas.mode == "paste":
            self.transFrame.show()
        else:
            self.transFrame.hide()
            
        if self.toolsDock.isFloating():
            # Force the floating window to resize to fit the new content
            self.toolsDock.adjustSize()


    def set_primary_color(self, hex):
        self.canvas.set_primary_color(hex)
        self._update_button_color(self.primaryButton, hex)

    def set_secondary_color(self, hex):
        self.canvas.set_secondary_color(hex)
        self._update_button_color(self.secondaryButton, hex)

    def invert_colors(self):
        p = self.canvas.primary_color.name()
        s = self.canvas.secondary_color.name() if self.canvas.secondary_color else "#ffffff"
        self.set_primary_color(s)
        self.set_secondary_color(p)

    def next_stamp(self):
        self.current_stamp_n += 1
        if self.current_stamp_n >= len(constants.STAMPS):
            self.current_stamp_n = 0

        pixmap = QPixmap(constants.STAMPS[self.current_stamp_n])
        # self.stampnextButton.setIcon(QIcon(pixmap))

        self.canvas.current_stamp = pixmap

    def select_all(self):
        """Select the entire canvas with the rectangle selection tool."""
        # Switch to selectrect tool button
        for mode in ["selectrect"]:
            btn = getattr(self, "%sButton" % mode, None)
            if btn:
                btn.click()
        self.canvas.select_all()

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()

        if self.canvas.mode in ["selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand"] and self.canvas.locked:
            clipboard.setPixmap(self.canvas.copy_selection())

        else:
            clipboard.setPixmap(self.canvas.pixmap())

    def paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        pix = clipboard.pixmap()
        if not pix.isNull():
            self.canvas.start_paste(pix)
            return
        img = clipboard.image()
        if not img.isNull():
            pix = QPixmap.fromImage(img)
            self.canvas.start_paste(pix)

    def paste_as_new_image(self):
        """Paste clipboard content as a brand-new canvas image."""
        clipboard = QApplication.clipboard()
        pix = clipboard.pixmap()
        if pix.isNull():
            img = clipboard.image()
            if img.isNull():
                return
            pix = QPixmap.fromImage(img)
        self.canvas.setPixmap(pix)
        self.canvas.set_scale(1.0)

    def open_file(self):
        """
        Open image file for editing.
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open file",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.gif);;All Files (*)",
        )

        if path:
            self.load_image(path)

    def load_image(self, path):
        if path:
            if not os.path.exists(path):
                self.statusBar.showMessage(f"File not found: {path}", 3000)
                return

            pixmap = QPixmap()
            if not pixmap.load(path):
                self.statusBar.showMessage(f"Unable to load image: {path}", 3000)
                return
            
            self.canvas.finalize_operation()
            self.canvas.setPixmap(pixmap)
            self.canvas.set_scale(1.0)
            self.current_file_path = path
            self.update_window_title()
            self.add_to_recent_files(path)

    def new_image(self):
        self.canvas.initialize()
        self.current_file_path = None
        self.update_window_title()

    def update_window_title(self):
        if self.current_file_path:
            self.setWindowTitle(f"ecPaint - {os.path.basename(self.current_file_path)}")
        else:
            self.setWindowTitle("ecPaint")

    def update_recent_files_menu(self):
        settings = QSettings("PyQtDraw", "RecentFiles")
        files = settings.value("recentFileList", [])
        if files is None:
            files = []
        elif isinstance(files, str):
            files = [files]
        elif not isinstance(files, (list, tuple)):
            try:
                files = list(files)
            except Exception:
                files = []

        files = [os.path.abspath(f) for f in files if isinstance(f, str) and f]
        
        # Remove old direct actions from the menu
        for action in self.recent_actions:
            self.menuFIle.removeAction(action)
        self.recent_actions = []
        
        if not files:
            # Hide separators if there are no recent files
            if self.recent_header_sep: self.recent_header_sep.setVisible(False)
            if self.recent_footer_sep: self.recent_footer_sep.setVisible(False)
            return

        # Show separators
        if self.recent_header_sep: self.recent_header_sep.setVisible(True)
        if self.recent_footer_sep: self.recent_footer_sep.setVisible(True)

        # Show the last 5 files directly in the menu before the footer separator
        display_files = files[:5]
        
        for f in display_files:
            action = QAction(os.path.basename(f), self)
            action.setToolTip(f)
            action.setData(f)
            
            # Use lambda to capture path directly (works reliably on all platforms)
            action.triggered.connect(lambda checked=False, p=f: self.open_recent_file(p))
            
            # Insert before the footer separator (the one just above Exit)
            self.menuFIle.insertAction(self.recent_footer_sep, action)
            self.recent_actions.append(action)

    def add_to_recent_files(self, path):
        settings = QSettings("PyQtDraw", "RecentFiles")
        files = settings.value("recentFileList", [])
        if files is None:
            files = []
        elif isinstance(files, str):
            files = [files]
        elif not isinstance(files, (list, tuple)):
            try:
                files = list(files)
            except Exception:
                files = []

        # Normalise path
        path = os.path.abspath(path)
        
        if path in files:
            files.remove(path)
        files.insert(0, path)
        files = [f for f in files if isinstance(f, str) and f][:10]  # Keep last 10 entries
        
        settings.setValue("recentFileList", files)
        self.update_recent_files_menu()

    def open_recent_file(self, path=None):
        """
        Open a recent file. If path is not provided, it attempts to 
        retrieve it from the sender action (typical Linux behavior).
        """
        if path is None:
            action = self.sender()
            if isinstance(action, QAction):
                path = action.data()
        
        if path and isinstance(path, str):
            self.load_image(path)



    def save_file(self):
        if self.current_file_path:
            pixmap = self.canvas.pixmap()
            pixmap.save(self.current_file_path, "PNG")
            self.add_to_recent_files(self.current_file_path)
            # Status message feedback
            self.statusBar.showMessage(f"Saved to {self.current_file_path}", 2000)
        else:
            self.save_file_as()

    def save_file_as(self):
        """
        Save active canvas to a new image file.
        """
        path, _ = QFileDialog.getSaveFileName(
            self, "Save file as", "", "PNG Image file (*.png)"
        )

        if path:
            pixmap = self.canvas.pixmap()
            pixmap.save(path, "PNG")
            self.current_file_path = path
            self.update_window_title()
            self.add_to_recent_files(path)

    def invert(self):
        if getattr(self.canvas, "selectionActive", False) or self.canvas.mode == "paste":
            self.canvas.invert_selection_colors()
        else:
            pixmap = self.canvas.pixmap()
            img = pixmap.toImage()
            img.invertPixels()
            pix = QPixmap()
            pix.convertFromImage(img)
            self.canvas.setPixmap(pix)

    def flip_horizontal(self):
        if getattr(self.canvas, "selectionActive", False) or self.canvas.mode == "paste":
            self.canvas.flip_selection_horizontal()
        else:
            pixmap = self.canvas.pixmap()
            self.canvas.setPixmap(pixmap.transformed(QTransform().scale(-1, 1)))

    def flip_vertical(self):
        if getattr(self.canvas, "selectionActive", False) or self.canvas.mode == "paste":
            self.canvas.flip_selection_vertical()
        else:
            pixmap = self.canvas.pixmap()
            self.canvas.setPixmap(pixmap.transformed(QTransform().scale(1, -1)))

    def rotate_right(self):
        if getattr(self.canvas, "selectionActive", False) or self.canvas.mode == "paste":
            self.canvas.rotate_selection_right()
        else:
            pixmap = self.canvas.pixmap()
            self.canvas.setPixmap(pixmap.transformed(QTransform().rotate(90)))

    def show_resize_dialog(self):
        if not self.canvas._image_pixmap:
            return
        w = self.canvas._image_pixmap.width()
        h = self.canvas._image_pixmap.height()
        dlg = ResizeDialogWindow(w, h, self)
        if dlg.exec():
            new_w, new_h = dlg.get_new_size()
            if new_w > 0 and new_h > 0:
                method = dlg.get_method()
                if method == 0:
                    self.canvas._perform_resample(new_w, new_h, Qt.FastTransformation)
                elif method == 1:
                    self.canvas._perform_resample(new_w, new_h, Qt.SmoothTransformation)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # app.setStyle("Breeze")
    app.setWindowIcon(QIcon(":/icons/program.ico"))
    
    # Apply custom stylesheet
    stylesheet = """
    QMenuBar {
        border-bottom: none;
    }
    
    QMenuBar::item {
        padding-top: 6px;
        padding-bottom: 6px;
        padding-left: 8px;
        padding-right: 8px;
    }
    
    QMenuBar::item:selected {
        background-color: rgba(0, 0, 0, 10%);
    }
    
    QMenuBar::item:pressed {
        background-color: rgba(0, 0, 0, 20%);
    }

    """
    app.setStyleSheet(stylesheet)
    
    window = MainWindow()
    app.exec()
