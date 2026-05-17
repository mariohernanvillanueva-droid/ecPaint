import random

import constants
import time
import math
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal, QSize, QTimer
from PySide6.QtGui import (
    QBitmap,   
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygon,
    QPolygonF,
    QRegion,
    QTransform,
)
from PySide6.QtWidgets import QLabel, QApplication
from utils import build_font


class Canvas(QLabel):
    mode = "selectrect"

    primary_color = QColor(Qt.GlobalColor.black)
    secondary_color = None

    primary_color_updated = Signal(str)
    secondary_color_updated = Signal(str)
    # Undo/Redo signals
    undo_available = Signal(bool)
    redo_available = Signal(bool)
    # Cursor and canvas information signals
    mouse_pos_changed = Signal(int, int)  # Emits (x, y) in image coordinates
    canvas_dimensions_changed = Signal(int, int)  # Emits (width, height)
    zoom_level_changed = Signal(int)  # Emits zoom percentage (10-800)
    status_message_changed = Signal(str) # Emits guidance messages
    color_hovered = Signal(QColor)
    color_picked = Signal()
    selection_dimensions_changed = Signal(int, int)
    selection_state_changed = Signal(bool)

    # Store configuration settings, including pen width, fonts etc.
    config = {
        # Drawing options.
        "size": 12,
        "fill": True,
        # Font options.
        "font": QFont("Times"),
        "fontsize": 18,
        "bold": False,
        "italic": False,
        "underline": False,
        "paste_fill": True,
        "contour": True,
        "tolerance": 32,
        "line_type": 0,
        "antialias": False,
        "poly_vertices": 5,
        "poly_inner_radius": 0.5,
        "smudge_radius": 20,
        "smudge_pressure": 50,
        "smooth": False,
    }

    active_color = None
    active_color = None
    preview_pen = None
    shape_pen = None
    shape_brush = None

    timer_event = None

    current_stamp = None

    def initialize(self):
        self.background_color = (
            QColor(self.secondary_color)
            if self.secondary_color
            else QColor(Qt.GlobalColor.white)
        )
        self.eraser_color = (
            QColor(self.secondary_color)
            if self.secondary_color
            else QColor(Qt.GlobalColor.white)
        )
        self.eraser_color.setAlpha(100)
        self.scale = 1.0
        self._image_pixmap = None

        cursor_zoom = QPixmap(":/icons/magnifier.png")
        self.zoom_cursor = QCursor(cursor_zoom)
        # Undo/redo stacks
        self._undo_stack = []
        self._redo_stack = []
        self._undo_limit = 30
        # Control whether changes should be recorded (disable during initialization)
        self._record_undo = False
        # Temporary preview pixmap (not committed to the base image)
        self._preview_pixmap = None
        # Ensure the QLabel places the pixmap at the top-left (no centering offset)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.reset()
        self.locked = False
        self.active_shape_fn = None
        self.hover_pos = None
        self._selectionActive = False
        # Move-to-commit states for shapes
        self.is_moving_shape = False
        self.is_dragging_shape = False
        self.is_rotating_shape = False
        self.shape_rotation = 0
        self.moving_rect = None
        self.selection_resizing = ""
        self.selection_resize_origin = None
        self.selection_resize_start_pos = None
        
        self.rotation_icon = QPixmap(":/icons/circlearrow.png")
        
        # Initialize pens and brushes with sensible defaults to prevent crashes in paintEvent
        self.shape_pen = QPen(Qt.GlobalColor.black)
        self.shape_brush = QBrush(Qt.BrushStyle.NoBrush)
        self.preview_pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
        
        # Buffer for pixel-perfect previews
        self._preview_overlay_image = QImage(constants.CANVAS_DIMENSIONS[0], constants.CANVAS_DIMENSIONS[1], QImage.Format.Format_ARGB32)
        
        # After initial reset, enable undo recording and record the initial state
        self._record_undo = True
        self._record_snapshot()

    # Override QLabel.setPixmap so we keep a full-resolution image and show a scaled copy
    def setPixmap(self, pixmap, record=True):
        # Only abort (cancel/commit) active operations when this is a real committed state change.
        # When record=False (e.g. intermediate brush/pen strokes), preserve the active selection.
        # Also skip abort when a selection/paste is active — pixel-painting tools (fill, eraser, etc.)
        # should paint onto the canvas without cancelling the selection overlay.
        has_active_selection = (
            getattr(self, "_selectionActive", False)
            or getattr(self, "locked", False)
            or getattr(self, "mode", None) == "paste"
        )
        if record and not has_active_selection and not getattr(self, "_in_set_pixmap", False):
            self._in_set_pixmap = True
            try:
                self.abort_operation()
            finally:
                self._in_set_pixmap = False

        # Record previous state for undo if enabled and requested.
        try:
            do_record = record and getattr(self, "_record_undo", True)
        except Exception:
            do_record = False

        if do_record and getattr(self, "_image_pixmap", None) is not None:
            self._undo_stack.append(self._image_pixmap.copy())
            # cap stack to limit
            if len(self._undo_stack) > self._undo_limit:
                self._undo_stack.pop(0)
            # clear redo stack on new change
            self._redo_stack.clear()
            try:
                self.undo_available.emit(True)
                self.redo_available.emit(False)
            except Exception:
                pass

        # store base image, ensuring it has an alpha channel (ARGB)
        from PySide6.QtGui import QImage
        if isinstance(pixmap, QPixmap):
            qimg = pixmap.toImage()
        else:
            qimg = pixmap
        try:
            qimg = qimg.convertToFormat(QImage.Format.Format_ARGB32)
            self._image_pixmap = QPixmap.fromImage(qimg)
        except Exception:
            # Fallback: coerce via QPixmap constructor
            self._image_pixmap = QPixmap(pixmap)

        # any committed pixmap replaces preview
        self._preview_pixmap = None
        self._dropper_image = None
        self._update_display()
        
        # Optimize: Only recreate the overlay buffer if the size actually changed
        if self._image_pixmap is not None:
             w, h = self._image_pixmap.width(), self._image_pixmap.height()
             if (not getattr(self, "_preview_overlay_image", None) or 
                 self._preview_overlay_image.width() != w or 
                 self._preview_overlay_image.height() != h):
                  self._preview_overlay_image = QImage(w, h, QImage.Format.Format_ARGB32)

        # Emit canvas dimensions changed signal
        try:
            w, h = self._image_pixmap.width(), self._image_pixmap.height()
            self.canvas_dimensions_changed.emit(w, h)
        except Exception:
            pass

    @property
    def selectionActive(self):
        return getattr(self, "_selectionActive", False)

    @selectionActive.setter
    def selectionActive(self, value):
        if getattr(self, "_selectionActive", None) != value:
            self._selectionActive = value
            self.selection_state_changed.emit(value)

    def pixmap(self):
        # return the full-resolution image copy
        img = getattr(self, "_image_pixmap", None)
        if img is None:
            return super().pixmap()
        return img.copy()

    def _record_snapshot(self):
        """Push a copy of the current image onto the undo stack without clearing redo."""
        if not getattr(self, "_record_undo", True):
            return
        if getattr(self, "_image_pixmap", None) is None:
            return
        self._undo_stack.append(self._image_pixmap.copy())
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        try:
            self.undo_available.emit(len(self._undo_stack) > 0)
        except Exception:
            pass

    def undo(self):
        # Cancel any active uncommitted selection or operation first.
        # This includes partially drawn selections or "locked" (floating) selections.
        if self.selectionActive or self.locked or (hasattr(self, "points") and self.points) or \
           self.mode == "paste" or (self.mode == "text" and getattr(self, "current_pos", None) is not None):
            self.abort_operation()
            # Ensure points are cleared for polygon/lasso tools
            if hasattr(self, "points"):
                self.points = []
            self.update()
            return

        if not self._undo_stack:
            return
        # push current state to redo
        if getattr(self, "_image_pixmap", None) is not None:
            self._redo_stack.append(self._image_pixmap.copy())
        img = self._undo_stack.pop()
        # ensure ARGB
        from PySide6.QtGui import QImage
        try:
            qimg = img.toImage() if isinstance(img, QPixmap) else img
            qimg = qimg.convertToFormat(QImage.Format.Format_ARGB32)
            self._image_pixmap = QPixmap.fromImage(qimg)
        except Exception:
            self._image_pixmap = QPixmap(img)
        self._update_display()
        try:
            self.undo_available.emit(len(self._undo_stack) > 0)
            self.redo_available.emit(len(self._redo_stack) > 0)
        except Exception:
            pass

    def redo(self):
        if not self._redo_stack:
            return
        # push current state to undo
        if getattr(self, "_image_pixmap", None) is not None:
            self._undo_stack.append(self._image_pixmap.copy())
            if len(self._undo_stack) > self._undo_limit:
                self._undo_stack.pop(0)
        img = self._redo_stack.pop()
        # ensure ARGB
        from PySide6.QtGui import QImage
        try:
            qimg = img.toImage() if isinstance(img, QPixmap) else img
            qimg = qimg.convertToFormat(QImage.Format.Format_ARGB32)
            self._image_pixmap = QPixmap.fromImage(qimg)
        except Exception:
            self._image_pixmap = QPixmap(img)
        self._update_display()
        try:
            self.undo_available.emit(len(self._undo_stack) > 0)
            self.redo_available.emit(len(self._redo_stack) > 0)
        except Exception:
            pass

    def can_undo(self):
        return len(getattr(self, "_undo_stack", [])) > 0

    def can_redo(self):
        return len(getattr(self, "_redo_stack", [])) > 0

    def _update_display(self):
        # Optimization: We handle ALL scaling and drawing in paintEvent. 
        # We only need to resize the label widget here so the scroll area knows the content size.
        if not getattr(self, "_image_pixmap", None):
            return
        w, h = self._image_pixmap.width(), self._image_pixmap.height()
        s = getattr(self, "scale", 1.0)
        sw, sh = max(1, int(w * s)), max(1, int(h * s))
        
        # Don't call super().setPixmap(scaled) which is extremely expensive at high zoom.
        # We must keep a "dummy" small pixmap or no pixmap at all to prevent QLabel from drawing over us.
        if self.pixmap() is not None:
             super().setPixmap(QPixmap()) # Clear the background pixmap

        margin = getattr(self, "_display_margin", 24)
        self.resize(QSize(sw + margin, sh + margin))
        self.update()

    def _show_preview(self, pixmap):
        """Display a temporary preview pixmap scaled to the current zoom without modifying the base image."""
        if not pixmap:
            # clear preview and restore display to base image size
            self._preview_pixmap = None
            scroll_area = self._find_scroll_area()
            old_h = old_v = None
            if scroll_area is not None:
                old_h = scroll_area.horizontalScrollBar().value()
                old_v = scroll_area.verticalScrollBar().value()
            self._update_display()
            self.update()
            if scroll_area is not None:
                scroll_area.horizontalScrollBar().setValue(old_h)
                scroll_area.verticalScrollBar().setValue(old_v)
            return

        scroll_area = self._find_scroll_area()
        old_min_h = old_max_h = old_val_h = old_min_v = old_max_v = old_val_v = None
        if scroll_area is not None:
            old_min_h = scroll_area.horizontalScrollBar().minimum()
            old_max_h = scroll_area.horizontalScrollBar().maximum()
            old_val_h = scroll_area.horizontalScrollBar().value()
            old_min_v = scroll_area.verticalScrollBar().minimum()
            old_max_v = scroll_area.verticalScrollBar().maximum()
            old_val_v = scroll_area.verticalScrollBar().value()

        margin = 0
        if self.locked != False or self.active_shape_fn != None:
            margin = getattr(self, "_display_margin", 24)
        # store preview and resize the label to the preview's scaled size
        # so the preview and anchors remain visible while dragging.
        self._preview_pixmap = pixmap
        s = getattr(self, "scale", 1.0)
        w, h = pixmap.width(), pixmap.height()
        # if fix_scroll:
            # sw, sh = max(1, int(w * s)), max(1, int(h * s))
            # if scroll_area is not None:
                # viewport = scroll_area.viewport().size()
                # sw = max(sw, viewport.width() + margin)
                # sh = max(sh, viewport.height() + margin)
        # else:
        sw, sh = max(1, int(w * s)), max(1, int(h * s))
        # Prevent shrinking the label below the original display size
        # while previewing so anchors and scrollbars remain stable.
        # if getattr(self, "_image_pixmap", None):
            # ow, oh = self._image_pixmap.width(), self._image_pixmap.height()
            # osw, osh = max(1, int(ow * s)), max(1, int(oh * s))
            # sw = max(sw, osw)
            # sh = max(sh, osh)
            
        self.resize(QSize(sw + margin, sh + margin))
        self.update()

        if scroll_area is not None:
            scroll_area.horizontalScrollBar().setMinimum(old_min_h)
            scroll_area.horizontalScrollBar().setMaximum(old_max_h)
            scroll_area.horizontalScrollBar().setValue(old_val_h)
            scroll_area.verticalScrollBar().setMinimum(old_min_v)
            scroll_area.verticalScrollBar().setMaximum(old_max_v)
            scroll_area.verticalScrollBar().setValue(old_val_v)

    def set_scale(self, factor):
        self.scale = max(0.0625, min(32.0, factor))
        self._update_display()
        # Emit zoom level in percentage
        try:
            zoom_percent = int(self.scale * 100)
            self.zoom_level_changed.emit(zoom_percent)
        except Exception:
            pass

    def zoom(self, factor):
        self.set_scale(self.scale * factor)

    def zoom_in(self):
        self.zoom(2.0)

    def zoom_out(self):
        self.zoom(0.5)

    def zoom_reset(self):
        self.set_scale(1.0)

    def _event_widget_pos(self, e=None):
        """Return mouse position in widget coordinates, accounting for scroll offset."""
        # Prefer the event's globalPosition for timestamp-accurate coordinates.
        # Fallback to QCursor.pos() for absolute reliability during drags.
        gp = None
        if e is not None:
            if hasattr(e, "globalPosition"): gp = e.globalPosition()
            elif hasattr(e, "globalPos"): gp = e.globalPos()
            
        if gp is not None:
            res = self.mapFromGlobal(gp)
        else:
            res = self.mapFromGlobal(QCursor.pos())
            
        # Ensure we return a QPoint (integer) to remain compatible with QRect.contains()
        # and other integer-based geometry methods.
        if hasattr(res, "toPoint"):
            return res.toPoint()
        return res

    def _find_scroll_area(self):
        from PySide6.QtWidgets import QAbstractScrollArea
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _handle_auto_scroll(self):
        """Scroll the parent QScrollArea if the mouse is near or outside the viewport during a drag."""
        # Polling check: ensure we still have the grab if buttons are down
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            if not self.underMouse() and QLabel.mouseGrabber() != self:
                try:
                    self.grabMouse()
                except RuntimeError:
                    pass

        # Only auto-scroll during drag operations
        is_dragging = (getattr(self, "is_dragging", False) or 
                       getattr(self, "is_dragging_shape", False) or 
                       getattr(self, "is_dragging_cp", False) or 
                       getattr(self, "is_dragging_end", False) or
                       getattr(self, "resizing", False) or
                       getattr(self, "is_rotating_shape", False))
        
        if not is_dragging:
            return

        from PySide6.QtWidgets import QAbstractScrollArea
        parent = self.parentWidget()
        scroll_area = None
        while parent:
            if isinstance(parent, QAbstractScrollArea):
                scroll_area = parent
                break
            parent = parent.parentWidget()
            
        if not scroll_area:
            return
            
        viewport = scroll_area.viewport()
        # Viewport-relative cursor position
        v_pos = viewport.mapFromGlobal(QCursor.pos())
        v_rect = viewport.rect()
        
        # Margin within which auto-scroll triggers
        margin = 30
        step = 15
        
        h_bar = scroll_area.horizontalScrollBar()
        v_bar = scroll_area.verticalScrollBar()
        
        if v_pos.x() < margin:
            h_bar.setValue(h_bar.value() - step)
        elif v_pos.x() > v_rect.width() - margin:
            h_bar.setValue(h_bar.value() + step)
            
        if v_pos.y() < margin:
            v_bar.setValue(v_bar.value() - step)
        elif v_pos.y() > v_rect.height() - margin:
            v_bar.setValue(v_bar.value() + step)

    def _to_image_pos(self, e=None):
        """Return raw floating-point image coordinates (for precision tools like Spray)."""
        pos = self._event_widget_pos(e)
        s = getattr(self, "scale", 1.0)
        return QPointF(pos.x() / s, pos.y() / s)

    def _to_image_pixel(self, e=None):
        """Return snapped integer pixel coordinates (floor truncation) for grid-based tools."""
        pos = self._to_image_pos(e)
        return QPoint(int(pos.x()), int(pos.y()))

    def reset(self):
        # Create an ARGB image/pixmap for display so we support transparency when cutting.
        from PySide6.QtGui import QImage
        w, h = constants.CANVAS_DIMENSIONS
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(self.background_color)
        pixmap = QPixmap.fromImage(img)
        # Use record=False during reset to avoid creating an undo snapshot
        self.setPixmap(pixmap, record=False)

    def cut_selection(self):
        """Cut the current selection to the clipboard and make the selected area transparent.

        Returns True if a cut was performed, False otherwise.
        """
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()

        # Rectangle selection
        if self.mode == "selectrect" and getattr(self, "locked", False):
            pix = self.selectrect_copy()
            clipboard.setPixmap(pix)
            self._clear_selection_area(transparent=True)
            self.deselect()
            return True

        # Ellipse selection
        if self.mode == "selectellipse" and getattr(self, "locked", False):
            pix = self.selectellipse_copy()
            clipboard.setPixmap(pix)
            self._clear_selection_area(transparent=True)
            self.deselect()
            return True

        # Polygon selection
        if self.mode == "selectpoly" and getattr(self, "locked", False) and getattr(self, "history_pos", None):
            pix = self.selectpoly_copy()
            clipboard.setPixmap(pix)
            self._clear_selection_area(transparent=True)
            self.deselect()
            return True

        # Free (lasso) selection
        if self.mode == "selectfree" and getattr(self, "locked", False) and getattr(self, "history_pos", None):
            pix = self.selectpoly_copy()
            clipboard.setPixmap(pix)
            self._clear_selection_area(transparent=True)
            self.deselect()
            return True

        # Wand selection
        if self.mode == "selectwand" and getattr(self, "locked", False) and getattr(self, "painter_path", None):
            pix = self.selectwand_copy()
            clipboard.setPixmap(pix)
            self._clear_selection_area(transparent=True)
            self.deselect()
            return True

        return False

    def _clear_selection_area(self, transparent=False):
        """Clear the current selection (rect or poly) in the base image.
        
        This respects the 'fill' setting to decide between filling with background color
        or making the area transparent.
        
        If transparent=True, the area is cleared to transparent regardless of the settings.
        """
        if not getattr(self, "locked", False):
            return

        from PySide6.QtGui import QImage
        img = self._image_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        
        # Special behavior for actionContour (contour=True, fill=False)
        is_contour_mode = self.config.get("contour") and not self.config.get("fill")
        if is_contour_mode and not transparent:
            if self.config.get("paste_fill", True): # transSolid selected
                transparent = True
        
        do_trans_except_bg = is_contour_mode and not transparent and not self.config.get("paste_fill", True)

        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        
        # Apply rotation transform to the painter if the selection is rotated
        rotation = getattr(self, "shape_rotation", 0)
        if rotation != 0 and getattr(self, "moving_rect", None):
            pivot = self._rotation_pivot()
            p.translate(pivot.x(), pivot.y())
            p.rotate(rotation)
            p.translate(-pivot.x(), -pivot.y())

        # Decide whether to clear based on the active shape type, not the current tool mode.
        # This allows a polygon selection to be cleared correctly even if the Rectangle tool is active.
        if self.active_shape_fn in ["drawPolygon", "drawPolyline"] and getattr(self, "history_pos", None):
            # Use history_pos which contains the finalized/moved points
            pts = self.history_pos if getattr(self, "locked", False) else (self.history_pos + [self.current_pos])
            userpoly = QPolygon([
                pt.toPoint() if hasattr(pt, "toPoint") else pt
                for pt in pts
            ])
            if do_trans_except_bg:
                p.end()
                bg_target = (self.secondary_color.rgb() if self.secondary_color else 0xFFFFFFFF) & 0x00FFFFFF
                brect = userpoly.boundingRect()
                for y in range(brect.top(), brect.bottom() + 1):
                    if 0 <= y < img.height():
                        for x in range(brect.left(), brect.right() + 1):
                            if 0 <= x < img.width():
                                if userpoly.containsPoint(QPoint(x, y), Qt.FillRule.OddEvenFill):
                                    if (img.pixel(x, y) & 0x00FFFFFF) != bg_target:
                                        img.setPixel(x, y, 0)
                self.setPixmap(QPixmap.fromImage(img), record=True)
                return

            if not transparent and (self.config.get("fill") or not self.config.get("paste_fill", True)):
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.setBrush(QBrush(self.secondary_color))
                p.setPen(QPen(self.secondary_color))
            else:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                p.setBrush(QBrush(QColor(0, 0, 0, 0)))
                p.setPen(QPen(Qt.GlobalColor.transparent))
            p.drawPolygon(userpoly)

        elif self.active_shape_fn in ["drawPath", "drawEllipse"]:
            # Generate painter_path for Ellipse if not already generated (e.g. user hits Delete before moving it)
            if self.active_shape_fn == "drawEllipse" and self.moving_rect and not getattr(self, "painter_path", None):
                w, h = self._image_pixmap.width(), self._image_pixmap.height()
                mask = QImage(w, h, QImage.Format.Format_ARGB32)
                mask.fill(QColor(Qt.GlobalColor.white))
                m_p = QPainter(mask)
                m_p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                m_p.setBrush(QBrush(Qt.GlobalColor.black))
                m_p.setPen(QPen(Qt.GlobalColor.black))
                m_p.drawEllipse(QRectF(self.moving_rect))
                m_p.end()
                self.painter_path = self._path_from_mask(mask)
                
            if getattr(self, "painter_path", None):
                if do_trans_except_bg:
                    p.end()
                    bg_target = (self.secondary_color.rgb() if self.secondary_color else 0xFFFFFFFF) & 0x00FFFFFF
                    path = self.painter_path
                    brect = path.boundingRect().toRect()
                    for y in range(brect.top(), brect.bottom() + 1):
                        if 0 <= y < img.height():
                            for x in range(brect.left(), brect.right() + 1):
                                if 0 <= x < img.width():
                                    if path.contains(QPointF(x + 0.5, y + 0.5)):
                                        if (img.pixel(x, y) & 0x00FFFFFF) != bg_target:
                                            img.setPixel(x, y, 0)
                    self.setPixmap(QPixmap.fromImage(img), record=True)
                    return
    
                if not transparent and (self.config.get("fill") or not self.config.get("paste_fill", True)):
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    p.fillPath(self.painter_path, self.secondary_color)
                else:
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                    p.fillPath(self.painter_path, QColor(0, 0, 0, 0))
            else:
                # Fallback to rectangle clearing within elif if path somehow failed
                rect = self.moving_rect if self.moving_rect else QRect(self.origin_pos, self.current_pos).normalized()
                if hasattr(rect, "toRect"):
                    rect = rect.toRect()
                
                if do_trans_except_bg:
                    p.end()
                    bg_target = (self.secondary_color.rgb() if self.secondary_color else 0xFFFFFFFF) & 0x00FFFFFF
                    for y in range(rect.top(), rect.bottom() + 1):
                        if 0 <= y < img.height():
                            for x in range(rect.left(), rect.right() + 1):
                                if 0 <= x < img.width():
                                    if (img.pixel(x, y) & 0x00FFFFFF) != bg_target:
                                        img.setPixel(x, y, 0)
                    self.setPixmap(QPixmap.fromImage(img), record=True)
                    return
    
                if not transparent and (self.config.get("fill") or not self.config.get("paste_fill", True)):
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    p.fillRect(rect, self.secondary_color)
                else:
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                    p.fillRect(rect, QColor(0, 0, 0, 0))

        else:
            # Fallback to rectangle clearing
            rect = self.moving_rect if self.moving_rect else QRect(self.origin_pos, self.current_pos).normalized()
            if hasattr(rect, "toRect"):
                rect = rect.toRect()
            
            if do_trans_except_bg:
                p.end()
                bg_target = (self.secondary_color.rgb() if self.secondary_color else 0xFFFFFFFF) & 0x00FFFFFF
                for y in range(rect.top(), rect.bottom() + 1):
                    if 0 <= y < img.height():
                        for x in range(rect.left(), rect.right() + 1):
                            if 0 <= x < img.width():
                                if (img.pixel(x, y) & 0x00FFFFFF) != bg_target:
                                    img.setPixel(x, y, 0)
                self.setPixmap(QPixmap.fromImage(img), record=True)
                return

            if not transparent and (self.config.get("fill") or not self.config.get("paste_fill", True)):
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.fillRect(rect, self.secondary_color)
            else:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                p.fillRect(rect, QColor(0, 0, 0, 0))
        
        p.end()
        # Use record=True here as this is a modification of the canvas content
        self.setPixmap(QPixmap.fromImage(img), record=True)

    def copy_selection(self):
        """Unified helper to copy pixels based on the active selection type (Rect, Poly, or Path).
        
        If shape_rotation != 0, the extracted content is rotated so the returned pixmap
        contains the selection pixels correctly oriented.
        """
        rotation = getattr(self, "shape_rotation", 0)
        
        if rotation != 0:
            return self._copy_selection_rotated(rotation)
        
        if self.active_shape_fn == "drawPath":
            return self.selectwand_copy()
        elif self.active_shape_fn in ["drawPolygon", "drawPolyline"]:
            return self.selectpoly_copy()
        elif self.active_shape_fn == "drawEllipse":
            return self.selectellipse_copy()
        else:
            return self.selectrect_copy()

    def _copy_selection_rotated(self, rotation):
        """Extract the rotated selection region and return its pixels un-rotated (axis-aligned)."""
        if not getattr(self, "moving_rect", None):
            return QPixmap()
        
        pivot = self._rotation_pivot()
        src_img = self._image_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        
        # Build the selection path in unrotated space
        base_path = QPainterPath()
        if getattr(self, "painter_path", None) and not self.painter_path.isEmpty():
            base_path = QPainterPath(self.painter_path)
        elif self.active_shape_fn == "drawEllipse" and self.moving_rect:
            base_path.addEllipse(QRectF(self.moving_rect))
        else:
            base_path.addRect(QRectF(self.moving_rect))
        
        # Rotate the path by shape_rotation to get the actual selected region
        rot_transform = QTransform()
        rot_transform.translate(pivot.x(), pivot.y())
        rot_transform.rotate(rotation)
        rot_transform.translate(-pivot.x(), -pivot.y())
        rotated_path = rot_transform.map(base_path)
        
        brect = rotated_path.boundingRect().toRect()
        if brect.isEmpty():
            return QPixmap()
        
        # Create transparent target sized to the rotated bounding box
        target = QImage(brect.width(), brect.height(), QImage.Format.Format_ARGB32)
        target.fill(QColor(0, 0, 0, 0))
        
        local_path = rotated_path.translated(-brect.x(), -brect.y())
        
        p = QPainter(target)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setClipPath(local_path)
        p.drawImage(0, 0, src_img, brect.x(), brect.y(), brect.width(), brect.height())
        p.end()
        
        return QPixmap.fromImage(target)

    def _union_selection(self, new_path):
        """Unions a new QPainterPath into the current selection state."""
        if not getattr(self, "painter_path", None) or self.painter_path.isEmpty():
            self.painter_path = QPainterPath()
            if getattr(self, "moving_rect", None) and not self.moving_rect.isEmpty():
                # Import existing selection geometry into the path
                if self.active_shape_fn == "drawEllipse":
                    self.painter_path.addEllipse(QRectF(self.moving_rect))
                elif self.active_shape_fn in ["drawPolygon", "drawPolyline"] and getattr(self, "history_pos", None):
                    poly = QPolygonF([QPointF(pt) for pt in self.history_pos])
                    self.painter_path.addPolygon(poly)
                else:
                    self.painter_path.addRect(QRectF(self.moving_rect))
        
        if not new_path.isEmpty():
            self.painter_path = self.painter_path.united(new_path)
            self.moving_rect = self.painter_path.boundingRect().toRect()
            self.active_shape_fn = "drawPath"
            self.original_painter_path = QPainterPath(self.painter_path)
            self.poly_orig_tl = self.moving_rect.topLeft()

    def _is_selection_hit(self, pos):
        """Unified precise hit detection for both rectangular and polygonal selections."""
        # Ensure we work with QPointF for QPainterPath precision
        pf = QPointF(pos) if not isinstance(pos, QPointF) else pos
        
        # If the selection is rotated, un-rotate the test point around the pivot
        rotation = getattr(self, "shape_rotation", 0)
        if rotation != 0 and getattr(self, "moving_rect", None):
            pivot = self._rotation_pivot()
            rot_rad = math.radians(-rotation)  # inverse rotation
            dx = pf.x() - pivot.x()
            dy = pf.y() - pivot.y()
            rx = dx * math.cos(rot_rad) - dy * math.sin(rot_rad)
            ry = dx * math.sin(rot_rad) + dy * math.cos(rot_rad)
            pf = QPointF(pivot.x() + rx, pivot.y() + ry)
            pos = pf.toPoint()

        if self.active_shape_fn in ["drawPolygon", "drawPolyline"] and getattr(self, "history_pos", None):
            poly = QPolygon([pt.toPoint() if hasattr(pt, "toPoint") else pt for pt in self.history_pos])
            if poly.containsPoint(pos, Qt.FillRule.WindingFill):
                return True
        elif self.active_shape_fn == "drawEllipse" and self.moving_rect:
            path = QPainterPath()
            path.addEllipse(QRectF(self.moving_rect))
            return path.contains(pf)
        elif self.active_shape_fn == "drawRoundedRect" and self.moving_rect:
            path = QPainterPath()
            args = getattr(self, "active_shape_args", (25, 25))
            path.addRoundedRect(QRectF(self.moving_rect), *args)
            return path.contains(pf)
        elif self.active_shape_fn == "drawPath" and getattr(self, "painter_path", None):
            return self.painter_path.contains(pf)
        elif self.mode == "text" and self.current_pos:
            return self._get_text_boundary_box().contains(pf)
        elif self.moving_rect and self.moving_rect.contains(pos):
            return True
        return False

    def _update_shape_tools(self):
        """Update active shape pen and brush to reflect real-time config changes."""
        self.shape_pen = QPen(
                            self.primary_color,
                            self.config["size"],
                            Qt.PenStyle.SolidLine if self.config.get("contour") else Qt.PenStyle.NoPen,
                            Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin,
                        )
        brush_color = self.primary_color if not self.config.get("contour") else self.secondary_color
        if brush_color:
             self.shape_brush = QBrush(brush_color)
        else:
             self.shape_brush = QBrush(Qt.BrushStyle.NoBrush)

    def set_primary_color(self, hex):
        self.primary_color = QColor(hex)
        self._update_shape_tools()
        self.update()

    def set_secondary_color(self, hex):
        self.secondary_color = QColor(hex)
        self.background_color = QColor(hex)
        self.eraser_color = QColor(hex)
        self.eraser_color.setAlpha(100)
        self._update_shape_tools()
        self.update()

    def set_config(self, key, value):
        self.config[key] = value
        if key == "paste_fill" and self.mode == "paste" and getattr(self, "original_stamp", None) is not None:
            self.current_stamp = self._get_transparent_stamp(self.original_stamp)
        self._update_shape_tools()
        self.update()

    def set_config_multiple(self, settings):
        self.config.update(settings)
        if "paste_fill" in settings and self.mode == "paste" and getattr(self, "original_stamp", None) is not None:
            self.current_stamp = self._get_transparent_stamp(self.original_stamp)
        self._update_shape_tools()
        self.update()

    def set_mode(self, mode):
        # Tools that share selection/movement state, or need to preserve it passively (like magnifier or dropper)
        SELECTION_MODES = {"selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand", "move", "magnifier", "dropper"}

        # Allow selections to persist across all tools EXCEPT when explicitly switching
        # to the Text tool or a Shape tool, which should commit the selection first.
        NON_PRESERVING_MODES = {"text", "line", "simpleline", "rect", "ellipse", "polygon", "star", "roundrect", "arc", "chord", "pie", "regularpoly", "spline", "polyline"}

        # is_drawing_shape is True only when the user is genuinely mid-draw on a non-selection shape.
        # It must NOT be True when is_moving_shape is only set because we preserved a selection
        # from a prior tool (selectionActive/locked guard against that false-positive).
        is_drawing_shape = (
            getattr(self, "is_moving_shape", False)
            and self.mode not in SELECTION_MODES
            and not getattr(self, "_selectionActive", False)
            and not getattr(self, "locked", False)
        )
        preserve_selection = (
            (self.selectionActive or getattr(self, "locked", False) or self.mode == "paste")
            and not is_drawing_shape
            and mode not in NON_PRESERVING_MODES
        )

        # If switching tools and NOT preserving selection, finalize the current operation first.
        # This ensures selections are deselected and shapes/pastes are committed.
        if mode != self.mode and not preserve_selection:
            self.finalize_operation()

        # If a paste preview is active, commit it (draw pasted pixmap onto base)
        if getattr(self, "paste_base", None) is not None and getattr(self, "current_stamp", None) is not None:
            try:
                cp = self.current_pos if self.current_pos is not None else QPointF(
                    self._image_pixmap.width() / 2.0, self._image_pixmap.height() / 2.0
                )
                if hasattr(cp, "toPoint"):
                    cp = QPointF(cp)
                top_left = QPointF(cp.x() - self.current_stamp.width() / 2.0, cp.y() - self.current_stamp.height() / 2.0)
                base = self.paste_base
                p = QPainter(base)
                p.drawPixmap(top_left, self.current_stamp)
                p.end()
                self.setPixmap(base)
            except Exception:
                pass
            # clear paste state
            self.paste_base = None
            self.original_base = None
            self.current_stamp = None
            self.timer_event = None
        else:
            # Clean up active timer animations only when NOT preserving a selection.
            # If preserve_selection is True, the timer is restored below after the mode switch.
            if not preserve_selection:
                self.timer_cleanup()

        
        if preserve_selection:
            # If we have a simple rect/ellipse selection, "freeze" it into a painter_path
            # before the mode change (to avoid it being misinterpreted by the new tool's shape_fn)
            if getattr(self, "moving_rect", None) and not getattr(self, "painter_path", None):
                if getattr(self, "active_shape_fn", "") == "drawEllipse":
                    self.painter_path = self._get_pixel_perfect_ellipse_path(QRectF(self.moving_rect))
                else:
                    self.painter_path = QPainterPath()
                    self.painter_path.addRect(QRectF(self.moving_rect))
                self.active_shape_fn = "drawPath"
                self.original_painter_path = QPainterPath(self.painter_path)
                self.poly_orig_tl = self.moving_rect.topLeft()
            # Preserve shape_rotation across selection tool switches
            # (do NOT reset it here — it is reset in the non-preserving branch above)
        else:
            # Not preserving: force-clear all selection/path state immediately
            # This is redundant with reset block below but ensures no leakage during finalize_operation
            self.painter_path = None
            self.selectionActive = False
            self.is_moving_shape = False
            self.moving_rect = None
        
        if mode != "move" and not preserve_selection:
            # Reset mode-specific vars (all)
            self.active_shape_fn = None
            self.active_shape_args = ()

            self.origin_pos = None

            self.current_pos = None
            self.last_pos = None

            self.history_pos = None
            self.last_history = []

            self.current_text = ""
            self.last_text = ""

            self.last_config = {}

            self.dash_offset = 0
            self.locked = False
            self.is_moving_shape = False
            self.is_dragging_shape = False
            self.is_dragging = False
            self.moving_rect = None
            self.poly_original_points = None
            self.poly_orig_tl = None
            self._preview_pixmap = None
            self.painter_path = None
            self.original_painter_path = None
            self.ants_path = None
            self.selectionActive = False
            self.preview_pen = None
            self.shape_rotation = 0
            self.is_rotating_shape = False
        
        # Clear dropper cache when leaving or entering any mode (will be re-cached if mode is dropper)
        self._dropper_image = None
        
        # Apply the mode
        self.mode = mode

        # If a selection is being preserved (e.g. switching to 'move'), restore its animated timer
        if preserve_selection:
            if self.active_shape_fn in ["drawPolygon", "drawPolyline", "drawPath"]:
                self.timer_event = self.generic_poly_timerEvent
            else:
                self.timer_event = self.generic_shape_timerEvent
            # Ensure is_moving_shape and locked are True so the selection boundary
            # is rendered correctly and tool handlers recognise the active state.
            self.is_moving_shape = True
            self.locked = True
            self.selectionActive = True
            if not getattr(self, "preview_pen", None):
                self.preview_pen = constants.SELECTION_PEN

        self.status_message_changed.emit("")
        self.selection_dimensions_changed.emit(0, 0)
        self.update()

    def reset_mode(self):
        self.set_mode(self.mode)

    def on_timer(self):
        if self.timer_event:
            self.timer_event()
        elif self.selectionActive or self.locked or self.mode == "paste":
            # Keep animated overlays (like walking ants) moving even if no tool timer is active
            self.update()

    def _get_animated_dash_offset(self):
        # Use perf_counter for rock-solid timing during high-frequency moves
        return (time.perf_counter() * 30.0) % 1000.0

    def timer_cleanup(self):
        try:
            self.releaseMouse()
        except RuntimeError:
            pass
            
        if self.timer_event:
            # Stop the timer, then trigger cleanup.
            timer_event = self.timer_event
            self.timer_event = None
            timer_event(final=True)
        # clear any transient preview overlay
        self._preview_pixmap = None
        try:
            self.update()
        except Exception:
            pass

    # Mouse events.

    def _lift_selection_if_needed(self):
        """If a selection hasn't been moved yet, extract its contents and transition to paste mode so it can be resized/rotated."""
        SELECTION_MODES = {"selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand"}
        if self.mode in SELECTION_MODES and getattr(self, "selectionActive", False) and getattr(self, "moving_rect", None):
            pix = self.copy_selection()
            center = QRectF(self.moving_rect).center()
            pre_move_pixmap = self.pixmap().copy()
            self._clear_selection_area()
            
            # Transition to paste mode centered at its current location
            self.start_paste(pix, initial_pos=center, immediate_drag=False, original_base=pre_move_pixmap)

    def mousePressEvent(self, e):
        # Always grab mouse on press to ensure move/release are received
        # regardless of whether the cursor stays within the widget bounds.
        self.grabMouse()
        self.setFocus()
        
        # Selection anchor-resize has priority
        sel_hit = self._selection_anchor_hit(self._event_widget_pos(e))
        if sel_hit:
            self._lift_selection_if_needed()
            return self._start_selection_anchor_resize(e, sel_hit)

        # Anchor-resize has priority over tool handlers.
        hit = self._anchor_hit(self._event_widget_pos(e))
        if hit:
            # Commit any active shapes or selections before starting a resize
            if getattr(self, "is_moving_shape", False):
                self._commit_any_active_shape()
            if self.selectionActive or self.locked:
                self.deselect()
            return self._start_anchor_resize(e, hit)

        # Rotation handle check
        handle_rect = self._get_rotation_handle_rect()
        if handle_rect and handle_rect.contains(self._event_widget_pos(e)):
            self._lift_selection_if_needed()
            self.is_rotating_shape = True
            center = self._rotation_pivot()
            mouse_pos = self._to_image_pos(e)
            dy = mouse_pos.y() - center.y()
            dx = mouse_pos.x() - center.x()
            self._start_angle = math.degrees(math.atan2(dy, dx))
            self._start_rotation = getattr(self, "shape_rotation", 0)
            return

        fn = getattr(self, "%s_mousePressEvent" % self.mode, None)
        if fn:
            res = fn(e)
            # Standard interaction: grab mouse on press to ensure move/release are received
            # regardless of whether the cursor stays within the widget bounds.
            self.grabMouse()
            return res

    def mouseMoveEvent(self, e):
        # Rotation in progress
        if getattr(self, "is_rotating_shape", False):
             center = self._rotation_pivot()
             mouse_pos = self._to_image_pos(e)
             dy = mouse_pos.y() - center.y()
             dx = mouse_pos.x() - center.x()
             current_angle = math.degrees(math.atan2(dy, dx))
             self.shape_rotation = self._start_rotation + (current_angle - self._start_angle)
             self.update()
             return

        # If resizing, handle updates here
        if getattr(self, "selection_resizing", False):
            return self._update_selection_anchor_resize(e)

        if getattr(self, "resizing", False):
            return self._update_anchor_resize(e)

        # Update cursor if hovering anchor
        sel_hit = self._selection_anchor_hit(self._event_widget_pos(e))
        hit = self._anchor_hit(self._event_widget_pos(e))
        handle_rect = self._get_rotation_handle_rect()
        
        if sel_hit:
            if sel_hit in ["top_center", "bottom_center"]:
                self.setCursor(Qt.SizeVerCursor)
            elif sel_hit in ["middle_left", "middle_right"]:
                self.setCursor(Qt.SizeHorCursor)
            elif sel_hit in ["top_left", "bottom_right"]:
                self.setCursor(Qt.SizeFDiagCursor)
            elif sel_hit in ["top_right", "bottom_left"]:
                self.setCursor(Qt.SizeBDiagCursor)
        elif hit:
            if hit == "bottom_center":
                self.setCursor(Qt.SizeVerCursor)
            elif hit == "right_center":
                self.setCursor(Qt.SizeHorCursor)
            elif hit == "bottom_right":
                self.setCursor(Qt.SizeFDiagCursor)
        elif handle_rect and handle_rect.contains(self._event_widget_pos(e)):
            self.setCursor(Qt.PointingHandCursor)
        else:
            # Check for movable shape/selection hit to show move cursor
            is_movable = getattr(self, "is_moving_shape", False) or self.mode == "paste"
            
            hit_movable = False
            if is_movable:
                hit_movable = self._is_selection_hit(self._to_image_pixel(e))
            
            if hit_movable or getattr(self, "is_dragging_shape", False) or getattr(self, "is_dragging", False):
                self.setCursor(Qt.SizeAllCursor)
            # elif self.mode in ["brush", "pen", "marker"]:
            #    self.setCursor(Qt.CursorShape.CrossCursor)
            elif self.mode == "magnifier":
                self.setCursor(self.zoom_cursor)
            elif self.mode == "move":
                self.unsetCursor()
            else:
                self.setCursor(Qt.CursorShape.CrossCursor) # self.unsetCursor()

        fn = getattr(self, "%s_mouseMoveEvent" % self.mode, None)
        if fn:
            fn(e)
            
        self._handle_auto_scroll()
        self.update()
        
        # Emit mouse position in image coordinates
        try:
            pos = self._to_image_pos(e)
            self.hover_pos = self._to_image_pixel(e)
            self.mouse_pos_changed.emit(int(pos.x()), int(pos.y()))
        except Exception:
            pass

    def mouseReleaseEvent(self, e):
        # Determine if we should keep the mouse capture (e.g. multi-click drawing phase)
        is_drawing_phase = (self.mode == "spline" and getattr(self, "spline_state", 0) in [2, 3]) or \
                           (self.mode in ["polygon", "polyline"] and self.timer_event is not None)
        
        if not is_drawing_phase:
            # Always attempt to release mouse capture when not in a special multi-step phase
            try:
                self.releaseMouse()
            except RuntimeError:
                pass

        if getattr(self, "is_rotating_shape", False):
             self.is_rotating_shape = False
             return

        # If resizing, commit
        if getattr(self, "selection_resizing", False):
            return self._end_selection_anchor_resize(e)

        if getattr(self, "resizing", False):
            return self._end_anchor_resize(e)

        fn = getattr(self, "%s_mouseReleaseEvent" % self.mode, None)
        if fn:
            return fn(e)

    def _commit_any_active_shape(self):
        """Force fully commit any shape that is in the 'preview' (is_moving_shape) state."""
        if not getattr(self, "is_moving_shape", False):
            return

        # Selection and move tools are never committed as filled shapes — always deselect
        SELECTION_MODES = {"selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand", "move"}
        if self.mode in SELECTION_MODES:
            self.deselect()
            return

        # Use the active shape function to decide which commit logic to use,
        # which is more robust than checking mode strings.
        shape_fn = getattr(self, "active_shape_fn", "")
        if shape_fn in ["drawRect", "drawEllipse", "drawRoundedRect"]:
            self._commit_generic_shape()
        elif shape_fn in ["drawPolygon", "drawPolyline"]:
            self._commit_generic_poly()
        elif shape_fn == "drawSpline":
            self._commit_spline()
        elif shape_fn == "drawStar":
            self._commit_star()
        elif shape_fn == "drawLine":
            self._commit_line()
        elif self.mode == "text":
            self.timer_cleanup()
            self.reset_mode()
        else:
            # Fallback for other tools that might use is_moving_shape
            self.deselect()

    def _commit_generic_shape(self):
        if not self.moving_rect:
            return
        self.timer_cleanup()
        # Ensure we commit to the high-res source image, not the scaled display pixmap
        pixmap = self.pixmap()
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
        
        p.setPen(self.shape_pen)
        if self.config["fill"]:
            p.setBrush(self.shape_brush)
        
        if getattr(self, "shape_rotation", 0) != 0:
            center = self.moving_rect.center()
            if hasattr(center, "toPoint"): center = center.toPoint()
            p.translate(center.x(), center.y())
            p.rotate(self.shape_rotation)
            p.translate(-center.x(), -center.y())

        getattr(p, self.active_shape_fn)(self.moving_rect, *self.active_shape_args)
        p.end()
        self.setPixmap(pixmap)
        self.is_moving_shape = False
        self.reset_mode()

    def _commit_line(self):
        """Rasterize the line with arrows onto the canvas and reset mode."""
        pts = getattr(self, "history_pos", None)
        if not pts or len(pts) < 2:
            self.timer_cleanup()
            self.reset_mode()
            return

        self.timer_cleanup()
        pixmap = self.pixmap()
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
         
        if getattr(self, "shape_rotation", 0) != 0:
            center = self._rotation_pivot()
            p.translate(center.x(), center.y())
            p.rotate(self.shape_rotation)
            p.translate(-center.x(), -center.y())

        self._draw_line_with_arrow(
            p, 
            pts[0], 
            pts[1], 
            self.primary_color, 
            self.config["size"],
            self.config.get("line_type", 0)
        )
        p.end()
        self.setPixmap(pixmap)
        self.is_moving_shape = False
        self.reset_mode()

    def _commit_generic_poly(self):
        if self.active_shape_fn == "drawSpline":
            return self._commit_spline()
        if self.active_shape_fn == "drawLine":
            return self._commit_line()
        if not self.history_pos:
            return
        self.timer_cleanup()
        # Ensure we commit to the high-res source image, not the scaled display pixmap
        pixmap = self.pixmap()
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
        p.setPen(self.shape_pen)
        if self.config["fill"] and self.secondary_color:
            p.setBrush(self.shape_brush)
            
        if getattr(self, "shape_rotation", 0) != 0:
            center = self.moving_rect.center()
            if hasattr(center, "toPoint"): center = center.toPoint()
            p.translate(center.x(), center.y())
            p.rotate(self.shape_rotation)
            p.translate(-center.x(), -center.y())

        getattr(p, self.active_shape_fn)(QPolygon([
            pt.toPoint() if hasattr(pt, "toPoint") else pt
            for pt in self.history_pos
        ]))
        p.end()
        self.setPixmap(pixmap)
        self.is_moving_shape = False
        self.reset_mode()

    def mouseDoubleClickEvent(self, e):
        fn = getattr(self, "%s_mouseDoubleClickEvent" % self.mode, None)
        if fn:
            return fn(e)

    def leaveEvent(self, e):
        # Clear color hover preview when leaving the canvas
        if self.mode == "dropper":
            self.color_hovered.emit(QColor())
        
        # Clear brush preview state when leaving the canvas
        self.hover_pos = None
        self.update()
        
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        # Handle shape/selection movement with arrow keys
        is_moving = getattr(self, "is_moving_shape", False) or self.mode in ["paste", "text"]
        
        if is_moving:
            step = 1
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                step = 10
            
            key = e.key()
            moved = True
            if key == Qt.Key_Left:
                self._move_active_shape(-step, 0)
            elif key == Qt.Key_Right:
                self._move_active_shape(step, 0)
            elif key == Qt.Key_Up:
                self._move_active_shape(0, -step)
            elif key == Qt.Key_Down:
                self._move_active_shape(0, step)
            elif key == Qt.Key_Escape:
                self.abort_operation()
                self.reset_mode()
            else:
                moved = False
            
            if moved:
                e.accept()
                return
        
        super().keyPressEvent(e)

    def _move_active_shape(self, dx, dy):
        """Helper to translate all geometry associated with the active floating shape."""
        delta = QPoint(dx, dy)
        
        if hasattr(self, "moving_rect") and self.moving_rect:
             self.moving_rect.translate(dx, dy)
        
        if hasattr(self, "current_pos") and self.current_pos:
             self.current_pos += delta
        
        if getattr(self, "history_pos", None):
             # Regular list of points (Polygon, Polyline)
             self.history_pos = [p + delta for p in self.history_pos]
        
        if getattr(self, "painter_path", None):
             self.painter_path.translate(dx, dy)
        
        if getattr(self, "ants_path", None):
             self.ants_path.translate(dx, dy)
             
        self.update()

    def wheelEvent(self, e):
        if self.mode == "magnifier":
            # Determine zoom factor: 2.0 for wheel up, 0.5 for wheel down
            factor = 2.0 if e.angleDelta().y() > 0 else 0.5
            
            # Use the center of the displayed region for wheel-to-zoom
            scroll_area = self._find_scroll_area()
            if scroll_area:
                vp = scroll_area.viewport().size()
                # Center of viewport relative to the Canvas widget
                cx_widget = scroll_area.horizontalScrollBar().value() + vp.width() / 2
                cy_widget = scroll_area.verticalScrollBar().value() + vp.height() / 2
                img_pos = QPointF(cx_widget / self.scale, cy_widget / self.scale)
            else:
                # Fallback to cursor position if no scroll area found
                img_pos = self._to_image_pos(e)
                
            self._magnifier_zoom(factor, img_pos)
            e.accept()
            return

        super().wheelEvent(e)

    # Generic events (shared by brush-like tools)

    def generic_mousePressEvent(self, e):
        # Clear redo stack when starting a new stroke
        self._redo_stack.clear()
        try:
            self.redo_available.emit(False)
        except Exception:
            pass

        # Record a snapshot of the image BEFORE we start drawing
        self._record_snapshot()

        # Prepare for undoing the raw stroke if smoothing is enabled
        self._working_image = self._image_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        if self.config.get("smooth", False):
            self._stroke_undo_image = self._working_image.copy()

        self.last_pos = self._to_image_pixel(e)
        self.smoothed_pos = QPointF(self.last_pos)
        
        if e.button() == Qt.MouseButton.LeftButton:
            self.active_color = self.primary_color
        else:
            self.active_color = self.secondary_color
            
        self.stroke_points = [self.last_pos]

    def generic_mouseReleaseEvent(self, e):
        self.last_pos = None
        self.stroke_points = None
        self._working_image = None

    # Mode-specific events.

    # Select polygon events

    def selectpoly_mousePressEvent(self, e):
        if self.is_moving_shape:
            if e.button() == Qt.MouseButton.LeftButton:
                pos = self._to_image_pixel(e)
                
                # Priority: If holding Ctrl, always start a new selection instead of dragging
                if e.modifiers() & Qt.ControlModifier:
                    pass
                elif self._is_selection_hit(pos):
                    # DRAG CONTENT
                    pix = self.copy_selection()
                    center = QRectF(self.moving_rect).center()
                    
                    pre_move_pixmap = self.pixmap().copy()
                    self._clear_selection_area()
                    
                    # Transition to paste mode
                    offset = QPoint(pos.x() - center.x(), pos.y() - center.y())
                    # Use immediate_drag=True and drag_offset=offset to prevent jumping to center
                    self.start_paste(pix, initial_pos=center, immediate_drag=True, drag_offset=offset, original_base=pre_move_pixmap)
                    return
                else:
                    # CLICK OUTSIDE: Commit/Cancel current selection
                    self.finalize_operation()
            elif e.button() == Qt.MouseButton.RightButton:
                pos = self._to_image_pixel(e)
                if not self._is_selection_hit(pos):
                    self.finalize_operation()
                return

        # Start new selection (possibly additive if Ctrl is held)
        if not (e.modifiers() & Qt.ControlModifier):
            # Only reset if we are NOT in the middle of drawing a polygon
            if not self.history_pos:
                self.reset_mode()
        else:
            # Force non-moving state so generic_shape_mousePressEvent initializes a new drag
            self.is_moving_shape = False
            
        self.active_shape_fn = "drawPolygon"
        self.preview_pen = constants.SELECTION_PEN
        self.selectionActive = True
        self.generic_poly_mousePressEvent(e)

    def selectpoly_timerEvent(self, final=False):
        self.generic_poly_timerEvent(final)

    def selectpoly_mouseMoveEvent(self, e):
        if self.is_moving_shape or not self.locked:
            self.generic_poly_mouseMoveEvent(e)
            if self.origin_pos is not None and self.current_pos is not None and not self.locked:
                rect = QRectF(self.origin_pos, self.current_pos).normalized().toRect()
                self.selection_dimensions_changed.emit(rect.width(), rect.height())

    def selectpoly_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
            return self.generic_poly_mouseReleaseEvent(e)

    def selectpoly_mouseDoubleClickEvent(self, e):
        if getattr(self, 'is_moving_shape', False):
            pos = self._to_image_pixel(e)
            if self._is_selection_hit(pos):
                self.finalize_operation()
            return
        
        if self.locked and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return
        self.current_pos = self._to_image_pixel(e)
        self.selectionActive = True
        
        # Finalize the polygon and enter moving mode
        if self.history_pos:
            # Avoid duplicate of last point
            if self.current_pos != self.history_pos[-1]:
                self.history_pos.append(self.current_pos)
            
            # Create a pixel-perfect mask and path (reference Lasso implementation)
            w, h = self._image_pixmap.width(), self._image_pixmap.height()
            mask = QImage(w, h, QImage.Format.Format_ARGB32)
            mask.fill(QColor(Qt.GlobalColor.white))
            
            p = QPainter(mask)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setBrush(QBrush(Qt.GlobalColor.black))
            p.setPen(QPen(Qt.GlobalColor.black))
            p.drawPolygon(QPolygon(self.history_pos))
            p.end()
            
            new_path = self._path_from_mask(mask)
            if not new_path.isEmpty():
                if (e.modifiers() & Qt.KeyboardModifier.ControlModifier) or (getattr(self, "painter_path", None) and not self.painter_path.isEmpty()):
                    self._union_selection(new_path)
                else:
                    self.painter_path = new_path
                
                self.active_shape_fn = "drawPath"
                self.is_moving_shape = True
                self.locked = True
                self.selectionActive = True
                self.moving_rect = self.painter_path.boundingRect().toRect()
                # For move persistence
                self.original_painter_path = QPainterPath(self.painter_path)
                self.poly_orig_tl = self.moving_rect.topLeft()
                
                self.timer_event = self.generic_poly_timerEvent 
                self.status_message_changed.emit("")
                self.update()
            else:
                self.reset_mode()
        else:
            self.locked = True
            self.update()

    def selectpoly_copy(self):
        """Delegates to the unified configuration-aware copy helper."""
        return self.copy_selection()

    # Select free (lasso) events — freehand selection via click-and-drag

    def selectfree_mousePressEvent(self, e):
        if self.is_moving_shape:
            if e.button() == Qt.MouseButton.LeftButton:
                pos = self._to_image_pixel(e)
                # Priority: If holding Ctrl, always start a new selection instead of dragging
                if e.modifiers() & Qt.ControlModifier:
                    pass
                elif self._is_selection_hit(pos):
                    # DRAG CONTENT — same as selectpoly
                    pix = self.copy_selection()
                    center = QRectF(self.moving_rect).center()
                    pre_move_pixmap = self._image_pixmap.copy()
                    self._clear_selection_area()
                    offset = QPoint(pos.x() - center.x(), pos.y() - center.y())
                    self.start_paste(pix, initial_pos=center, immediate_drag=True, drag_offset=offset, original_base=pre_move_pixmap)
                    return
                else:
                    # Click outside — clear and allow a new selection
                    self.finalize_operation()
            elif e.button() == Qt.MouseButton.RightButton:
                pos = self._to_image_pixel(e)
                if not self._is_selection_hit(pos):
                    self.finalize_operation()
                return

        if e.button() == Qt.MouseButton.LeftButton and (not self.locked or e.modifiers() & Qt.ControlModifier):
            # Start a new lasso stroke
            if not (e.modifiers() & Qt.ControlModifier):
                self.reset_mode()
            else:
                self.is_moving_shape = False
                
            self.active_shape_fn = "drawPath"
            self.preview_pen = constants.SELECTION_PEN
            self.selectionActive = True
            pt = self._to_image_pixel(e)
            self.history_pos = [pt]
            self.current_pos = pt
            self.origin_pos = pt
            self.timer_event = self.generic_poly_timerEvent
            self.status_message_changed.emit("Hold and drag to draw a free selection. Release to complete.")

    def selectfree_mouseMoveEvent(self, e):
        if self.is_moving_shape:
            return self.generic_poly_mouseMoveEvent(e)
        # While button held, accumulate points for lasso path
        if e.buttons() & Qt.MouseButton.LeftButton and self.history_pos is not None:
            pt = self._to_image_pixel(e)
            # Record every single pixel move for pixel-perfect freehand path
            if not self.history_pos or pt != self.history_pos[-1]:
                self.history_pos.append(pt)
                self.current_pos = pt
                if self.origin_pos is not None:
                    rect = QRectF(self.origin_pos, self.current_pos).normalized().toRect()
                    self.selection_dimensions_changed.emit(rect.width(), rect.height())
                self.update()

    def selectfree_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
            return self.generic_poly_mouseReleaseEvent(e)

        if e.button() == Qt.MouseButton.LeftButton and self.history_pos and len(self.history_pos) >= 3:
            # Close the lasso and enter the standard moving-selection state
            # Create a pixel-perfect mask and path for the lasso region
            w, h = self._image_pixmap.width(), self._image_pixmap.height()
            mask = QImage(w, h, QImage.Format.Format_ARGB32)
            mask.fill(QColor(Qt.GlobalColor.white)) # background
            
            p = QPainter(mask)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setBrush(QBrush(Qt.GlobalColor.black)) # foreground
            p.setPen(QPen(Qt.GlobalColor.black))
            p.drawPolygon(QPolygon(self.history_pos))
            p.end()
            
            new_path = self._path_from_mask(mask)
            if not new_path.isEmpty():
                if (e.modifiers() & Qt.KeyboardModifier.ControlModifier) or (getattr(self, "painter_path", None) and not self.painter_path.isEmpty()):
                    self._union_selection(new_path)
                else:
                    self.painter_path = new_path
                
                self.active_shape_fn = "drawPath"
                self.is_moving_shape = True
                self.locked = True
                self.selectionActive = True
                self.moving_rect = self.painter_path.boundingRect().toRect()
                # For move persistence
                self.original_painter_path = QPainterPath(self.painter_path)
                self.poly_orig_tl = self.moving_rect.topLeft()
                
                self.timer_event = self.generic_poly_timerEvent 
                self.status_message_changed.emit("")
                self.update()
            else:
                if not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    self.reset_mode()
        else:
            # Not enough points — cancel only if not additive
            if not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.reset_mode()


    def selectfree_mouseDoubleClickEvent(self, e):
        if getattr(self, "is_moving_shape", False) or getattr(self, "locked", False):
            pos = self._to_image_pixel(e)
            if self._is_selection_hit(pos):
                self.finalize_operation()

    def selectfree_timerEvent(self, final=False):
        self.generic_poly_timerEvent(final)

    def selectfree_copy(self):
        """Delegates to the unified configuration-aware copy helper."""
        return self.copy_selection()

    # Select rectangle events

    def selectwand_mousePressEvent(self, e):
        pos = self._to_image_pixel(e)
        
        # If we already have a selection move active, handle it
        if self.is_moving_shape:
            if e.button() == Qt.MouseButton.LeftButton:
                # Priority: If holding Ctrl, always start a new selection instead of dragging
                if e.modifiers() & Qt.ControlModifier:
                    pass
                elif self._is_selection_hit(pos):
                    # DRAG CONTENT
                    pix = self.copy_selection()
                    # Calculate center of path's bounding box
                    brect = self.painter_path.boundingRect()
                    center = brect.center()
                    
                    pre_move_pixmap = self.pixmap().copy()
                    self._clear_selection_area()
                    
                    # Transition to paste mode
                    offset = QPoint(pos.x() - center.x(), pos.y() - center.y())
                    self.start_paste(pix, initial_pos=center, immediate_drag=True, drag_offset=offset, original_base=pre_move_pixmap)
                    return
                else:
                    self.finalize_operation()
                    # Continue to allow making a new selection if clicked outside
            elif e.button() == Qt.MouseButton.RightButton:
                pos = self._to_image_pixel(e)
                if not self._is_selection_hit(pos):
                    self.finalize_operation()
                return

        # NEW SELECTION (possibly additive if Ctrl is held)
        if e.button() == Qt.MouseButton.LeftButton:
            if not (e.modifiers() & Qt.ControlModifier):
                self.reset_mode()
                
            image = self._image_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            tolerance = self.config.get("tolerance", 32)
            
            mask = self._flood_fill(pos, image, tolerance)
            if mask:
                new_path = self._path_from_mask(mask)
                if not new_path.isEmpty():
                    if (e.modifiers() & Qt.ControlModifier) or (getattr(self, "painter_path", None) and not self.painter_path.isEmpty()):
                        self._union_selection(new_path)
                    else:
                        self.painter_path = new_path
                        
                    # Transition to path-based moving mode
                    self.active_shape_fn = "drawPath"
                    self.selectionActive = True
                    self.is_moving_shape = True
                    self.locked = True
                    self.moving_rect = self.painter_path.boundingRect().toRect()
                    self.original_painter_path = QPainterPath(self.painter_path)
                    self.poly_orig_tl = self.moving_rect.topLeft()
                    self.timer_event = self.generic_poly_timerEvent # Re-use walking ants animation
                    self.status_message_changed.emit("Magic Wand: Drag to move the selected area, or click outside to deselect.")
                    self.update()

    def selectwand_mouseMoveEvent(self, e):
        if self.is_moving_shape:
             return self.generic_poly_mouseMoveEvent(e)

    def selectwand_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
              return self.generic_shape_mouseReleaseEvent(e)


    def selectwand_mouseDoubleClickEvent(self, e):
        if getattr(self, "is_moving_shape", False) or getattr(self, "locked", False):
            pos = self._to_image_pixel(e)
            if self._is_selection_hit(pos):
                self.finalize_operation()

    def selectwand_copy(self):
        """Extract pixels based on the painter_path."""
        if not getattr(self, "painter_path", None) or self.painter_path.isEmpty():
            return QPixmap()
            
        brect = self.painter_path.boundingRect().toRect()
        if brect.isEmpty():
            return QPixmap()
            
        from PySide6.QtGui import QImage, QPainter
        src_img = self._image_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        
        # Create transparent target
        target = QImage(brect.width(), brect.height(), QImage.Format.Format_ARGB32)
        target.fill(QColor(0, 0, 0, 0))
        
        # Translate path to target coordinates
        local_path = QPainterPath(self.painter_path)
        local_path.translate(-brect.topLeft())
        
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setClipPath(local_path)
        painter.drawImage(0, 0, src_img, brect.x(), brect.y(), brect.width(), brect.height())
        painter.end()
        
        return QPixmap.fromImage(target)

    def _flood_fill(self, start_pos, image, tolerance):
        """Optimized stack-based flood fill for Magic Wand."""
        w, h = image.width(), image.height()
        start_x, start_y = start_pos.x(), start_pos.y()
        if not (0 <= start_x < w and 0 <= start_y < h):
            return None

        # Pixel data access - using pixelColor is a bit slow but safe
        start_color = image.pixelColor(start_x, start_y)
        sr, sg, sb = start_color.red(), start_color.green(), start_color.blue()
        
        mask = QImage(w, h, QImage.Format.Format_ARGB32)
        mask.fill(QColor(Qt.GlobalColor.white))  # background
        
        tol_sq = tolerance ** 2
        
        # Visited array using a 1D list for speed
        visited = [False] * (w * h)
        stack = [(start_x, start_y)]
        visited[start_y * w + start_x] = True
        
        # Optimization: use a local ref for speed
        get_pixel = image.pixelColor
        set_mask = mask.setPixel
        # Inverting: use Black for the selected region to compensate for Qt's bitmap behavior here
        selected_rgba = QColor(Qt.GlobalColor.black).rgba()
        
        while stack:
            cx, cy = stack.pop()
            set_mask(cx, cy, selected_rgba)
            
            # 4-connected neighbors
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < w and 0 <= ny < h:
                    idx = ny * w + nx
                    if not visited[idx]:
                        c = get_pixel(nx, ny)
                        # Euclidean distance squared
                        dr, dg, db = sr - c.red(), sg - c.green(), sb - c.blue()
                        if (dr*dr + dg*dg + db*db) <= tol_sq:
                            visited[idx] = True
                            stack.append((nx, ny))
        return mask

    def _get_pixel_perfect_ellipse_path(self, rect):
        """Build a QPainterPath for an ellipse that exactly matches QPainter.drawEllipse rasterization."""
        n_rect = rect.normalized()
        if n_rect.isEmpty():
            return QPainterPath()
        
        # Create a small mask just for the rectangle area to be efficient
        r = n_rect.toRect()
        # Add 1px padding to ensure the ellipse fits entirely within the mask
        r = r.adjusted(-1, -1, 1, 1)
        
        from PySide6.QtGui import qRgb
        mask = QImage(r.width(), r.height(), QImage.Format.Format_Mono)
        mask.setColor(0, qRgb(255, 255, 255)) # Index 0 = White (Background)
        mask.setColor(1, qRgb(0, 0, 0))       # Index 1 = Black (Foreground)
        mask.fill(0) # Fill with White
        
        p = QPainter(mask)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # We draw using index 1 (Black)
        p.setBrush(QBrush(QColor(qRgb(0, 0, 0))))
        p.setPen(QPen(QColor(qRgb(0, 0, 0))))
        
        # Draw ellipse relative to the mask's top-left
        local_rect = QRectF(n_rect.translated(-r.topLeft()))
        p.drawEllipse(local_rect)
        p.end()
        
        # Convert to path using the region-of-1-bits logic
        bitmap = QBitmap.fromImage(mask)
        region = QRegion(bitmap)
        path = QPainterPath()
        for rect_part in region:
            path.addRect(QRectF(rect_part))
        
        return path.simplified().translated(r.topLeft())

    def _get_pixel_perfect_poly_path(self, points, close=True):
        """Build a QPainterPath for a polygon that exactly matches QPainter rasterization."""
        if not points:
            return QPainterPath()
        
        from PySide6.QtGui import qRgb
        poly = QPolygonF([QPointF(pt) for pt in points])
        r = poly.boundingRect().toRect()
        r = r.adjusted(-1, -1, 1, 1)
        
        mask = QImage(r.width(), r.height(), QImage.Format.Format_Mono)
        mask.setColor(0, qRgb(255, 255, 255))
        mask.setColor(1, qRgb(0, 0, 0))
        mask.fill(0)
        
        p = QPainter(mask)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setBrush(QBrush(QColor(qRgb(0, 0, 0))))
        p.setPen(QPen(QColor(qRgb(0, 0, 0))))
        
        local_poly = QPolygonF([QPointF(pt.x() - r.x(), pt.y() - r.y()) for pt in points])
        if close:
            p.drawPolygon(local_poly)
        else:
            p.drawPolyline(local_poly)
        p.end()
        
        bitmap = QBitmap.fromImage(mask)
        region = QRegion(bitmap)
        path = QPainterPath()
        for rect_part in region:
            path.addRect(QRectF(rect_part))
        
        return path.simplified().translated(r.topLeft())

    def _path_from_mask(self, mask):
        """Convert a mono mask QImage to a QPainterPath using QRegion.

        The mask uses Qt::color0 (background) for unselected pixels and
        Qt::color1 (foreground) for selected pixels.  QRegion(bitmap)
        includes only color1 pixels, so this produces the correct selection
        without any inversion.
        """
        bitmap = QBitmap.fromImage(mask)
        region = QRegion(bitmap)
        path = QPainterPath()
        for r in region:
            path.addRect(QRectF(r))
        return path.simplified()

    def selectrect_mousePressEvent(self, e):
        if self.is_moving_shape:
            if e.button() == Qt.MouseButton.LeftButton:
                pos = self._to_image_pixel(e)
                
                # Priority: If holding Ctrl, always start a new selection instead of dragging
                if e.modifiers() & Qt.ControlModifier:
                    pass
                elif self._is_selection_hit(pos):
                    # DRAG CONTENT
                    pix = self.copy_selection()
                    center = QRectF(self.moving_rect).center()
                    
                    pre_move_pixmap = self.pixmap().copy()
                    self._clear_selection_area()
                    
                    # Transition to paste mode immediately
                    offset = QPoint(pos.x() - center.x(), pos.y() - center.y())
                    # Use immediate_drag=True and drag_offset=offset to prevent jumping to center
                    self.start_paste(pix, initial_pos=center, immediate_drag=True, drag_offset=offset, original_base=pre_move_pixmap)
                    return
                else:
                    # CLICK OUTSIDE: deselect and fall through to start a new selection.
                    self.finalize_operation()
            elif e.button() == Qt.MouseButton.RightButton:
                pos = self._to_image_pixel(e)
                if not self._is_selection_hit(pos):
                    self.finalize_operation()
                return

        # Start new selection (possibly additive if Ctrl is held)
        if not (e.modifiers() & Qt.ControlModifier):
            self.reset_mode()
        else:
            # Force non-moving state so generic_shape_mousePressEvent initializes a new drag
            self.is_moving_shape = False
        
        self.active_shape_fn = "drawRect"
        self.preview_pen = constants.SELECTION_PEN
        self.selectionActive = True
        self.generic_shape_mousePressEvent(e)


    def selectrect_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def selectrect_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
             return self.generic_shape_mouseReleaseEvent(e)

        self.current_pos = self._get_constrained_pos(self.origin_pos, self._to_image_pixel(e), e.modifiers())
        if self.origin_pos is not None and self.current_pos is not None:
            # Use QRectF-to-Rect conversion to match the drag preview's 10-pixel size (at p=10)
            rect_f = QRectF(self.origin_pos, self.current_pos).normalized()
            
            # Guard against tiny accidental clicks
            if rect_f.width() < 1 and rect_f.height() < 1:
                 # If we were already in selection mode and just clicked, don't reset unless not holding Ctrl
                 if not (e.modifiers() & Qt.ControlModifier):
                      self.reset_mode()
                 return

            if (e.modifiers() & Qt.ControlModifier) or (getattr(self, "painter_path", None) and not self.painter_path.isEmpty()):
                # Add to existing selection
                p = QPainterPath()
                p.addRect(rect_f)
                self._union_selection(p)
            else:
                self.moving_rect = rect_f.toRect()
                self.active_shape_fn = "drawRect"
                
            self.preview_pen = constants.SELECTION_PEN
            self.last_pos = self.current_pos # keep timer active
            self.is_moving_shape = True
            self.selectionActive = True # Suppress drawing-tool previews
            self.locked = True


    def selectrect_mouseDoubleClickEvent(self, e):
        if getattr(self, "is_moving_shape", False) or getattr(self, "locked", False):
            pos = self._to_image_pixel(e)
            if self._is_selection_hit(pos):
                self.finalize_operation()

    def selectrect_copy(self):
        self.timer_cleanup()
        # Use moving_rect if it exists (live positioned bounds), fallback to origin/current
        rect = self.moving_rect if self.moving_rect else QRect(self.origin_pos, self.current_pos).normalized()
        return self.pixmap().copy(rect)

    # Move events (repositioning selection boundary)

    def selectellipse_mousePressEvent(self, e):
        if self.is_moving_shape:
            if e.button() == Qt.MouseButton.LeftButton:
                pos = self._to_image_pixel(e)
                
                # Priority: Additive selection
                if e.modifiers() & Qt.ControlModifier:
                    pass
                elif self._is_selection_hit(pos):
                    # DRAG CONTENT
                    pix = self.copy_selection()
                    center = QRectF(self.moving_rect).center()
                    
                    pre_move_pixmap = self.pixmap().copy()
                    self._clear_selection_area()
                    
                    # Transition to paste mode immediately
                    offset = QPoint(pos.x() - center.x(), pos.y() - center.y())
                    self.start_paste(pix, initial_pos=center, immediate_drag=True, drag_offset=offset, original_base=pre_move_pixmap)
                    return
                else:
                    self.finalize_operation()
            elif e.button() == Qt.MouseButton.RightButton:
                pos = self._to_image_pixel(e)
                if not self._is_selection_hit(pos):
                    self.finalize_operation()
                return

        # Start new selection (possibly additive if Ctrl is held)
        if not (e.modifiers() & Qt.ControlModifier):
            self.reset_mode()
        else:
            self.is_moving_shape = False
            
        self.active_shape_fn = "drawEllipse"
        self.preview_pen = constants.SELECTION_PEN
        self.selectionActive = True
        self.generic_shape_mousePressEvent(e)

    def selectellipse_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def selectellipse_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
             return self.generic_shape_mouseReleaseEvent(e)

        self.current_pos = self._get_constrained_pos(self.origin_pos, self._to_image_pixel(e), e.modifiers())
        if self.origin_pos is not None and self.current_pos is not None:
            rect_f = QRectF(self.origin_pos, self.current_pos).normalized()
            if rect_f.width() < 1 and rect_f.height() < 1:
                 if not (e.modifiers() & Qt.ControlModifier):
                      self.reset_mode()
                 return

            if (e.modifiers() & Qt.ControlModifier) or (getattr(self, "painter_path", None) and not self.painter_path.isEmpty()):
                # Add to existing selection
                p = self._get_pixel_perfect_ellipse_path(rect_f)
                self._union_selection(p)
            else:
                self.moving_rect = rect_f.toRect()
                self.painter_path = self._get_pixel_perfect_ellipse_path(rect_f)
                self.active_shape_fn = "drawEllipse"
                
            self.preview_pen = constants.SELECTION_PEN
            self.last_pos = self.current_pos # keep timer active
            self.is_moving_shape = True
            self.selectionActive = True # Suppress drawing-tool previews
            self.locked = True


    def selectellipse_mouseDoubleClickEvent(self, e):
        if getattr(self, "is_moving_shape", False) or getattr(self, "locked", False):
            pos = self._to_image_pixel(e)
            if self._is_selection_hit(pos):
                self.finalize_operation()

    def selectellipse_copy(self):
        self.timer_cleanup()
        rect = self.moving_rect if self.moving_rect else QRect(self.origin_pos, self.current_pos).normalized()
        
        # Ensure we have the latest pixel-perfect path
        self.painter_path = self._get_pixel_perfect_ellipse_path(QRectF(rect))
        
        return self.selectwand_copy()

    def move_mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.deselect()
            return

        if self.is_moving_shape and self.moving_rect:
            pos = self._to_image_pixel(e)
            
            if self._is_selection_hit(pos):
                self.is_dragging_shape = True

                tl = self.moving_rect.topLeft()
                self._drag_offset = QPoint(pos.x() - tl.x(), pos.y() - tl.y())
                
                # Set appropriate timer event to maintain the "marching ants" animation during move
                if self.active_shape_fn == "drawPath":
                    self.timer_event = self.generic_poly_timerEvent
                else:
                    self.timer_event = self.generic_shape_timerEvent
                
                self.status_message_changed.emit("Moving selection boundary...")
    
    def move_mouseMoveEvent(self, e):
        if self.is_dragging_shape:
            # Delegate to existing generic movement logic based on shape type
            if self.active_shape_fn in ["drawPolygon", "drawPolyline", "drawPath"]:
                return self.generic_poly_mouseMoveEvent(e)
            else:
                return self.generic_shape_mouseMoveEvent(e)

    def move_mouseReleaseEvent(self, e):
        if self.is_dragging_shape:
            self.is_dragging_shape = False
            self.status_message_changed.emit("")

    def deselect(self):
        """Reset the selection state and notify UI."""
        self.timer_cleanup()
        self.locked = False
        self.is_moving_shape = False
        self.is_dragging_shape = False
        self.moving_rect = None
        self.poly_original_points = None
        self.poly_orig_tl = None
        self.painter_path = None
        self.original_painter_path = None
        self.ants_path = None
        self.selectionActive = False
        self.history_pos = []
        self.origin_pos = None
        self.current_pos = None
        self.active_shape_fn = None
        self.status_message_changed.emit("")
        self.update()

    def abort_operation(self):
        """Discard current operation (Escape) and restore pixels if necessary."""
        if self.mode == "paste":
            self.cancel_paste()
            return

        # For shapes/text, just stop the timer without the final commit
        self.timer_event = None
        
        self.locked = False
        self.is_moving_shape = False
        self.is_dragging_shape = False
        self.moving_rect = None
        self.poly_original_points = None
        self.poly_orig_tl = None
        self.selectionActive = False
        self.current_pos = None
        self.origin_pos = None
        self.history_pos = None
        self.painter_path = None
        self.ants_path = None
        self.status_message_changed.emit("")
        self.update()

    def finalize_operation(self):
        """Commit current operation (Paste, Shape, Selection) and return to base state."""
        SELECTION_MODES = {"selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand", "move"}

        if self.mode == "paste":
             self.timer_cleanup()
             self.deselect() # Ensure any background selection is cleared too
             return

        # Selection tools always deselect — checked before is_moving_shape
        # to prevent them from being committed as filled shapes
        if self.mode in SELECTION_MODES:
            self.deselect()
            return

        if getattr(self, "is_moving_shape", False):
            self._commit_any_active_shape()
            self.deselect() # Clean up all preview state after commit
            return

        # Fallback to ensure everything is cleared
        self.deselect()


    def select_all(self):
        """Select the entire canvas area using the rectangle selection tool."""
        if self._image_pixmap is None:
            return
            
        w = self._image_pixmap.width()
        h = self._image_pixmap.height()

        # Reset any previous selection state cleanly
        self.reset_mode()

        # Configure as if the user drew a selection over the whole canvas
        self.active_shape_fn = "drawRect"
        self.preview_pen = constants.SELECTION_PEN
        self.selectionActive = True
        self.origin_pos = QPoint(0, 0)
        self.current_pos = QPoint(w, h)
        self.moving_rect = QRect(0, 0, w, h)
        self.is_moving_shape = True
        self.locked = True
        self.last_pos = self.current_pos  # keep animation timer active
        self.timer_event = self.generic_shape_timerEvent
        self.update()

    def invert_selection(self):
        """Invert the current selection."""
        if self._image_pixmap is None:
            return

        w = self._image_pixmap.width()
        h = self._image_pixmap.height()
        
        full_path = QPainterPath()
        full_path.addRect(0, 0, w, h)
        
        current_path = QPainterPath()
        if self.selectionActive or self.locked:
            if self.active_shape_fn == "drawPath" and getattr(self, "painter_path", None):
                current_path = self.painter_path
            elif self.active_shape_fn in ["drawPolygon", "drawPolyline"] and getattr(self, "history_pos", None):
                current_path.addPolygon(QPolygonF([QPointF(pt) for pt in self.history_pos]))
            elif self.active_shape_fn == "drawEllipse" and self.moving_rect:
                current_path.addPath(self._get_pixel_perfect_ellipse_path(QRectF(self.moving_rect)))
            elif self.moving_rect:
                current_path.addRect(QRectF(self.moving_rect))
        
        # Invert: full_path - current_path
        new_path = full_path.subtracted(current_path)
        
        if new_path.isEmpty():
            self.deselect()
            return

        # Commit any existing floating pixels before changing the selection path
        # Use deselect() if the selection was moved (locked) to ensure it's finalized
        if self.locked and self.mode in ["selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand"]:
             # If it was a moved selection boundary, deselecting is enough as it doesn't move pixels
             self.deselect()

        self.reset_mode() # Clear points etc.
        
        self.active_shape_fn = "drawPath"
        self.painter_path = new_path
        self.original_painter_path = QPainterPath(new_path)
        self.moving_rect = new_path.boundingRect().toRect()
        self.selectionActive = True
        self.locked = True
        self.is_moving_shape = True
        self.timer_event = self.generic_poly_timerEvent
        self.update()



    # Eraser events

    def eraser_mousePressEvent(self, e):
        # Clear redo stack when starting a new stroke
        self._redo_stack.clear()
        try:
            self.redo_available.emit(False)
        except Exception:
            pass

        self._record_snapshot()
        self.last_pos = self._to_image_pixel(e)
        self.stroke_points = [self.last_pos]
        # Match the Delete tool logic: work on a QImage in ARGB32 format for robust transparency
        self._working_image = self._image_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        if self.config.get("smooth", False):
            self._stroke_undo_image = self._working_image.copy()
        
        self._mouse_button_pressed = e.button()
        
        # Trigger immediate drawing for visual feedback on click
        self.eraser_mouseMoveEvent(e)

    def eraser_mouseMoveEvent(self, e):
        if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
            curr = self._to_image_pixel(e)
            if curr == self.last_pos and len(self.stroke_points) > 1:
                return

            # Draw RAW line for immediate, 0-latency feedback
            p = QPainter(self._working_image)
            antialias = self.config.get("antialias", False) or self.config.get("smooth", False)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, antialias)

            # Color Eraser logic: right-click replaces primary color with secondary color
            if getattr(self, "_mouse_button_pressed", None) == Qt.MouseButton.RightButton:
                size = self.config["size"]
                pad = int(size / 2) + 2
                bbox = QRect(self.last_pos, curr).normalized().adjusted(-pad, -pad, pad, pad)
                bbox = bbox.intersected(self._working_image.rect())
                
                region_img = self._working_image.copy(bbox)
                mask_img = region_img.createMaskFromColor(self.primary_color.rgba(), Qt.MaskMode.MaskOutColor)
                bitmap = QBitmap.fromImage(mask_img)
                
                region = QRegion(bitmap)
                region.translate(bbox.topLeft())
                p.setClipRegion(region)
                
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                color = self.secondary_color
            else:
                if self.config.get("fill"):
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    color = self.secondary_color
                else:
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                    color = Qt.GlobalColor.transparent

            p.setPen(QPen(color, self.config["size"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            
            if curr == self.last_pos:
                p.drawPoint(curr)
            else:
                p.drawLine(self.last_pos, curr)
            p.end()

            self.stroke_points.append(curr)
            self.last_pos = curr

            # Start/Reset the timer to convert this raw stroke to a smooth spline after 1 second
            if self.config.get("smooth", False):
                if not hasattr(self, "_smooth_timer"):
                    self._smooth_timer = QTimer()
                    self._smooth_timer.setSingleShot(True)
                    self._smooth_timer.timeout.connect(self._finalize_smooth_stroke)
                self._smooth_timer.start(1000)

            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.setPixmap(self._image_pixmap, record=False)
            self.update()

    def eraser_mouseReleaseEvent(self, e):
        # Finalize smoothing if active
        if self.config.get("smooth", False):
            if hasattr(self, "_smooth_timer"):
                self._smooth_timer.stop()
            self._finalize_smooth_stroke()
        elif self.last_pos is not None and getattr(self, "_working_image", None) is not None:
             # Standard non-smooth commit
             curr = self._to_image_pixel(e)
             p = QPainter(self._working_image)
             p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
             
             if self.config.get("fill"):
                 p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                 color = self.secondary_color
             else:
                 p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                 color = Qt.GlobalColor.transparent

             p.setPen(QPen(color, self.config["size"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
             
             if curr == self.last_pos:
                 p.drawPoint(curr)
             else:
                 p.drawLine(self.last_pos, curr)
             p.end()
             self._image_pixmap = QPixmap.fromImage(self._working_image)
             self.setPixmap(self._image_pixmap, record=False)
             
        self.generic_mouseReleaseEvent(e)

        self._working_image = None
        self.last_pos = None
        self.stroke_points = None
        # Explicitly clear redo stack just in case, though it should be cleared at start
        self._redo_stack.clear()
        try:
            self.redo_available.emit(False)
        except Exception:
            pass

    # Stamp (pie) events

    def stamp_mousePressEvent(self, e):
        pixmap = self.pixmap()
        p = QPainter(pixmap)
        stamp = self.current_stamp
        pos = self._to_image_pos(e)
        point = QPointF(pos.x() - stamp.width() // 2, pos.y() - stamp.height() // 2)
        p.drawPixmap(point, stamp)
        p.end()
        self.setPixmap(pixmap)

    def _get_transparent_stamp(self, pixmap):
        if not self.config.get("paste_fill", True):
            from PySide6.QtGui import QImage
            img = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            bg_target = self.secondary_color.rgb() & 0x00FFFFFF
            for y in range(img.height()):
                for x in range(img.width()):
                    if (img.pixel(x, y) & 0x00FFFFFF) == bg_target:
                        img.setPixel(x, y, 0)
            return QPixmap.fromImage(img)
        return pixmap

    # Paste (clipboard) preview/placement
    def start_paste(self, pixmap, initial_pos=None, immediate_drag=False, drag_offset=None, original_base=None):
        # Save selection geometry before start_paste wipes it via finalize_operation
        saved_path = QPainterPath(self.painter_path) if getattr(self, "painter_path", None) else None
        saved_history = list(self.history_pos) if getattr(self, "history_pos", None) else None
        saved_poly_orig = list(self.poly_original_points) if getattr(self, "poly_original_points", None) else None
        saved_poly_tl = QPoint(self.poly_orig_tl) if getattr(self, "poly_orig_tl", None) else None
        saved_fn = getattr(self, "active_shape_fn", None)
        
        # Commit any existing selection, shape, or paste before starting a new one
        self.finalize_operation()

        # 1. Handle Auto-Resize FIRST before changing mode to avoid tool-reset side effects
        old_w, old_h = self._image_pixmap.width(), self._image_pixmap.height()
        img_w, img_h = pixmap.width(), pixmap.height()
        
        did_auto_resize = False
        # Trigger resize if image is strictly larger than canvas in either dimension
        if img_w > old_w or img_h > old_h:
            new_w = max(old_w, img_w)
            new_h = max(old_h, img_h)
            
            self._perform_resize(new_w, new_h)
            self.canvas_dimensions_changed.emit(new_w, new_h)
            did_auto_resize = True

        # 2. Transition to paste mode
        # Save prior mode before forced reset
        prior = self.mode
        # Use set_mode directly to force a full reset of all drawing/selection flags
        self.set_mode("paste")
        self._prior_mode = prior
        
        # 3. Process transparency if enabled
        self.original_stamp = pixmap
        self.current_stamp = self._get_transparent_stamp(self.original_stamp)

        # 4. Initialize common paste state
        self.original_base = original_base
        self.paste_base = self.pixmap().copy()
        
        # Restore selection geometry into paste mode
        if saved_path:
            self.painter_path = saved_path
            self.original_painter_path = QPainterPath(saved_path)
        if saved_history:
            self.history_pos = saved_history
            self._original_history_pos = list(saved_history)
        if saved_poly_orig:
            self.poly_original_points = saved_poly_orig
        if saved_poly_tl:
            self.poly_orig_tl = saved_poly_tl
        if saved_fn:
            self.active_shape_fn = saved_fn
            
        self.dash_offset = 0
        from PySide6.QtGui import QPen
        self.preview_pen = QPen(constants.SELECTION_PEN)
        self.timer_event = self.paste_timerEvent
        self.is_dragging = immediate_drag
        self._dragged_during_session = False

        if initial_pos:
            self.current_pos = QPointF(initial_pos)
        elif did_auto_resize:
            # Position at (0,0) top-left of the canvas
            self.current_pos = QPointF(img_w / 2.0, img_h / 2.0)
        else:
            # Default to center of the visible area or canvas
            w, h = self._image_pixmap.width(), self._image_pixmap.height()
            cx, cy = w / 2.0, h / 2.0

            scroll_area = self._find_scroll_area()
            if scroll_area is not None:
                s = getattr(self, "scale", 1.0)
                hbar = scroll_area.horizontalScrollBar()
                vbar = scroll_area.verticalScrollBar()
                vp = scroll_area.viewport().size()
                
                canvas_dw = w * s
                canvas_dh = h * s
                
                vis_left = hbar.value()
                vis_top = vbar.value()
                vis_right = min(vis_left + vp.width(), canvas_dw)
                vis_bottom = min(vis_top + vp.height(), canvas_dh)
                
                cx = (vis_left + vis_right) / 2.0 / s
                cy = (vis_top + vis_bottom) / 2.0 / s

            self.current_pos = QPointF(cx, cy)

        # Initialize tracking rect for movement and selection hit-testing
        sw, sh = self.current_stamp.width(), self.current_stamp.height()
        left = self.current_pos.x() - sw / 2.0
        top = self.current_pos.y() - sh / 2.0
        self.moving_rect = QRect(int(left), int(top), sw, sh)
        self._press_pos = initial_pos if initial_pos else self.current_pos
        
        # Capture original state to synchronize selection bounds ("ants") during movement
        self._original_history_pos = list(self.history_pos) if getattr(self, "history_pos", None) else None
        self._original_moving_rect = QRect(self.moving_rect) if getattr(self, "moving_rect", None) else None
        self._original_current_pos = QPointF(self.current_pos)
        
        self._drag_offset = drag_offset if drag_offset else QPoint(0, 0)
        self.update()

    def paste_timerEvent(self, final=False):
        if not getattr(self, "paste_base", None) or not self.current_stamp:
            return

        if final:
            cp = self.current_pos if self.current_pos is not None else QPointF(
                self._image_pixmap.width() / 2.0, self._image_pixmap.height() / 2.0
            )
            top_left = QPointF(cp.x() - self.current_stamp.width() / 2.0, cp.y() - self.current_stamp.height() / 2.0)
            base = self.paste_base
            p = QPainter(base)
            
            rotation = getattr(self, "shape_rotation", 0)
            if rotation != 0:
                p.translate(cp.x(), cp.y())
                p.rotate(rotation)
                p.translate(-cp.x(), -cp.y())
                
            p.drawPixmap(top_left, self.current_stamp)
            p.end()
            self.setPixmap(base)
            # clear paste state and restore prior mode
            self.paste_base = None
            self.current_stamp = None
            prior = getattr(self, "_prior_mode", None)
            if prior:
                self.set_mode(prior)
            self._prior_mode = None
        else:
            # We now rely on paintEvent for rendering and dash animation
            self.update()

    def paste_mouseMoveEvent(self, e):
        if getattr(self, "is_dragging", False):
            # Use explicit subtraction via QPointF to avoid PySide6 QPoint.operator- bug
            pos = self._to_image_pixel(e)
            
            # Detect significant movement to distinguish drag from simple click
            if not getattr(self, "_dragged_during_session", False):
                if getattr(self, "_press_pos", None):
                    diff = QPointF(pos) - QPointF(self._press_pos)
                    if abs(diff.x()) > 2 or abs(diff.y()) > 2:
                        self._dragged_during_session = True
            
            self.current_pos = QPointF(pos.x() - self._drag_offset.x(), pos.y() - self._drag_offset.y())
            
            # Synchronize selection boundary ("ants") and hit-test box with the moved content
            diff = self.current_pos - getattr(self, "_original_current_pos", self.current_pos)
            if getattr(self, "_original_history_pos", None):
                self.history_pos = [QPointF(p.x() + diff.x(), p.y() + diff.y()) for p in self._original_history_pos]
            if getattr(self, "_original_moving_rect", None):
                # Ensure we use a copy of the original rect to prevent cumulative translation drift
                self.moving_rect = self._original_moving_rect.translated(int(diff.x()), int(diff.y()))
            if getattr(self, "original_painter_path", None):
                self.painter_path = self.original_painter_path.translated(diff.x(), diff.y())
            
            if self.moving_rect:
                self.selection_dimensions_changed.emit(self.moving_rect.width(), self.moving_rect.height())

    def cancel_paste(self):
        """Cancel paste and restore either the original pre-move state or the paste base."""
        restore_img = getattr(self, "original_base", None) or getattr(self, "paste_base", None)
        if restore_img:
            self.setPixmap(restore_img, record=False)
        self.paste_base = None
        self.original_base = None
        self.current_stamp = None
        # Return to previous mode if available
        prior = getattr(self, "_prior_mode", None)
        if prior:
            self.set_mode(prior)
        else:
            self.reset_mode()

    def paste_mousePressEvent(self, e):
        # Left-click to start dragging or commit if clicking outside
        if e.button() == Qt.MouseButton.LeftButton:
            if self.current_pos is None:
                return
            
            pos = self._to_image_pixel(e)
            
            # Only drag if clicking INSIDE the (potentially moved) selection
            if self._is_selection_hit(pos):
                self.is_dragging = True
                self._dragged_during_session = False
                self._press_pos = pos
                # Calculate offset from image center (current_pos) to mouse click to prevent snapping
                # Use explicit subtraction for QPoint compatibility to avoid operator- artifacts
                self._drag_offset = QPoint(pos.x() - self.current_pos.x(), pos.y() - self.current_pos.y())
            else:
                self.finalize_operation()
                prior = getattr(self, "_prior_mode", None)
                if prior in ["selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand"]:
                    self.mousePressEvent(e)
                return
        elif e.button() == Qt.MouseButton.RightButton:
            pos = self._to_image_pixel(e)
            if not self._is_selection_hit(pos):
                self.finalize_operation()



    def paste_mouseDoubleClickEvent(self, e):
        pos = self._to_image_pixel(e)
        if self._is_selection_hit(pos):
            self.finalize_operation()

    def paste_mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False



    # Pen events

    def pen_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)
        # Trigger immediate drawing for visual feedback on click
        self.pen_mouseMoveEvent(e)

    def pen_mouseMoveEvent(self, e):
        if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
            curr = self._to_image_pixel(e)
            if curr == self.last_pos and len(self.stroke_points) > 1:
                return

            # Draw RAW line for immediate, 0-latency feedback
            p = QPainter(self._working_image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
            p.setPen(
                QPen(
                    self.active_color,
                    self.config["size"],
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            if curr == self.last_pos:
                p.drawPoint(curr)
            else:
                p.drawLine(self.last_pos, curr)
            p.end()

            self.stroke_points.append(curr)
            self.last_pos = curr

            # Start/Reset the timer to convert this raw stroke to a smooth spline after 1 second of inactivity
            if self.config.get("smooth", False):
                if not hasattr(self, "_smooth_timer"):
                    self._smooth_timer = QTimer()
                    self._smooth_timer.setSingleShot(True)
                    self._smooth_timer.timeout.connect(self._finalize_smooth_stroke)
                self._smooth_timer.start(1000) # 1 second delay

            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.update()

    def _finalize_smooth_stroke(self):
        """Replaces the raw, jittery stroke with a simplified, smooth Catmull-Rom spline."""
        if not self.config.get("smooth", False) or not getattr(self, "stroke_points", None):
            return
            
        # Remove consecutive duplicate points (often caused by mousePress calling mouseMove)
        raw_pts = []
        for pt in self.stroke_points:
            if not raw_pts or pt != raw_pts[-1]:
                raw_pts.append(pt)

        if not raw_pts:
            return

        # Handle single click (tap)
        if len(raw_pts) == 1:
            p = QPainter(self._working_image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            
            if self.mode == "marker":
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
                c = QColor(getattr(self, "_marker_color", self.primary_color))
                c.setAlpha(180)
                size = self.config["size"]
                color = c
            elif self.mode == "brush":
                size = self.config["size"] * constants.BRUSH_MULT
                color = self.active_color
            elif self.mode == "eraser":
                size = self.config["size"]
                if getattr(self, "_mouse_button_pressed", None) == Qt.MouseButton.RightButton:
                    pad = int(size / 2) + 2
                    bbox = QRect(raw_pts[0].x() - pad, raw_pts[0].y() - pad, pad*2, pad*2).intersected(self._working_image.rect())
                    region_img = self._working_image.copy(bbox)
                    mask_img = region_img.createMaskFromColor(self.primary_color.rgba(), Qt.MaskMode.MaskOutColor)
                    region = QRegion(QBitmap.fromImage(mask_img))
                    region.translate(bbox.topLeft())
                    p.setClipRegion(region)
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    color = self.secondary_color
                else:
                    if self.config.get("fill"):
                        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                        color = self.secondary_color
                    else:
                        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                        color = Qt.GlobalColor.transparent
            else: # pen
                size = self.config["size"]
                color = self.active_color

            p.setPen(QPen(color, size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPoint(raw_pts[0])
            p.end()
            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.setPixmap(self._image_pixmap, record=False)
            self.update()
            return

        # 1. Point Thinning / Simplification (for strokes with multiple points)
        thinned_pts = [raw_pts[0]]
        for pt in raw_pts[1:]:
            dist = math.hypot(pt.x() - thinned_pts[-1].x(), pt.y() - thinned_pts[-1].y())
            if dist > 8.0: 
                thinned_pts.append(pt)
        if len(thinned_pts) < 2 or (raw_pts[-1].x() != thinned_pts[-1].x() or raw_pts[-1].y() != thinned_pts[-1].y()):
            thinned_pts.append(raw_pts[-1])

        # 2. Restore image to pre-stroke state
        if hasattr(self, "_stroke_undo_image"):
            self._working_image = self._stroke_undo_image.copy()
        
        # 3. Build and draw the smooth spline path
        path = self._catmull_rom_to_path(thinned_pts)
        
        p = QPainter(self._working_image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        
        # Configure painter based on tool mode
        if self.mode == "marker":
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            c = QColor(getattr(self, "_marker_color", self.primary_color))
            c.setAlpha(180)
            size = self.config["size"]
            color = c
        elif self.mode == "brush":
            size = self.config["size"] * constants.BRUSH_MULT
            color = self.active_color
        elif self.mode == "eraser":
            size = self.config["size"]
            if getattr(self, "_mouse_button_pressed", None) == Qt.MouseButton.RightButton:
                pad = int(size / 2) + 2
                bbox = path.boundingRect().toAlignedRect().adjusted(-pad, -pad, pad, pad)
                bbox = bbox.intersected(self._working_image.rect())
                region_img = self._working_image.copy(bbox)
                mask_img = region_img.createMaskFromColor(self.primary_color.rgba(), Qt.MaskMode.MaskOutColor)
                region = QRegion(QBitmap.fromImage(mask_img))
                region.translate(bbox.topLeft())
                p.setClipRegion(region)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                color = self.secondary_color
            else:
                if self.config.get("fill"):
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    color = self.secondary_color
                else:
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                    color = Qt.GlobalColor.transparent
        else: # pen
            size = self.config["size"]
            color = self.active_color

        p.setPen(
            QPen(
                color,
                size,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawPath(path)
        p.end()
        
        self._image_pixmap = QPixmap.fromImage(self._working_image)
        self.setPixmap(self._image_pixmap, record=False)
        self.update()

    def pen_mouseReleaseEvent(self, e):
        # Finalize smoothing if active
        if self.config.get("smooth", False):
            if hasattr(self, "_smooth_timer"):
                self._smooth_timer.stop()
            self._finalize_smooth_stroke()
        else:
            # Standard non-smooth behavior: draw final dot if needed
            curr = self._to_image_pixel(e)
            if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
                p = QPainter(self._working_image)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
                if abs(self.last_pos.x() - curr.x()) < 1 and abs(self.last_pos.y() - curr.y()) < 1:
                    p.drawPoint(curr)
                p.end()
            
        self.generic_mouseReleaseEvent(e)

    # Brush events

    def brush_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)
        # Trigger immediate drawing for visual feedback on click
        self.brush_mouseMoveEvent(e)

    def brush_mouseMoveEvent(self, e):
        if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
            curr = self._to_image_pixel(e)
            if curr == self.last_pos and len(self.stroke_points) > 1:
                return

            # Draw RAW line for immediate feedback
            p = QPainter(self._working_image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
            p.setPen(
                QPen(
                    self.active_color,
                    self.config["size"] * constants.BRUSH_MULT,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            if curr == self.last_pos:
                p.drawPoint(curr)
            else:
                p.drawLine(self.last_pos, curr)
            p.end()

            self.stroke_points.append(curr)
            self.last_pos = curr

            # Start/Reset smoothing timer
            if self.config.get("smooth", False):
                if not hasattr(self, "_smooth_timer"):
                    self._smooth_timer = QTimer()
                    self._smooth_timer.setSingleShot(True)
                    self._smooth_timer.timeout.connect(self._finalize_smooth_stroke)
                self._smooth_timer.start(1000)

            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.update()

    def brush_mouseReleaseEvent(self, e):
        # Finalize smoothing if active
        if self.config.get("smooth", False):
            if hasattr(self, "_smooth_timer"):
                self._smooth_timer.stop()
            self._finalize_smooth_stroke()
        else:
            # Standard behavior
            curr = self._to_image_pixel(e)
            if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
                p = QPainter(self._working_image)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
                if abs(self.last_pos.x() - curr.x()) < 1 and abs(self.last_pos.y() - curr.y()) < 1:
                    p.drawPoint(curr)
                p.end()
        
        self.generic_mouseReleaseEvent(e)

    # Smudge events

    def smudge_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)
        self.smudge_last_image = None
        if getattr(self, "_working_image", None) is not None:
            size = self.config["smudge_radius"]
            rect = QRect(self.last_pos.x() - size // 2, self.last_pos.y() - size // 2, size, size)
            self.smudge_last_image = self._working_image.copy(rect)
        self.smudge_mouseMoveEvent(e)

    def smudge_mouseMoveEvent(self, e):
        if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
            curr = self._to_image_pixel(e)
            size = self.config["smudge_radius"]
            pressure = self.config["smudge_pressure"] / 100.0
            
            p = QPainter(self._working_image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
            
            dist = math.hypot(curr.x() - self.last_pos.x(), curr.y() - self.last_pos.y())
            steps = max(1, int(dist))
            
            for i in range(1, steps + 1):
                t = i / steps
                x = int(self.last_pos.x() + t * (curr.x() - self.last_pos.x()))
                y = int(self.last_pos.y() + t * (curr.y() - self.last_pos.y()))
                
                if getattr(self, "smudge_last_image", None) is not None:
                    p.setOpacity(pressure)
                    path = QPainterPath()
                    path.addEllipse(x - size // 2, y - size // 2, size, size)
                    p.setClipPath(path)
                    
                    p.drawImage(x - size // 2, y - size // 2, self.smudge_last_image)
                    p.setClipping(False)
                    p.setOpacity(1.0)
                
                rect = QRect(x - size // 2, y - size // 2, size, size)
                self.smudge_last_image = self._working_image.copy(rect)
                
            self.last_pos = curr
            p.end()
            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.update()

    def smudge_mouseReleaseEvent(self, e):
        if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.setPixmap(self._image_pixmap, record=False)
        self.smudge_last_image = None
        self.generic_mouseReleaseEvent(e)

    # Spray events

    def spray_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)
        # Trigger immediate drawing for visual feedback on click
        self.spray_mouseMoveEvent(e)

    def spray_mouseMoveEvent(self, e):
        if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
            p = QPainter(self._working_image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(QPen(self.active_color, 1))

            for n in range(self.config["size"] * constants.SPRAY_PAINT_N):
                xo = random.gauss(0, self.config["size"] * constants.SPRAY_PAINT_MULT)
                yo = random.gauss(0, self.config["size"] * constants.SPRAY_PAINT_MULT)
                pos = self._to_image_pos(e)
                # Snap spray points to integer pixel grid for consistency
                point = QPoint(int(pos.x() + xo), int(pos.y() + yo))
                p.drawPoint(point)
            p.end()
            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.update()

    def spray_mouseReleaseEvent(self, e):
        # Draw a spray burst on click even if mouse didn't move.
        curr = self._to_image_pos(e)
        if self.last_pos is not None and getattr(self, "_working_image", None) is not None:
            p = QPainter(self._working_image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(QPen(self.active_color, 1))

            for n in range(self.config["size"] * constants.SPRAY_PAINT_N):
                xo = random.gauss(0, self.config["size"] * constants.SPRAY_PAINT_MULT)
                yo = random.gauss(0, self.config["size"] * constants.SPRAY_PAINT_MULT)
                # Snap spray points to integer pixel grid for consistency
                point = QPoint(int(curr.x() + xo), int(curr.y() + yo))
                p.drawPoint(point)
            p.end()
            self._image_pixmap = QPixmap.fromImage(self._working_image)
            self.setPixmap(self._image_pixmap, record=False)

        self.generic_mouseReleaseEvent(e)

    # Text events

    def keyPressEvent(self, e):
        # Cancel active operations (Paste, Moving Shape, Locked Selection) with Escape
        if e.key() == Qt.Key.Key_Escape:
            self.abort_operation()
            return


        # Delete key: clear the current selection to transparent for
        # rectangle or polygon selections. This action is recorded
        # (undoable) like a normal edit.
        if e.key() == Qt.Key.Key_Delete:
            # Clear the current selection for all selection tools
            if self.mode in ["selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand"] and getattr(self, "locked", False):
                self._clear_selection_area(transparent=True)
                self.deselect()
                return

        if self.mode == "text":
            if e.key() == Qt.Key.Key_Backspace:
                self.current_text = self.current_text[:-1]
            elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.current_text += "\n"
            else:
                self.current_text = self.current_text + e.text()

    def text_mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.current_pos is None:
            self.current_pos = self._to_image_pixel(e)
            self.current_text = ""
            self.blink_counter = 0
            self.timer_event = self.text_timerEvent
            self.is_moving_shape = True

        elif e.button() == Qt.MouseButton.LeftButton:
            pos = self._to_image_pixel(e)
            # Hit test against the boundary box (even if not visible, it has a hit area)
            boundary = self._get_text_boundary_box()
            if boundary.contains(QPointF(pos)):
                self.is_dragging = True
                self._drag_offset = pos - self.current_pos
            else:
                self.timer_cleanup()
                self.reset_mode()

        elif e.button() == Qt.MouseButton.RightButton and self.current_pos:
            self.abort_operation()
            self.reset_mode()

    def text_mouseMoveEvent(self, e):
        if getattr(self, "is_dragging", False):
            pos = self._to_image_pixel(e)
            self.current_pos = pos - self._drag_offset
            self.update()

    def text_mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False

    def _get_text_boundary_box(self):
        if not self.current_pos:
            return QRectF()
        font = build_font(self.config)
        metrics = QFontMetrics(font)
        # We handle empty text by ensuring a minimum hit area (line height)
        text_to_measure = self.current_text if self.current_text else " "
        br = metrics.boundingRect(QRect(0, 0, 10000, 10000), Qt.AlignLeft | Qt.TextWordWrap, text_to_measure)
        tx = int(self.current_pos.x())
        ty = int(self.current_pos.y() - metrics.ascent())
        text_rect = QRect(tx, ty, br.width(), br.height())
        
        # Consider padding and stroke width
        pw = self.config.get("size", 1) if self.config.get("contour", True) else 0
        pad = 6 + (pw / 2.0)
        return QRectF(text_rect).adjusted(-pad, -pad, pad, pad)

    def text_timerEvent(self, final=False):
        # When final=True commit text to the base image; otherwise draw
        # a transient preview overlay so preview frames don't pollute undo
        # history or leave ghosted artifacts.
        if final:
            pixmap = self.pixmap()
            p = QPainter(pixmap)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            
            # Apply rotation around the text center if requested
            rotation = getattr(self, "shape_rotation", 0)
            if rotation != 0:
                brect = self._get_text_boundary_box()
                cx = brect.center().x()
                cy = brect.center().y()
                p.translate(cx, cy)
                p.rotate(rotation)
                p.translate(-cx, -cy)
            
            self._draw_text_with_background(p, self.current_pos, self.current_text, self.config)
            
            p.end()
            self.setPixmap(pixmap)
            self.last_text = None
            return

        # Create a preview by copying the displayed pixmap and drawing
        # only the current text into it, then show as transient preview.
        self.update()
        # Advance blink counter
        self.blink_counter = getattr(self, "blink_counter", 0) + 1
        self.last_text = self.current_text
        self.last_config = self.config.copy()

    # Fill events

    def fill_mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.active_color = self.primary_color
        else:
            self.active_color = self.secondary_color

        image = self.pixmap().toImage()
        w, h = image.width(), image.height()
        pos = self._to_image_pixel(e)
        x, y = pos.x(), pos.y()

        # Get our target color from origin.
        point = QPoint(x, y)
        target_color = image.pixel(point)

        have_seen = set()
        queue = [(x, y)]

        def get_cardinal_points(have_seen, center_pos):
            points = []
            cx, cy = center_pos
            for x, y in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
                xx, yy = cx + x, cy + y
                if (
                    xx >= 0
                    and xx < w
                    and yy >= 0
                    and yy < h
                    and (xx, yy) not in have_seen
                ):
                    points.append((xx, yy))
                    have_seen.add((xx, yy))

            return points

        # Now perform the search and fill.
        pixmap = self.pixmap()
        p = QPainter(pixmap)
        p.setPen(QPen(self.active_color))

        while queue:
            x, y = queue.pop()
            if image.pixel(x, y) == target_color:
                p.drawPoint(QPoint(x, y))
                queue.extend(get_cardinal_points(have_seen, (x, y)))

        p.end()
        self.setPixmap(pixmap)

    # Dropper events

    def dropper_mousePressEvent(self, e):
        pos = self._to_image_pixel(e)
        # Use a local QImage for performance (toImage is relatively cheap compared to pixmap copies)
        img = getattr(self, "_dropper_image", None)
        if img is None:
             img = self._image_pixmap.toImage()
             
        if 0 <= pos.x() < img.width() and 0 <= pos.y() < img.height():
            c = img.pixel(int(pos.x()), int(pos.y()))
            hex = QColor(c).name()

            if e.button() == Qt.MouseButton.LeftButton:
                self.set_primary_color(hex)
                self.primary_color_updated.emit(hex)  # Update UI.

            elif e.button() == Qt.MouseButton.RightButton:
                self.set_secondary_color(hex)
                self.secondary_color_updated.emit(hex)  # Update UI.
        
        # Notify the main window to revert tool if necessary
        self.color_picked.emit()

    def dropper_mouseMoveEvent(self, e):
        # Update the status bar with the color under the cursor
        pos = self._to_image_pixel(e)
        
        img = getattr(self, "_dropper_image", None)
        if img is None:
             img = self._image_pixmap.toImage()
             self._dropper_image = img

        if 0 <= pos.x() < img.width() and 0 <= pos.y() < img.height():
            c = img.pixel(int(pos.x()), int(pos.y()))
            self.color_hovered.emit(QColor(c))
        else:
            self.color_hovered.emit(QColor()) # Generic invalid color

    # Generic shape events: Rectangle, Ellipse, Rounded-rect

    # Marker events
    def marker_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)
        # Choose color based on which button was pressed
        if e.button() == Qt.MouseButton.RightButton:
            self._marker_color = QColor(self.secondary_color) if self.secondary_color else QColor(Qt.GlobalColor.white)
        else:
            self._marker_color = QColor(self.primary_color)
        # Initialize a path to avoid alpha stacking during a single stroke
        self.stroke_path = QPainterPath()
        self.ants_path = None
        self.stroke_path.moveTo(self.last_pos)
        self.marker_mouseMoveEvent(e)

    def marker_mouseMoveEvent(self, e):
        if hasattr(self, "stroke_path") and getattr(self, "_working_image", None) is not None:
            curr = self._to_image_pixel(e)
            if curr == self.last_pos and len(self.stroke_points) > 1:
                return
            
            # Record point and update raw path
            self.stroke_points.append(curr)
            self.stroke_path.lineTo(curr)
            self.last_pos = curr

            # Start/Reset smoothing timer
            if self.config.get("smooth", False):
                if not hasattr(self, "_smooth_timer"):
                    self._smooth_timer = QTimer()
                    self._smooth_timer.setSingleShot(True)
                    self._smooth_timer.timeout.connect(self._finalize_smooth_stroke)
                self._smooth_timer.start(1000)

            # Draw the RAW path to a display copy for immediate feedback
            display_img = self._working_image.copy()
            p = QPainter(display_img)
            p.setRenderHint(QPainter.Antialiasing, self.config.get("antialias", False))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            
            c = QColor(getattr(self, "_marker_color", self.primary_color))
            c.setAlpha(180)
            p.setPen(
                QPen(
                    c,
                    self.config["size"],
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            if len(self.stroke_points) == 1:
                p.drawPoint(curr)
            else:
                p.drawPath(self.stroke_path)
            p.end()
            
            self._image_pixmap = QPixmap.fromImage(display_img)
            self.update()

    def marker_mouseReleaseEvent(self, e):
        # Finalize smoothing if active
        if self.config.get("smooth", False):
            if hasattr(self, "_smooth_timer"):
                self._smooth_timer.stop()
            self._finalize_smooth_stroke()
        elif hasattr(self, "stroke_path") and getattr(self, "_working_image", None) is not None:
             # Standard non-smooth commit
             p = QPainter(self._working_image)
             p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
             p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
             c = QColor(getattr(self, "_marker_color", self.primary_color))
             c.setAlpha(180)
             p.setPen(QPen(c, self.config["size"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
             if len(self.stroke_points) == 1:
                 p.drawPoint(self.stroke_points[0])
             else:
                 p.drawPath(self.stroke_path)
             p.end()
             self._image_pixmap = QPixmap.fromImage(self._working_image)
             self.setPixmap(self._image_pixmap, record=False)
             
        self._marker_color = None
        self.generic_mouseReleaseEvent(e)

    def generic_shape_mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.reset_mode()
            return

        if self.is_moving_shape:
            if e.button() == Qt.MouseButton.RightButton:
                self.reset_mode()
                return

            pos = self._to_image_pixel(e)
            if self._is_selection_hit(pos):
                self.is_dragging_shape = True
                self._prev_move_pos = pos  # delta-based: remember press position
            elif e.button() == Qt.MouseButton.LeftButton:
                # CLICK OUTSIDE: Commit current shape
                self.generic_shape_mouseDoubleClickEvent(e)
            return

        self.origin_pos = self._to_image_pixel(e)
        self.current_pos = self._to_image_pixel(e)

        self.shape_pen = QPen(
                            self.primary_color,
                            self.config["size"],
                            Qt.PenStyle.SolidLine if self.config.get("contour") else Qt.PenStyle.NoPen,
                            Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin,
                        )
        # If 'Only Fill' (contour=False, fill=True), use primary color for block. Otherwise secondary.
        brush_color = self.primary_color if not self.config.get("contour") else self.secondary_color
        self.shape_brush = QBrush(brush_color)
        self.timer_event = self.generic_shape_timerEvent

    def _draw_generic_shape_moving_preview(self):
        # We no longer draw to a pixmap; paintEvent handles the moving overlay
        self._preview_pixmap = None
        self.update()

    def generic_shape_timerEvent(self, final=False):
        if getattr(self, "is_moving_shape", False):
            self._draw_generic_shape_moving_preview()
            return

        # We now simply call update() and let paintEvent render the overlay
        self.update()
        self.last_pos = self.current_pos

    def generic_shape_mouseMoveEvent(self, e):
        pos = self._to_image_pixel(e)
        if not self.is_moving_shape and getattr(self, "origin_pos", None) is not None:
            pos = self._get_constrained_pos(self.origin_pos, pos, e.modifiers())
        
        self.current_pos = pos
        if self.is_moving_shape:
            if self.is_dragging_shape:
                prev = getattr(self, "_prev_move_pos", None)
                if prev is not None:
                    dx = pos.x() - prev.x()
                    dy = pos.y() - prev.y()
                    self.moving_rect.translate(dx, dy)
                self._prev_move_pos = pos
                # Ensure the last_pos is updated to prevent any stale checks
                self.last_pos = self.current_pos
                self._draw_generic_shape_moving_preview()
                if self.moving_rect and self.active_shape_fn in ["drawRect", "drawEllipse"]:
                    self.selection_dimensions_changed.emit(self.moving_rect.width(), self.moving_rect.height())
            return

        if self.origin_pos is not None and self.active_shape_fn in ["drawRect", "drawEllipse"]:
            if self.mode in ["selectrect", "selectellipse"]:
                rect = QRectF(self.origin_pos, self.current_pos).normalized().toRect()
            else:
                # Rectangle shape tool uses inclusive dimensions in this app
                rect = QRect(self.origin_pos, self.current_pos).normalized()
            self.selection_dimensions_changed.emit(rect.width(), rect.height())
    
    def generic_shape_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
            self.is_dragging_shape = False
            self._prev_move_pos = None  # clear delta baseline
            return

        self.current_pos = self._get_constrained_pos(self.origin_pos, self._to_image_pixel(e), e.modifiers())
        if self.last_pos is not None:
            self.moving_rect = QRect(self.origin_pos, self.current_pos).normalized()
            # A single click without significant dragging should not create a shape
            if self.moving_rect.width() < 2 and self.moving_rect.height() < 2:
                self.reset_mode()
                return

            # Enter moving mode
            self.is_moving_shape = True
            if getattr(self, "selectionActive", False):
                self.status_message_changed.emit("")
            else:
                self.status_message_changed.emit("Drag to move the shape, and press Double-click to accept or Right-click to cancel")
            self.preview_pen = constants.SELECTION_PEN
            # Ensure timer stays active for show_preview and box animation
            self.last_pos = self.current_pos
            self.update()
            return

        self.reset_mode()

    def _get_constrained_pos(self, origin, current, modifiers):
        """Helper to constrain a point to a square aspect ratio relative to an origin if Shift is held."""
        if origin is not None and current is not None and (modifiers & Qt.ShiftModifier):
            dx = current.x() - origin.x()
            dy = current.y() - origin.y()
            # Constrain to the larger of the two axes to create a square
            dist = max(abs(dx), abs(dy))
            return QPoint(origin.x() + (dist if dx >= 0 else -dist),
                         origin.y() + (dist if dy >= 0 else -dist))
        return current

    def _get_angle_constrained_pos(self, origin, current, modifiers):
        """Helper to constrain a point to the nearest 45-degree angle multiple relative to an origin if Shift is held."""
        if origin is not None and current is not None and (modifiers & Qt.ShiftModifier):
            dx = current.x() - origin.x()
            dy = current.y() - origin.y()
            
            # Calculate angle in radians
            angle = math.atan2(dy, dx)
            # Round to nearest 45 degrees (pi/4)
            angle = round(angle / (math.pi / 4)) * (math.pi / 4)
            
            # Calculate distance
            dist = math.sqrt(dx*dx + dy*dy)
            
            # New coordinates
            return QPoint(int(origin.x() + dist * math.cos(angle)),
                          int(origin.y() + dist * math.sin(angle)))
        return current

    def generic_shape_mouseDoubleClickEvent(self, e):
        if self.is_moving_shape and self.moving_rect:
            self._commit_generic_shape()

    # Arrow events

    def line_mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.timer_cleanup()
            self.reset_mode()
            return

        pt = self._to_image_pixel(e)
        self.origin_pos = pt
        self.current_pos = pt

        self.shape_pen = QPen(
            self.primary_color,
            self.config["size"],
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.SquareCap,
            Qt.PenJoinStyle.BevelJoin,
        )
        self.active_shape_fn = "drawLine"
        self.timer_event = self.line_timerEvent

    def line_timerEvent(self, final=False):
        self.update()
        self.last_pos = self.current_pos

    def line_mouseMoveEvent(self, e):
        if self.origin_pos is not None:
            self.current_pos = self._get_angle_constrained_pos(self.origin_pos, self._to_image_pixel(e), e.modifiers())
            self.update()

    def line_mouseReleaseEvent(self, e):
        if self.origin_pos is not None:
            pt = self._get_angle_constrained_pos(self.origin_pos, self._to_image_pixel(e), e.modifiers())
            self.current_pos = pt
            self.history_pos = [self.origin_pos, self.current_pos]
            self._commit_line()

    def line_mouseDoubleClickEvent(self, e):
        pass



    # Simple Line events

    def simpleline_mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.timer_cleanup()
            self.reset_mode()
            return

        pt = self._to_image_pixel(e)
        self.origin_pos = pt
        self.current_pos = pt
        self.shape_pen = QPen(
            self.primary_color,
            self.config["size"],
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.SquareCap,
            Qt.PenJoinStyle.BevelJoin,
        )
        self.active_shape_fn = "drawLine"
        self.timer_event = self.simpleline_timerEvent

    def simpleline_timerEvent(self, final=False):
        self.update()
        self.last_pos = self.current_pos

    def simpleline_mouseMoveEvent(self, e):
        if self.origin_pos is not None:
            self.current_pos = self._get_angle_constrained_pos(self.origin_pos, self._to_image_pixel(e), e.modifiers())
            self.update()

    def simpleline_mouseReleaseEvent(self, e):
        if self.origin_pos is not None:
            pt = self._get_angle_constrained_pos(self.origin_pos, self._to_image_pixel(e), e.modifiers())
            self.current_pos = pt
            self.history_pos = [self.origin_pos, self.current_pos]
            self._commit_line()

    def simpleline_mouseDoubleClickEvent(self, e):
        pass

    # Generic poly events
    def generic_poly_mousePressEvent(self, e):
        if self.is_moving_shape:
            if e.button() == Qt.MouseButton.RightButton:
                self.reset_mode()
                return

            pos = self._to_image_pixel(e)
            # Use unified hit detection for precise contour check
            if self._is_selection_hit(pos):
                self.is_dragging_shape = True
                self._prev_move_pos = pos  # delta-based: remember press position
            elif e.button() == Qt.MouseButton.LeftButton:
                # CLICK OUTSIDE: Commit current polygon
                self.generic_poly_mouseDoubleClickEvent(e)
            return

        if e.button() == Qt.MouseButton.LeftButton:
            self.shape_pen = QPen(
                                self.primary_color,
                                self.config["size"],
                                Qt.PenStyle.SolidLine if (self.config.get("contour") or self.active_shape_fn == "drawPolyline") else Qt.PenStyle.NoPen,
                                Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin,
                            )
            # If 'Only Fill' (contour=False, fill=True), use primary color for block. Otherwise secondary.
            brush_color = self.primary_color if not self.config.get("contour") else self.secondary_color
            self.shape_brush = QBrush(brush_color)
            pt = self._to_image_pixel(e)
            if self.history_pos:
                # Constrain the next point relative to the previous point in the sequence
                pt = self._get_angle_constrained_pos(self.history_pos[-1], pt, e.modifiers())
                self.history_pos.append(pt)
            else:
                self.history_pos = [pt]
                self.current_pos = pt
                self.timer_event = self.generic_poly_timerEvent
                self.status_message_changed.emit("Press Double-click to complete the action or Right-click to cancel")

        elif e.button() == Qt.MouseButton.RightButton and self.history_pos:
            # Clean up, we're not drawing
            self.timer_cleanup()
            self.reset_mode()
    def generic_poly_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
            self.is_dragging_shape = False
            self._prev_move_pos = None  # clear delta baseline
            return
            
        # Explicitly re-grab mouse to ensure hover tracking works off-canvas
        # even after the initial click-press grab is released by the OS.
        if self.timer_event == self.generic_poly_timerEvent:
            self.grabMouse()

    def _draw_generic_poly_moving_preview(self):
        # We no longer draw to a pixmap; paintEvent handles the moving overlay
        self._preview_pixmap = None
        self.update()

    def generic_poly_timerEvent(self, final=False):
        if not final and self.timer_event == self.generic_poly_timerEvent:
            # Poll mouse position during both drag and hover phases for off-window support
            pos = self._to_image_pixel(None)
            if not getattr(self, "is_moving_shape", False) and getattr(self, "history_pos", None):
                pos = self._get_angle_constrained_pos(self.history_pos[-1], pos, QApplication.keyboardModifiers())
            self.current_pos = pos
            self.hover_pos = self.current_pos
            
            if getattr(self, "is_moving_shape", False):
                self._draw_generic_poly_moving_preview()
                return

        self.update()
        self.last_pos = self.current_pos
        self.last_history = (self.history_pos if self.history_pos else []) + [self.current_pos]

    def generic_poly_mouseMoveEvent(self, e):
        pos = self._to_image_pixel(e)
        if not self.is_moving_shape and getattr(self, "history_pos", None):
            pos = self._get_angle_constrained_pos(self.history_pos[-1], pos, e.modifiers())
        
        self.current_pos = pos
        if self.is_moving_shape:
            if self.is_dragging_shape:
                prev = getattr(self, "_prev_move_pos", None)
                if prev is not None:
                    dx = pos.x() - prev.x()
                    dy = pos.y() - prev.y()
                    
                    # Translate the bounding rect
                    self.moving_rect.translate(dx, dy)
                    
                    # Translate history points incrementally
                    if getattr(self, "history_pos", None):
                        self.history_pos = [QPoint(p.x() + dx, p.y() + dy) for p in self.history_pos]
                        # Keep originals in sync so resize still works correctly
                        if getattr(self, "poly_original_points", None):
                            self.poly_original_points = list(self.history_pos)
                    
                    # Translate path incrementally
                    if getattr(self, "painter_path", None):
                        self.painter_path.translate(dx, dy)
                        if getattr(self, "original_painter_path", None):
                            self.original_painter_path = QPainterPath(self.painter_path)
                    
                    # Translate ants path
                    if getattr(self, "ants_path", None):
                        self.ants_path.translate(dx, dy)
                    
                    # Keep poly_orig_tl in sync
                    if getattr(self, "poly_orig_tl", None):
                        self.poly_orig_tl = self.moving_rect.topLeft()
                
                self._prev_move_pos = pos
                # Update last_pos/current_pos to keep timer active and synced
                self.last_pos = self.current_pos
                self._draw_generic_poly_moving_preview()
                if self.moving_rect:
                    self.selection_dimensions_changed.emit(self.moving_rect.width(), self.moving_rect.height())
            return
        
        # Ensure real-time update during drawing phase (e.g. Polygon/Polyline/Spline points)
        self.update()

    # Anchor helpers and paint overlay
    def _anchor_rects_display(self):
        """Return display-space QRect for each anchor box."""
        if not getattr(self, "_image_pixmap", None):
            return {}
        s = getattr(self, "scale", 1.0)
        # If a preview image is active (resize preview), base anchor
        # positions on the preview size so handles follow the live preview.
        if getattr(self, "_preview_pixmap", None):
            w, h = self._preview_pixmap.width(), self._preview_pixmap.height()
        else:
            w, h = self._image_pixmap.width(), self._image_pixmap.height()
        # display coordinates (top-left at 0,0)
        dw, dh = int(w * s), int(h * s)
        size = 8
        # offset so anchor centers sit immediately outside the displayed image (no overlap)
        # add a 1px gap to avoid any overlap due to rounding/antialiasing
        offset = size // 2 + 1
        # bottom center (outside)
        bx = dw // 2
        by = dh + offset
        # right center
        rx = dw + offset
        ry = dh // 2
        # bottom right
        cx = dw + offset
        cy = dh + offset

        return {
            "bottom_center": QRect(bx - size // 2, by - size // 2, size, size),
            "right_center": QRect(rx - size // 2, ry - size // 2, size, size),
            "bottom_right": QRect(cx - size // 2, cy - size // 2, size, size),
        }

    def _anchor_hit(self, pos):
        """Check if the mouse is over the bottom-right resize handle."""
        # Get scaled dimensions
        w = int(self._image_pixmap.width() * self.scale)
        h = int(self._image_pixmap.height() * self.scale)

        # Define a hit-box for the corner (e.g., 10x10 pixels)
        rctBottomRight = QRectF(w - 5, h - 5, 15, 15)
        if rctBottomRight.contains(pos):
            return "bottom_right"
        
        rctBottom = QRectF(w / 2 - 7, h - 5, 15, 15)
        if rctBottom.contains(pos):
            return "bottom_center"
        
        rctRight = QRectF(w - 5, h / 2 - 5, 15, 15)
        if rctRight.contains(pos):
            return "right_center"
        return None

    def _start_anchor_resize(self, e, hit):
        self.resizing = hit

        if hit:
            if hit == "bottom_center":
                self.setCursor(Qt.SizeVerCursor)
            elif hit == "right_center":
                self.setCursor(Qt.SizeHorCursor)
            elif hit == "bottom_right":
                self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.unsetCursor()


    def _end_anchor_resize(self, e):
        """Finalize the resize by creating a new pixmap of the target size."""
        if not getattr(self, "resizing", False):
            return

        # 1. Calculate the new target size in image coordinates
        pos = self._to_image_pos(e)

        sw, sh = self._image_pixmap.width(), self._image_pixmap.height()
        if self.resizing == "bottom_right":
            new_w, new_h = max(1, int(pos.x())), max(1, int(pos.y()))

        if self.resizing == "bottom_center":
            new_w, new_h = sw, max(1, int(pos.y()))

        if self.resizing == "right_center":
            new_w, new_h = max(1, int(pos.x())), sh
        
        self.resizing = ""
        self.unsetCursor()
        self._perform_resize(new_w, new_h)
        self.canvas_dimensions_changed.emit(new_w, new_h)

    def _perform_resize(self, new_w, new_h):
        """Unified method to resize the high-res canvas while preserving content."""
        # 1. Finalize any floating selection/shape before resizing to prevent data loss or holes
        if getattr(self, "is_moving_shape", False):
            self._commit_any_active_shape()

        # 2. Get the current high-res image
        old_pixmap = self.pixmap()
        
        # 3. Create a NEW pixmap with the target dimensions
        from PySide6.QtGui import QImage, QPainter
        new_img = QImage(new_w, new_h, QImage.Format.Format_ARGB32)

        # 4. Handle Background: Fill with the solid background color
        new_img.fill(self.background_color)

        # 5. Paint the old image onto the new one
        painter = QPainter(new_img)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, old_pixmap)
        painter.end()

        # 6. Update the canvas data and refresh the display
        self.setPixmap(QPixmap.fromImage(new_img))
        self._update_display()

    def _perform_resample(self, new_w, new_h, transform_mode=Qt.SmoothTransformation):
        """Unified method to resample (scale) the high-res canvas content to new dimensions."""
        # 1. Finalize any floating selection/shape before resizing to prevent data loss or holes
        if getattr(self, "is_moving_shape", False):
            self._commit_any_active_shape()

        # 2. Get the current high-res image
        old_pixmap = self.pixmap()
        
        # 3. Scale the pixmap to the target dimensions
        new_pixmap = old_pixmap.scaled(new_w, new_h, Qt.IgnoreAspectRatio, transform_mode)

        # 4. Update the canvas data and refresh the display
        self.setPixmap(new_pixmap)
        self.canvas_dimensions_changed.emit(new_w, new_h)
        self._update_display()

    def _selection_anchor_rects_display(self):
        """Return display-space QRectF for each selection anchor box."""
        if not self.moving_rect:
            return {}
        
        # Show handles if selection is active, OR we are in paste mode, OR a shape is being moved/adjusted
        show_handles = (
            getattr(self, "selectionActive", False) or 
            self.mode == "paste" or 
            getattr(self, "is_moving_shape", False)
        )
        
        if not show_handles:
            return {}
        
        s = getattr(self, "scale", 1.0)
        # Use QRectF for floating point precision during scaling
        rect = QRectF(
            self.moving_rect.left() * s,
            self.moving_rect.top() * s,
            self.moving_rect.width() * s,
            self.moving_rect.height() * s
        )
        
        size = 8
        half = size / 2
        
        points = {
            "top_left": QPointF(rect.left(), rect.top()),
            "top_center": QPointF(rect.center().x(), rect.top()),
            "top_right": QPointF(rect.right(), rect.top()),
            "middle_left": QPointF(rect.left(), rect.center().y()),
            "middle_right": QPointF(rect.right(), rect.center().y()),
            "bottom_left": QPointF(rect.left(), rect.bottom()),
            "bottom_center": QPointF(rect.center().x(), rect.bottom()),
            "bottom_right": QPointF(rect.right(), rect.bottom()),
        }
        
        rotation = getattr(self, "shape_rotation", 0)
        if rotation != 0:
            pivot = QPointF(self.moving_rect.center().x() * s, self.moving_rect.center().y() * s)
            transform = QTransform()
            transform.translate(pivot.x(), pivot.y())
            transform.rotate(rotation)
            transform.translate(-pivot.x(), -pivot.y())
            for name, pt in points.items():
                points[name] = transform.map(pt)
                
        return {name: QRectF(pt.x() - half, pt.y() - half, size, size) for name, pt in points.items()}

    def _selection_anchor_hit(self, pos):
        """Check if the mouse is over any selection resize handle."""
        rects = self._selection_anchor_rects_display()
        for name, r in rects.items():
            if r.contains(pos):
                return name
        return None

    def _start_selection_anchor_resize(self, e, hit):
        self.selection_resizing = hit
        self.selection_resize_origin = QRectF(self.moving_rect)
        self.selection_resize_start_pos = self._to_image_pos(e)
        
        # Store original geometry for precision during live resizing
        self._selection_resize_orig_path = QPainterPath(self.painter_path) if getattr(self, "painter_path", None) else None
        self._selection_resize_orig_history = list(self.history_pos) if getattr(self, "history_pos", None) else None
        self._selection_resize_orig_poly = list(self.poly_original_points) if getattr(self, "poly_original_points", None) else None
        
        self.grabMouse()
        self.update()

    def _update_selection_anchor_resize(self, e):
        if not self.selection_resizing:
            return

        mouse_pos = self._to_image_pos(e)
        rotation = getattr(self, "shape_rotation", 0)
        orig_rect = self.selection_resize_origin
        c_old = orig_rect.center()
        
        # Determine the fixed point in local space (the corner opposite to the one being dragged)
        fixed_local = QPointF(c_old)
        if "top" in self.selection_resizing: fixed_local.setY(orig_rect.bottom())
        elif "bottom" in self.selection_resizing: fixed_local.setY(orig_rect.top())
        if "left" in self.selection_resizing: fixed_local.setX(orig_rect.right())
        elif "right" in self.selection_resizing: fixed_local.setX(orig_rect.left())
        
        # Calculate its current world position
        w_fixed = fixed_local
        if rotation != 0:
            t_old = QTransform().translate(c_old.x(), c_old.y()).rotate(rotation).translate(-c_old.x(), -c_old.y())
            w_fixed = t_old.map(fixed_local)
            
            # Inverse rotate mouse position around the old center
            t_inv = QTransform().translate(c_old.x(), c_old.y()).rotate(-rotation).translate(-c_old.x(), -c_old.y())
            mouse_local = t_inv.map(mouse_pos)
        else:
            mouse_local = mouse_pos
            
        rect = QRectF(orig_rect)
        
        # Update local boundary based on un-rotated mouse position
        if "top" in self.selection_resizing:
            rect.setTop(mouse_local.y())
        if "bottom" in self.selection_resizing:
            rect.setBottom(mouse_local.y())
        if "left" in self.selection_resizing:
            rect.setLeft(mouse_local.x())
        if "right" in self.selection_resizing:
            rect.setRight(mouse_local.x())
            
        rect = rect.normalized()
        c_new = rect.center()
        
        # Keep the fixed point stable in world space by translating the local rect
        if rotation != 0:
            t_new = QTransform().translate(c_new.x(), c_new.y()).rotate(rotation).translate(-c_new.x(), -c_new.y())
            w_fixed_new_untranslated = t_new.map(fixed_local)
            delta = w_fixed - w_fixed_new_untranslated
            rect.translate(delta.x(), delta.y())
            
        new_rect = rect.toRect()
        if new_rect != self.moving_rect:
            self._apply_selection_resize(new_rect)
            self.selection_dimensions_changed.emit(new_rect.width(), new_rect.height())
            self.update()

    def _end_selection_anchor_resize(self, e):
        self.selection_resizing = ""
        self.selection_resize_origin = None
        self.selection_resize_start_pos = None
        self._selection_resize_orig_path = None
        self._selection_resize_orig_history = None
        self._selection_resize_orig_poly = None
        try:
            self.releaseMouse()
        except RuntimeError:
            pass
        self.update()

    def _apply_selection_resize(self, new_rect):
        """Scale all geometry and content (if in paste mode) to the new rectangle."""
        orig_rect = QRectF(self.selection_resize_origin)
        if orig_rect.width() == 0 or orig_rect.height() == 0:
            self.moving_rect = new_rect
            return
            
        sx = new_rect.width() / orig_rect.width()
        sy = new_rect.height() / orig_rect.height()
        
        # 1. Scale Path from original
        if getattr(self, "_selection_resize_orig_path", None):
            transform = QTransform()
            transform.translate(new_rect.left(), new_rect.top())
            transform.scale(sx, sy)
            transform.translate(-orig_rect.left(), -orig_rect.top())
            self.painter_path = transform.map(self._selection_resize_orig_path)
            
        # 2. Scale History Points from original (for Polygon/Lasso/Spline)
        if getattr(self, "_selection_resize_orig_history", None):
            self.history_pos = [
                QPoint(
                    int(new_rect.left() + (p.x() - orig_rect.left()) * sx),
                    int(new_rect.top() + (p.y() - orig_rect.top()) * sy)
                )
                for p in self._selection_resize_orig_history
            ]

        # 3. Scale Poly Original Points (for moves)
        if getattr(self, "_selection_resize_orig_poly", None):
            self.poly_original_points = [
                QPoint(
                    int(new_rect.left() + (p.x() - orig_rect.left()) * sx),
                    int(new_rect.top() + (p.y() - orig_rect.top()) * sy)
                )
                for p in self._selection_resize_orig_poly
            ]

        # 4. Scale Content (if in paste mode)
        if self.mode == "paste" and getattr(self, "original_stamp", None):
             self.current_stamp = self.original_stamp.scaled(
                 new_rect.size(),
                 Qt.AspectRatioMode.IgnoreAspectRatio,
                 Qt.TransformationMode.SmoothTransformation
             )
             self.current_stamp = self._get_transparent_stamp(self.current_stamp)
             # Center the stamp at the center of the new rect
             self.current_pos = new_rect.center()
        
        # 5. Update tracking rect
        self.moving_rect = new_rect

        # 6. Update move baseline so subsequent moves respect the new size
        if self.mode == "paste":
            self._original_moving_rect = QRectF(self.moving_rect)
            self._original_current_pos = QPointF(self.current_pos)
            if getattr(self, "history_pos", None):
                self._original_history_pos = list(self.history_pos)
        
        # Also update the baseline for path-based selections/shapes being moved
        if getattr(self, "original_painter_path", None):
            self.original_painter_path = QPainterPath(self.painter_path)
            self.poly_orig_tl = new_rect.topLeft()
        
        # Sync poly_orig_tl for simple shapes too
        if getattr(self, "poly_original_points", None):
            self.poly_orig_tl = new_rect.topLeft()

        self.selection_dimensions_changed.emit(new_rect.width(), new_rect.height())
        self._update_display()

    def _update_anchor_resize(self, e):
        """Show a 'live' preview of the crop/expansion area while dragging."""
        pos = self._to_image_pos(e)

        sw, sh = self._image_pixmap.width(), self._image_pixmap.height()

        if self.resizing == "bottom_right":
            w, h = max(1, int(pos.x())), max(1, int(pos.y()))

        if self.resizing == "bottom_center":
            w, h = sw, max(1, int(pos.y()))

        if self.resizing == "right_center":
            w, h = max(1, int(pos.x())), sh

        # Emit preview dimensions during resize
        try:
            self.canvas_dimensions_changed.emit(w, h)
        except Exception:
            pass

        # Create a preview image that supports transparency
        preview_img = QImage(w, h, QImage.Format.Format_ARGB32)
        # Fill with background color but respect alpha channels in the Source mode drawing
        preview_img.fill(self.background_color)

        from PySide6.QtGui import QPainter
        painter = QPainter(preview_img)
        # Use Source mode to preserve existing transparency during the live preview
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, self._image_pixmap)
        painter.end()

        self._show_preview(QPixmap.fromImage(preview_img))

    def _draw_checkerboard(self, painter, w, h):
        """Draw a tiled checkerboard pattern efficiently using a brush."""
        if not hasattr(self, "_checkerboard_brush"):
            # Create a small checkerboard pattern
            size = 10
            pix = QPixmap(size * 2, size * 2)
            p = QPainter(pix)
            # Use fixed colors matching the old implementation
            p.fillRect(0, 0, size, size, QColor(200, 200, 200))
            p.fillRect(size, size, size, size, QColor(200, 200, 200))
            p.fillRect(size, 0, size, size, QColor(150, 150, 150))
            p.fillRect(0, size, size, size, QColor(150, 150, 150))
            p.end()
            self._checkerboard_brush = QBrush(pix)
        
        painter.save()
        painter.setBrush(self._checkerboard_brush)
        painter.setPen(Qt.PenStyle.NoPen)
        # Reset transform if any to draw checkerboard in widget coordinates or 
        # just draw the visible area. Here we draw the sized rectangle.
        painter.drawRect(0, 0, w, h)
        painter.restore()

    def _draw_text_with_background(self, p, pos, text, config):
        if not pos or text is None:
            return None
            
        font = build_font(config)
        p.setFont(font)
        metrics = QFontMetrics(font)
        
        # Calculate bounding rect for background
        br = metrics.boundingRect(QRect(0, 0, 10000, 10000), Qt.AlignLeft | Qt.TextWordWrap, text)
        tx = int(pos.x())
        ty = int(pos.y() - metrics.ascent())
        text_rect = QRect(tx, ty, br.width(), br.height())
        
        # Add 6px padding for the background rectangle
        # We add half the stroke width to the padding so the 6px gap is measured 
        # from the inner edge of the border.
        pw = config.get("size", 1) if config.get("contour", True) else 0
        pad = 6 + (pw / 2.0)
        bg_rect = QRectF(text_rect).adjusted(-pad, -pad, pad, pad)

        # Background Rectangle: always drawn for the text tool (solidBack behaviour regardless of config)
        if text:
            do_contour = config.get("contour", True)
            do_fill = config.get("fill", True)
            
            p.save()
            # Set pen for border (foreground color, respects stroke width)
            if do_contour:
                bg_pen = QPen(self.primary_color, config.get("size", 1))
                bg_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
                p.setPen(bg_pen)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                
            # Set brush for fill (background color)
            if do_fill:
                p.setBrush(QBrush(self.secondary_color))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
            
            p.drawRect(bg_rect)
            p.restore()

        # Text itself (foreground color)
        p.setPen(QPen(self.primary_color))
        p.drawText(text_rect, Qt.AlignLeft | Qt.TextWordWrap, text)
        return text_rect

    def _rotation_pivot(self):
        """Return the rotation center in image coordinates for the active element.

        Returns a QPointF — text uses the bounding-box center, shapes use
        moving_rect.center().
        """
        if self.mode == "text" and getattr(self, "current_pos", None):
            brect = self._get_text_boundary_box()
            return brect.center()
        if self.mode == "paste" and getattr(self, "current_pos", None):
            c = self.current_pos
            if hasattr(c, "toPoint"):
                c = QPointF(c)
            return c
        if getattr(self, "moving_rect", None):
            c = self.moving_rect.center()
            if hasattr(c, "toPoint"):
                c = QPointF(c)
            return c
        return QPointF(0, 0)

    def _get_rotation_handle_rect(self):

        """Return the rectangle for the rotation handle in widget coordinates.

        The anchor is placed at the bottom-center of the bounding rect (image
        space), then rotated around the shape's/text's center by shape_rotation
        so the handle visually follows the bottom of the element during rotation.
        """
        # Only show for drawing shapes, text tool, AND active selections
        SHAPE_MODES = {"rect", "ellipse", "roundrect", "polygon", "polyline",
                       "regularpoly", "line", "spline", "text"}
        SELECTION_MODES = {"selectrect", "selectellipse", "selectpoly", "selectfree", "selectwand", "move"}
        
        is_active_selection = (
            self.mode in SELECTION_MODES
            and getattr(self, "is_moving_shape", False)
            and getattr(self, "locked", False)
            and getattr(self, "moving_rect", None)
        )
        
        is_paste = (self.mode == "paste" and getattr(self, "current_stamp", None))
        
        if self.mode not in SHAPE_MODES and not is_active_selection and not is_paste:
            return None
        if getattr(self, "selectionActive", False) and self.mode in SHAPE_MODES:
            return None

        s = getattr(self, "scale", 1.0)

        # For text tool: derive the bounding rect from the text boundary box
        if self.mode == "text":
            if not getattr(self, "current_pos", None) or self.timer_event != self.text_timerEvent:
                return None
            brect = self._get_text_boundary_box()
            if brect.isEmpty():
                return None
            rect_left   = brect.left()
            rect_right  = brect.right()
            rect_top    = brect.top()
            rect_bottom = brect.bottom()
        elif is_paste:
            cp = self.current_pos
            w, h = self.current_stamp.width(), self.current_stamp.height()
            rect_left = cp.x() - w // 2
            rect_right = rect_left + w
            rect_top = cp.y() - h // 2
            rect_bottom = rect_top + h
        else:
            # Shape tools: use moving_rect
            if not getattr(self, "is_moving_shape", False) or not getattr(self, "moving_rect", None):
                return None
            rect = self.moving_rect
            if hasattr(rect, "toRect"):
                rect = rect.toRect()
            rect_left   = rect.left()
            rect_right  = rect.right()
            rect_top    = rect.top()
            rect_bottom = rect.bottom()

        # Bottom-center of the bounding rect in image coordinates
        anchor_x = (rect_left + rect_right) / 2.0
        anchor_y = rect_bottom + 1          # one pixel below the bottom edge

        # Shape/text center in image coordinates
        shape_cx = (rect_left  + rect_right)  / 2.0
        shape_cy = (rect_top   + rect_bottom) / 2.0

        # Rotate the anchor around the center by shape_rotation
        rotation = getattr(self, "shape_rotation", 0)
        if rotation != 0:
            rot_rad = math.radians(rotation)
            dx = anchor_x - shape_cx
            dy = anchor_y - shape_cy
            anchor_x = shape_cx + dx * math.cos(rot_rad) - dy * math.sin(rot_rad)
            anchor_y = shape_cy + dx * math.sin(rot_rad) + dy * math.cos(rot_rad)

        # Convert to widget coordinates; centre the 24×24 icon on the anchor
        size = 24
        wx = anchor_x * s
        wy = anchor_y * s
        return QRect(int(wx - size / 2), int(wy), size, size)

    def paintEvent(self, ev):
        # Create a QPainter to draw on the widget
        painter = QPainter(self)

        s = getattr(self, "scale", 1.0)
        # High-performance rendering: Avoid QPixmap.scaled() which is extremely slow at 16x zoom.
        # Instead, we let QPainter handle the scaling during drawPixmap.
        
        # Draw the main image (or preview pixmap if active, e.g. during Resize)
        # with checkerboard background for transparency.
        pix = getattr(self, "_preview_pixmap", None) or getattr(self, "_image_pixmap", None)
        
        if pix:
            w, h = pix.width(), pix.height()
            sw, sh = max(1, int(w * s)), max(1, int(h * s))
            
            # 1. Draw checkerboard efficiently using tiling
            self._draw_checkerboard(painter, sw, sh)
            
            # 2. Draw the pixmap scaled
            painter.save()
            painter.scale(s, s)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(0, 0, pix)
            painter.restore()

        # Draw real-time tool previews (Architectural Fix)
        # This replaces the slow _preview_pixmap approach for shape/polygon tools.
        is_moving = getattr(self, "is_moving_shape", False)
        
        # Check for active drawing previews (Polygon, Line, Rect, etc.)
        is_drawing = (self.timer_event is not None and 
                     not is_moving and 
                     getattr(self, "current_pos", None) is not None)

        if (is_moving or is_drawing) and self.active_shape_fn:
            # High-fidelity pixel-perfect preview approach for ACTUAL shapes
            if not getattr(self, "selectionActive", False):
                # 1. Clear the intermediate low-resolution buffer
                self._preview_overlay_image.fill(Qt.GlobalColor.transparent)
                overlay_painter = QPainter(self._preview_overlay_image)
                overlay_painter.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
                
                # Apply rotation if moving
                if is_moving and getattr(self, "shape_rotation", 0) != 0:
                    center = self._rotation_pivot()
                    overlay_painter.translate(center.x(), center.y())
                    overlay_painter.rotate(self.shape_rotation)
                    overlay_painter.translate(-center.x(), -center.y())
                
                # 2. Set up pens and brushes on the overlay buffer
                current_pen = self.shape_pen if self.shape_pen else QPen(Qt.GlobalColor.black)
                
                # If in 'Only Fill' mode (NoPen) and drawing a polygon, provide a 1px guide pen
                if is_drawing and current_pen.style() == Qt.PenStyle.NoPen and self.active_shape_fn == "drawPolygon":
                    current_pen = QPen(self.primary_color, 1, Qt.PenStyle.SolidLine)
                
                overlay_painter.setPen(current_pen)
                if (self.config.get("fill") and 
                    self.active_shape_fn not in ["drawPolyline", "drawLine", "drawSpline"] and
                    self.shape_brush):
                    overlay_painter.setBrush(self.shape_brush)
                else:
                    overlay_painter.setBrush(Qt.BrushStyle.NoBrush)

                # 3. Draw the shape at 1:1 scale (integer coords)
                if self.active_shape_fn in ["drawPolygon", "drawPolyline"]:
                    pts = (self.history_pos or []) if is_moving else ((self.history_pos or []) + [self.current_pos])
                    if pts:
                        poly = QPolygon(pts)
                        getattr(overlay_painter, self.active_shape_fn)(poly)
                elif self.active_shape_fn == "drawSpline" and (self.history_pos or not is_moving):
                    # Use history_pos; include current cursor only when in initial drawing phase
                    pts = (self.history_pos or []) if is_moving else ((self.history_pos or []) + ([self.current_pos] if self.current_pos is not None else []))
                    if len(pts) >= 2:
                        spline_path = self._bezier_to_path(pts)
                        overlay_painter.drawPath(spline_path)
                elif self.active_shape_fn == "drawLine":
                    # Use history_pos points if moving, else use drag origin/current
                    p1 = p2 = None
                    if is_moving and self.history_pos and len(self.history_pos) >= 2:
                        p1 = self.history_pos[0]
                        p2 = self.history_pos[1]
                    elif not is_moving:
                        p1 = self.origin_pos
                        p2 = self.current_pos
                    if p1 is not None and p2 is not None:
                        self._draw_line_with_arrow(
                            overlay_painter, 
                            p1, 
                            p2, 
                            self.primary_color, 
                            self.config["size"],
                            self.config.get("line_type", 0)
                        )
                elif is_drawing and getattr(self, "origin_pos", None) is not None:
                    if self.active_shape_fn == "drawStar":
                         # Draw star preview during drag (Center-based)
                         center = self.origin_pos
                         dist = math.sqrt((self.current_pos.x() - center.x())**2 + (self.current_pos.y() - center.y())**2)
                         rx = ry = dist
                         ratio = self.config["poly_inner_radius"]
                         path = self._get_star_path(center, rx, ry, rx * ratio, ry * ratio, self.config["poly_vertices"])
                         overlay_painter.drawPath(path)
                    else:
                         rect = QRect(self.origin_pos, self.current_pos).normalized()
                         getattr(overlay_painter, self.active_shape_fn)(rect, *self.active_shape_args)
                elif is_moving and self.moving_rect:
                    if self.active_shape_fn == "drawPath":
                        pass # Path is drawn in the walking ants section
                    elif self.active_shape_fn == "drawStar":
                         center = self.moving_rect.center()
                         rx = self.moving_rect.width() / 2
                         ry = self.moving_rect.height() / 2
                         ratio = self.config["poly_inner_radius"]
                         path = self._get_star_path(center, rx, ry, rx * ratio, ry * ratio, self.config["poly_vertices"])
                         overlay_painter.drawPath(path)
                    else:
                        getattr(overlay_painter, self.active_shape_fn)(self.moving_rect, *self.active_shape_args)
                overlay_painter.end()

                # 4. Draw the pixelated buffer to the scaled widget painter
                painter.save()
                painter.scale(s, s)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                painter.drawImage(0, 0, self._preview_overlay_image)
                painter.restore()

        # Smudge cursor preview (XOR Circle)
        if self.mode == "smudge" and not is_moving and getattr(self, "hover_pos", None) is not None:
            size = self.config.get("smudge_radius", 20)
            painter.save()
            painter.scale(s, s)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            # Use XOR mode as requested to ensure visibility on any background
            painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
            
            # Draw a 1px cosmetic white pen (which will invert because of XOR)
            pen = QPen(Qt.GlobalColor.white, 0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            
            # The circle is centered at hover_pos with radius matching smudge_radius
            # Using integer division to match the smudge implementation's centering
            half = size // 2
            rect = QRect(self.hover_pos.x() - half, self.hover_pos.y() - half, size, size)
            painter.drawEllipse(rect)
            painter.restore()

        # WYSIWYG Brush/Pen/Marker preview (Pixel-Perfect & Performance Optimized)
        if self.mode in ["brush", "pen", "marker"] and not is_drawing and not is_moving and getattr(self, "hover_pos", None) is not None:
            size = self.config["size"]
            if self.mode == "brush":
                size *= constants.BRUSH_MULT
            
            # Use small localized stamp instead of full-canvas overlay to save CPU/Memory
            pad = 2
            stamp_size = int(size + 2*pad)
            stamp = QImage(stamp_size, stamp_size, QImage.Format.Format_ARGB32)
            stamp.fill(Qt.GlobalColor.transparent)
            
            sp = QPainter(stamp)
            sp.setRenderHint(QPainter.RenderHint.Antialiasing, self.mode == "marker")
            
            color = QColor(self.primary_color)
            if self.mode == "marker":
                color.setAlpha(180)
            else:
                color.setAlpha(127)

            sp.setPen(
                QPen(
                    color,
                    size,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            # Draw point in center of stamp
            sp.drawPoint(QPoint(stamp_size//2, stamp_size//2))
            sp.end()
            
            painter.save()
            if self.mode == "marker":
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            
            # Map stamp to widget space
            offset = 0.5 if int(size) % 2 != 0 else 0.0
            half = stamp_size / 2.0
            target = QRectF((self.hover_pos.x() + offset - half) * s, 
                            (self.hover_pos.y() + offset - half) * s, 
                            stamp_size * s, stamp_size * s)
            painter.drawImage(target, stamp)
            painter.restore()

        # 5. Draw high-res metadata (walking ants/selection box) directly on the widget
        painter.save()
        painter.scale(s, s)
        if getattr(self, "selectionActive", False) or is_moving:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
            # Use a 2-image-pixel thick pen and CLIP to the selection.
            # This makes the boundary exactly 1-image-pixel wide and 100% inside.
            box_pen = QPen(Qt.GlobalColor.white, 2)
            box_pen.setStyle(Qt.PenStyle.DashLine)
            box_pen.setDashOffset(self._get_animated_dash_offset())
            painter.setPen(box_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            
            # Apply rotation to the ants as well if moving
            if is_moving and getattr(self, "shape_rotation", 0) != 0:
                center = self._rotation_pivot()
                painter.translate(center.x(), center.y())
                painter.rotate(self.shape_rotation)
                painter.translate(-center.x(), -center.y())
            
            # Identify the path/geometry to clip to
            clip_path = QPainterPath()
            
            if getattr(self, "painter_path", None) and not self.painter_path.isEmpty():
                clip_path.addPath(self.painter_path)
            elif getattr(self, "moving_rect", None) and not self.moving_rect.isEmpty():
                # If we have a simple rect/ellipse selection being moved
                if self.active_shape_fn == "drawEllipse":
                    clip_path.addPath(self._get_pixel_perfect_ellipse_path(QRectF(self.moving_rect)))
                else:
                    clip_path.addRect(QRectF(self.moving_rect))
            
            # 2. Add current drag preview if we are drawing a new selection
            has_start = (getattr(self, "origin_pos", None) is not None) or (getattr(self, "history_pos", None))
            if not is_moving and self.active_shape_fn and has_start and getattr(self, "current_pos", None) is not None:
                if self.active_shape_fn in ["drawRect", "drawEllipse"] and getattr(self, "origin_pos", None) is not None:
                    rect = QRectF(self.origin_pos, self.current_pos).normalized()
                    if not rect.isNull():
                        if self.active_shape_fn == "drawRect":
                            clip_path.addRect(rect)
                        else:
                            # Use pixel-perfect ellipse path for selection preview to match rasterization
                            clip_path.addPath(self._get_pixel_perfect_ellipse_path(rect))
                elif self.active_shape_fn in ["drawPolygon", "drawPolyline", "drawPath"] and getattr(self, "history_pos", None):
                    pts = self.history_pos + [self.current_pos]
                    is_closed = (self.active_shape_fn == "drawPolygon" and len(pts) >= 3)
                    # Use pixel-perfect path for all selection previews (including lines)
                    clip_path.addPath(self._get_pixel_perfect_poly_path(pts, close=is_closed))
            
            if not clip_path.isEmpty():
                painter.save()
                painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
                
                # Use a 2-image-pixel thick pen and CLIP to the selection.
                # This makes the boundary exactly 1-image-pixel wide and 100% inside.
                box_pen = QPen(Qt.GlobalColor.white, 2)
                box_pen.setStyle(Qt.PenStyle.DashLine)
                box_pen.setDashOffset(self._get_animated_dash_offset())
                painter.setPen(box_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                
                
                # For all selection types (Rect, Ellipse, Poly, Lasso), use the clip_path to
                # ensure a pixel-perfect 1-image-pixel wide border.
                # Even for lines, our pixel-perfect path is composed of 1x1 rectangles,
                # so clipping works to reduce the 2-pixel pen to a 1-pixel line.
                if self.selectionActive or is_moving or self.mode == "paste" or self.active_shape_fn in ["drawRect", "drawEllipse", "drawStar", "drawSpline", "drawPolygon", "drawPolyline", "drawLine"]:
                    painter.setClipPath(clip_path)
                elif getattr(self, "history_pos", None) and len(self.history_pos) >= 1:
                    # 1+ points in history means 2+ points total including hover (a line or more)
                    painter.setClipPath(clip_path)
                
                painter.drawPath(clip_path)

                # Draw bounding box for resizing
                if self.moving_rect:
                    bbox = QRectF(self.moving_rect)
                    box_pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
                    painter.setPen(box_pen)
                    painter.drawRect(bbox)
                
                painter.restore()

            elif self.active_shape_fn == "drawStar":
                if is_moving and self.moving_rect:
                    center = self.moving_rect.center()
                    rx = self.moving_rect.width() / 2
                    ry = self.moving_rect.height() / 2
                else:
                    rect = QRect(self.origin_pos, self.current_pos).normalized()
                    side = max(rect.width(), rect.height())
                    center = rect.center()
                    rx = ry = side / 2

                ratio = self.config["poly_inner_radius"]
                n = self.config["poly_vertices"]
                path = self._get_star_path(center, rx, ry, rx * ratio, ry * ratio, n)
                
                painter.save()
                painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
                painter.drawPath(path)
                painter.restore()
                
            elif self.active_shape_fn == "drawSpline":
                pts = self.history_pos if is_moving else (self.history_pos + [self.current_pos])
                if pts and len(pts) >= 2:
                    painter.save()
                    painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
                    path = self._bezier_to_path(pts)
                    painter.drawPath(path)
                    painter.restore()
                
        painter.restore()

        # Draw Paste Preview
        if self.mode == "paste" and getattr(self, "current_stamp", None):
            painter.save()
            painter.scale(s, s)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            cp = self.current_pos
            top_left = QPointF(cp.x() - self.current_stamp.width() / 2.0, cp.y() - self.current_stamp.height() / 2.0)
            
            rotation = getattr(self, "shape_rotation", 0)
            if rotation != 0:
                painter.translate(cp.x(), cp.y())
                painter.rotate(rotation)
                painter.translate(-cp.x(), -cp.y())
                
            painter.drawPixmap(top_left, self.current_stamp)
            
            # Draw animated dashed border
            painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
            
            # Clip to the stamp area and draw with width 2 to get a 1px inner border
            painter.save()
            rect = QRectF(top_left.x(), top_left.y(), self.current_stamp.width(), self.current_stamp.height())
            painter.setClipRect(rect)
            
            pen = QPen(self.preview_pen)
            pen.setWidth(2)
            pen.setDashOffset(self._get_animated_dash_offset())
            painter.setPen(pen)
            
            # Draw the ants using the precise path if available, otherwise fallback to bounding rect
            if getattr(self, "painter_path", None) and not self.painter_path.isEmpty():
                painter.setClipPath(self.painter_path)
                # Ensure the path is drawn with the dash pattern
                painter.drawPath(self.painter_path)
            else:
                rect = QRectF(top_left.x(), top_left.y(), self.current_stamp.width(), self.current_stamp.height())
                painter.setClipRect(rect)
                painter.drawRect(rect)

            painter.restore()
            painter.restore()

        # Draw Text Preview
        if self.mode == "text" and getattr(self, "current_pos", None) is not None and self.timer_event == self.text_timerEvent:
            painter.save()
            painter.scale(s, s)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            
            # Apply rotation around the text bounding-box center
            rotation = getattr(self, "shape_rotation", 0)
            if rotation != 0:
                brect = self._get_text_boundary_box()
                cx = brect.center().x()
                cy = brect.center().y()
                painter.translate(cx, cy)
                painter.rotate(rotation)
                painter.translate(-cx, -cy)
            
            text_rect = self._draw_text_with_background(painter, self.current_pos, self.current_text, self.config)
            
            # Caret (only when not rotated to avoid positioning complexity)
            if rotation == 0 and text_rect and (getattr(self, "blink_counter", 0) // 5) % 2 == 0:
                font = build_font(self.config)
                metrics = QFontMetrics(font)
                lines = self.current_text.split("\n")
                last_line = lines[-1]
                text_width = metrics.horizontalAdvance(last_line)
                caret_x = self.current_pos.x() + text_width
                y_offset = (len(lines) - 1) * metrics.lineSpacing()
                base_y = self.current_pos.y() + y_offset
                painter.setPen(QPen(self.primary_color))
                painter.drawLine(
                    int(caret_x), int(base_y - metrics.ascent()),
                    int(caret_x), int(base_y + metrics.descent())
                )
            painter.restore()
        
        # Brush/Pen/Marker cursor preview (Removed from zoomed painter to use low-res overlay for pixelation)

        # then draw anchors overlay in widget coordinates
        rects = self._anchor_rects_display()
        if not rects:
            painter.end()
            return
        # filled handle with contrasting border so handles are visible on any background
        fill = QColor(0, 120, 215, 200)
        border = QColor(10, 10, 10, 200)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for name, r in rects.items():
            painter.fillRect(r, fill)
            pen = QPen(border)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(r)
        
        # Draw selection anchors in widget coordinates
        sel_rects = self._selection_anchor_rects_display()
        for name, r in sel_rects.items():
            painter.fillRect(r, fill)
            pen = QPen(border)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRect(r)
        
        # Draw Star tool handle in widget coordinates for maximum visibility and clipping immunity
        if self.mode == "regularpoly" and getattr(self, "is_moving_shape", False) and getattr(self, "moving_rect", None):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            
            s = getattr(self, "scale", 1.0)
            center = self.moving_rect.center()
            n = self.config["poly_vertices"]
            rx = self.moving_rect.width() / 2
            ry = self.moving_rect.height() / 2
            ratio = self.config["poly_inner_radius"]
            pts = self._get_star_vertices(center, rx, ry, rx * ratio, ry * ratio, n)
            if pts and len(pts) > 1:
                # Rotate the inner handle point around center by shape_rotation
                raw_pt = pts[1]
                rotation = getattr(self, "shape_rotation", 0)
                if rotation != 0:
                    cx, cy = center.x(), center.y()
                    rot_rad = math.radians(rotation)
                    dx, dy = raw_pt.x() - cx, raw_pt.y() - cy
                    rx = dx * math.cos(rot_rad) - dy * math.sin(rot_rad)
                    ry = dx * math.sin(rot_rad) + dy * math.cos(rot_rad)
                    raw_pt = QPointF(cx + rx, cy + ry)
                pt_widget = QPointF(raw_pt.x() * s, raw_pt.y() * s)
                sz = 12 # Fixed 12px on screen
                painter.setPen(QPen(Qt.GlobalColor.black, 1))
                painter.setBrush(QBrush(QColor(0, 255, 255))) # Cyan
                painter.drawEllipse(QRectF(pt_widget.x() - sz/2, pt_widget.y() - sz/2, sz, sz))
            painter.restore()
            
        # Draw rotation handle if active
        handle_rect = self._get_rotation_handle_rect()
        if handle_rect and not getattr(self, "rotation_icon", None).isNull():
            painter.drawPixmap(handle_rect, self.rotation_icon)

        painter.end()


    def generic_poly_mouseDoubleClickEvent(self, e):
        if self.is_moving_shape:
            if self.active_shape_fn == "drawSpline":
                self._commit_spline()
            elif self.active_shape_fn == "drawLine":
                self._commit_line()
            else:
                self._commit_generic_poly()
            return

        # Finish drawing phase and enter move mode
        if not self.history_pos:
            return
            
        self.history_pos.append(self._to_image_pixel(e))
        
        if getattr(self, "selectionActive", False):
            # For selection, convert to path immediately to maintain the precise boundary
            is_poly = (self.active_shape_fn == "drawPolygon")
            p = self._get_pixel_perfect_poly_path(self.history_pos, close=is_poly)
            
            # Use unified union logic if adding, otherwise set as base path
            if (e.modifiers() & Qt.ControlModifier) or (getattr(self, "painter_path", None) and not self.painter_path.isEmpty()):
                 self._union_selection(p)
            else:
                 self.painter_path = p
                 self.moving_rect = self.painter_path.boundingRect().toRect()
                 self.active_shape_fn = "drawPath"
            
            # Initialize persistence state for the Move tool
            self.original_painter_path = QPainterPath(self.painter_path)
            self.poly_orig_tl = self.moving_rect.topLeft()
            
            self.is_moving_shape = True
            self.locked = True
            self.status_message_changed.emit("")
        else:
            # Regular shape tool logic: keep history_pos for solid rendering
            self.poly_original_points = list(self.history_pos)
            self.moving_rect = QRectF(QPolygon(self.history_pos).boundingRect())
            self.poly_orig_tl = self.moving_rect.topLeft()
            self.is_moving_shape = True
            self.status_message_changed.emit("Drag to move the shape, and press Double-click to accept or Right-click to cancel")
        
        self.preview_pen = constants.SELECTION_PEN
        self.last_pos = self.current_pos # keep timer active
        self.update()

    # Polyline events

    def polyline_mousePressEvent(self, e):
        self.active_shape_fn = "drawPolyline"
        self.preview_pen = constants.PREVIEW_PEN
        self.selectionActive = False
        self.generic_poly_mousePressEvent(e)

    def polyline_timerEvent(self, final=False):
        self.generic_poly_timerEvent(final)

    def polyline_mouseMoveEvent(self, e):
        self.generic_poly_mouseMoveEvent(e)

    def polyline_mouseDoubleClickEvent(self, e):
        self.generic_poly_mouseDoubleClickEvent(e)

    def polyline_mouseReleaseEvent(self, e):
        self.generic_poly_mouseReleaseEvent(e)

    # ------------------------------------------------------------------ #
    # Spline (Catmull-Rom) events                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _catmull_rom_to_path(pts, tension=0.5):
        """Build a QPainterPath for a Catmull-Rom spline through *pts* (QPoint list).

        The curve is guaranteed to pass through every control point.
        At least 2 points are required; a single segment is drawn as a line.
        """
        path = QPainterPath()
        if not pts or len(pts) < 2:
            return path

        # Convert to float tuples for arithmetic
        coords = [(float(p.x()), float(p.y())) for p in pts]

        path.moveTo(*coords[0])

        # Duplicate endpoints so the curve reaches the first/last control points
        extended = [coords[0]] + coords + [coords[-1]]

        for i in range(1, len(extended) - 2):
            p0 = extended[i - 1]
            p1 = extended[i]
            p2 = extended[i + 1]
            p3 = extended[i + 2]

            # Catmull-Rom → cubic Bézier control point conversion
            b1x = p1[0] + (p2[0] - p0[0]) * tension / 3.0
            b1y = p1[1] + (p2[1] - p0[1]) * tension / 3.0
            b2x = p2[0] - (p3[0] - p1[0]) * tension / 3.0
            b2y = p2[1] - (p3[1] - p1[1]) * tension / 3.0

            path.cubicTo(b1x, b1y, b2x, b2y, p2[0], p2[1])

        return path

    @staticmethod
    def _bezier_to_path(pts):
        """Build a QPainterPath for a Bézier curve.
        
        - 2 points: Line
        - 3 points: Quadratic Bézier (pts[0]=start, pts[1]=end, pts[2]=CP1)
        - 4 points: Cubic Bézier (pts[0]=start, pts[1]=end, pts[2]=CP1, pts[3]=CP2)
        """
        path = QPainterPath()
        if not pts:
            return path
        
        path.moveTo(pts[0])
        if len(pts) == 1:
            return path
        
        if len(pts) == 2:
            path.lineTo(pts[1])
        elif len(pts) == 3:
            path.quadTo(pts[2], pts[1])
        else:
            path.cubicTo(pts[2], pts[3], pts[1])
            
        return path

    def spline_mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            if self.history_pos:
                # Right-click cancels
                self.timer_cleanup()
                self.reset_mode()
            return

        if self.is_moving_shape:
            # Delegate to generic poly move logic
            return self.generic_poly_mousePressEvent(e)

        pt = self._to_image_pixel(e)
        if e.button() == Qt.MouseButton.LeftButton:
            self.active_shape_fn = "drawSpline"
            self.preview_pen = constants.PREVIEW_PEN
            self.selectionActive = False
            self.shape_pen = QPen(
                self.primary_color,
                self.config["size"],
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            self.shape_brush = QBrush(self.secondary_color)

            if not getattr(self, "history_pos", None):
                self.history_pos = [pt]
                self.spline_state = 1
                self.is_dragging_end = True
                self.timer_event = self.spline_timerEvent
                self.status_message_changed.emit(
                    "Drag to the ending point."
                )
            elif self.spline_state == 2:
                if len(self.history_pos) == 2:
                    self.history_pos.append(pt)
                else:
                    self.history_pos[2] = pt
                self.is_dragging_cp = True
            elif self.spline_state == 3:
                if len(self.history_pos) == 3:
                    self.history_pos.append(pt)
                else:
                    self.history_pos[3] = pt
                self.is_dragging_cp = True

    def spline_mouseMoveEvent(self, e):
        if self.is_moving_shape:
            return self.generic_poly_mouseMoveEvent(e)
            
        # Update current_pos using the most accurate coordinates available
        pos = self._to_image_pixel(e)
        
        if getattr(self, "is_dragging_cp", False):
            # Update Control Points (currently unconstrained, as is standard)
            self.current_pos = pos
            if self.spline_state == 2:
                # Update CP1
                self.history_pos[2] = self.current_pos
            elif self.spline_state == 3:
                # Update CP2
                self.history_pos[3] = self.current_pos
        elif getattr(self, "is_dragging_end", False) and getattr(self, "history_pos", None):
             # Update End point - Constrain to 45 degree angles relative to start point
             self.current_pos = self._get_angle_constrained_pos(self.history_pos[0], pos, e.modifiers())
             if len(self.history_pos) > 1:
                self.history_pos[1] = self.current_pos
        else:
             self.current_pos = pos
        
        # In state 2 or 3, current_pos serves as a temporary CP preview in paintEvent
        self.update()

    def spline_mouseReleaseEvent(self, e):
        if self.is_moving_shape:
            self.is_dragging_shape = False
            return
            
        if getattr(self, "is_dragging_cp", False):
            self.is_dragging_cp = False
            if self.spline_state == 2:
                self.spline_state = 3
                self.status_message_changed.emit("Drag to locate the second control point.")
            elif self.spline_state == 3:
                self.spline_state = 4
                self.spline_finalize()
        elif getattr(self, "is_dragging_end", False):
            self.is_dragging_end = False
            # Recalculate constrained position for the final point
            self.current_pos = self._get_angle_constrained_pos(self.history_pos[0], self._to_image_pixel(e), e.modifiers())
            
            start_pt = self.history_pos[0]
            if (abs(start_pt.x() - self.current_pos.x()) < 3 and 
                abs(start_pt.y() - self.current_pos.y()) < 3):
                # Click without dragging: reset state
                self.history_pos = None
                self.spline_state = 0
                self.status_message_changed.emit("Click and drag to start drawing the spline.")
                self.update()
                return
                
            self.history_pos.append(self.current_pos)
            self.spline_state = 2
            self.status_message_changed.emit("Drag to locate the first control point.")
            
        # Explicitly re-grab mouse to ensure hover tracking works off-canvas
        # after the button release.
        if self.mode == "spline" and self.spline_state in [2, 3]:
            self.grabMouse()

    def spline_mouseDoubleClickEvent(self, e):
        """Finalize the drawing of the spline and enter move mode."""
        if not self.history_pos:
            return
        
        self.spline_finalize()

    def spline_finalize(self):
        """Transition the spline from drawing to move/modify state."""
        self.poly_original_points = list(self.history_pos)
        path = self._bezier_to_path(self.history_pos)
        self.moving_rect = QRectF(path.boundingRect())
        self.poly_orig_tl = self.moving_rect.topLeft()
        self.preview_pen = constants.SELECTION_PEN
        self.is_moving_shape = True
        self.status_message_changed.emit("Drag to move the spline, and press Double-click to accept or Right-click to cancel")
        self.last_pos = self.current_pos # keep timer active
        self.update()

    def spline_timerEvent(self, final=False):
        if not final:
            # Poll state
            dragging_cp = getattr(self, "is_dragging_cp", False)
            dragging_end = getattr(self, "is_dragging_end", False)
            
            # Detect button release even if mouseReleaseEvent wasn't triggered (e.g. outside window)
            if (dragging_cp or dragging_end) and QApplication.mouseButtons() == Qt.MouseButton.NoButton:
                # Simulate a release to finalize the current drag phase
                self.spline_mouseReleaseEvent(None)
                return

            # Poll the mouse position during both drag and hover phases to ensure 
            # updates even if the cursor moves entirely outside the application window.
            is_active = (dragging_cp or dragging_end or
                         (self.mode == "spline" and getattr(self, "spline_state", 0) in [2, 3]))
            
            if is_active:
                modifiers = QApplication.keyboardModifiers()
                pos = self._to_image_pixel(None)
                if getattr(self, "is_dragging_cp", False):
                    self.current_pos = pos
                    if self.spline_state == 2:
                        self.history_pos[2] = self.current_pos
                    elif self.spline_state == 3:
                        self.history_pos[3] = self.current_pos
                elif getattr(self, "is_dragging_end", False) and getattr(self, "history_pos", None):
                    self.current_pos = self._get_angle_constrained_pos(self.history_pos[0], pos, modifiers)
                    if len(self.history_pos) > 1:
                        self.history_pos[1] = self.current_pos

        self.update()
        self.last_pos = self.current_pos

    def _commit_spline(self):
        """Rasterize the Catmull-Rom spline onto the canvas and reset mode."""
        pts = getattr(self, "history_pos", None)
        if not pts or len(pts) < 2:
            self.timer_cleanup()
            self.reset_mode()
            return

        self.timer_cleanup()
        pixmap = self.pixmap()
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
        p.setPen(self.shape_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)  # splines are always open/unfilled strokes
        
        if getattr(self, "shape_rotation", 0) != 0:
            # Calculate bounding box for center of rotation
            path = self._catmull_rom_to_path(pts)
            center = path.boundingRect().center()
            p.translate(center.x(), center.y())
            p.rotate(self.shape_rotation)
            p.translate(-center.x(), -center.y())

        path = self._bezier_to_path(pts)
        p.drawPath(path)
        p.end()
        self.setPixmap(pixmap)
        self.is_moving_shape = False
        self.reset_mode()

    # Rectangle events

    def rect_mousePressEvent(self, e):
        self.active_shape_fn = "drawRect"
        self.active_shape_args = ()
        self.preview_pen = constants.PREVIEW_PEN
        self.selectionActive = False
        self.generic_shape_mousePressEvent(e)

    def rect_timerEvent(self, final=False):
        self.generic_shape_timerEvent(final)

    def rect_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def rect_mouseReleaseEvent(self, e):
        self.generic_shape_mouseReleaseEvent(e)

    def rect_mouseDoubleClickEvent(self, e):
        self.generic_shape_mouseDoubleClickEvent(e)

    # Polygon events

    def polygon_mousePressEvent(self, e):
        self.active_shape_fn = "drawPolygon"
        self.preview_pen = constants.PREVIEW_PEN
        self.selectionActive = False
        self.generic_poly_mousePressEvent(e)

    def polygon_timerEvent(self, final=False):
        self.generic_poly_timerEvent(final)

    def polygon_mouseMoveEvent(self, e):
        self.generic_poly_mouseMoveEvent(e)

    def polygon_mouseDoubleClickEvent(self, e):
        self.generic_poly_mouseDoubleClickEvent(e)

    def polygon_mouseReleaseEvent(self, e):
        self.generic_poly_mouseReleaseEvent(e)

    # Ellipse events

    def ellipse_mousePressEvent(self, e):
        self.active_shape_fn = "drawEllipse"
        self.active_shape_args = ()
        self.preview_pen = constants.PREVIEW_PEN
        self.selectionActive = False
        self.generic_shape_mousePressEvent(e)

    def ellipse_timerEvent(self, final=False):
        self.generic_shape_timerEvent(final)

    def ellipse_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def ellipse_mouseReleaseEvent(self, e):
        self.generic_shape_mouseReleaseEvent(e)

    def ellipse_mouseDoubleClickEvent(self, e):
        self.generic_shape_mouseDoubleClickEvent(e)

    # Roundedrect events

    def roundrect_mousePressEvent(self, e):
        self.active_shape_fn = "drawRoundedRect"
        self.active_shape_args = (25, 25)
        self.preview_pen = constants.PREVIEW_PEN
        self.selectionActive = False
        self.generic_shape_mousePressEvent(e)

    def roundrect_timerEvent(self, final=False):
        self.generic_shape_timerEvent(final)

    def roundrect_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def roundrect_mouseReleaseEvent(self, e):
        self.generic_shape_mouseReleaseEvent(e)

    def roundrect_mouseDoubleClickEvent(self, e):
        self.generic_shape_mouseDoubleClickEvent(e)

    # Magnifier events

    def magnifier_mousePressEvent(self, e):
        # Determine zoom factor: 2.0 for left-click, 0.5 for others
        factor = 2.0 if e.button() == Qt.MouseButton.LeftButton else 0.5
        
        # Use the cursor position for click-to-zoom
        img_pos = self._to_image_pos(e)
        self._magnifier_zoom(factor, img_pos)

    def _magnifier_zoom(self, factor, img_pos):
        """Unified helper to apply zoom centered on a specific image coordinate."""
        # 1. Apply the zoom
        self.set_scale(self.scale * factor)

        # 2. Scroll to center the specified point in the viewport
        scroll_area = self._find_scroll_area()
        if scroll_area:
            # Calculate where the point is in widget coordinates at the new scale
            s = self.scale
            vp = scroll_area.viewport().size()
            
            # Target center position in the scaled canvas
            target_x = img_pos.x() * s
            target_y = img_pos.y() * s
            
            # Set scrollbars to place that target at the center of the viewport
            scroll_area.horizontalScrollBar().setValue(int(target_x - vp.width() / 2))
            scroll_area.verticalScrollBar().setValue(int(target_y - vp.height() / 2))

    def magnifier_mouseMoveEvent(self, e):
        pass

    def _draw_symmetrical_pixel_line(self, painter, p1, p2, color, size):
        """Hybrid line algorithm: Symmetrical custom logic for 1px, standard RoundCap for >1px."""
        painter.save()
        
        if size == 1:
            # High-precision symmetrical 1px line
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            
            # Sort points for direction independence
            if p1.y() > p2.y() or (p1.y() == p2.y() and p1.x() > p2.x()):
                p1, p2 = p2, p1

            x1, y1 = p1.x(), p1.y()
            x2, y2 = p2.x(), p2.y()
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            sx, sy = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1)
            err = dx - dy
            
            while True:
                painter.drawRect(x1, y1, 1, 1)
                if x1 == x2 and y1 == y2:
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    x1 += sx
                if e2 < dx:
                    err += dx
                    y1 += sy
        else:
            # Standard artistic line with Rounded Caps
            pen = QPen(color, size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(p1, p2)

        painter.restore()

    def _draw_line_with_arrow(self, painter, p1, p2, color, size, line_type, is_preview=False):
        """Draw a line with optional arrow heads based on line_type."""
        if line_type == 0:
            if is_preview:
                painter.drawLine(p1, p2)
            else:
                self._draw_symmetrical_pixel_line(painter, p1, p2, color, size)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
        
        # Draw the main line
        if is_preview:
            # For preview, the pen is already set (dashed white)
            painter.drawLine(p1, p2)
            pen = painter.pen()
        else:
            pen = QPen(color, size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(p1, p2)

        # Draw the arrow head at p2
        angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        
        # Calculate head size based on stroke width
        head_length = max(15, size * 3)
        head_width = head_length * 0.6
        
        # Points for the head
        p2_f = QPointF(p2)
        
        # Type 1: Simple Wings (→)
        if line_type == 1:
            # We want the wings to be separate lines
            wing_angle = math.pi / 6 # 30 degrees
            p_wing1 = p2_f - QPointF(math.cos(angle - wing_angle) * head_length, 
                                     math.sin(angle - wing_angle) * head_length)
            p_wing2 = p2_f - QPointF(math.cos(angle + wing_angle) * head_length, 
                                     math.sin(angle + wing_angle) * head_length)
            
            painter.drawLine(p2_f, p_wing1)
            painter.drawLine(p2_f, p_wing2)
            
        # Type 2: Triangle Head (⭢)
        elif line_type == 2:
            wing_angle = math.pi / 6
            p_wing1 = p2_f - QPointF(math.cos(angle - wing_angle) * head_length, 
                                     math.sin(angle - wing_angle) * head_length)
            p_wing2 = p2_f - QPointF(math.cos(angle + wing_angle) * head_length, 
                                     math.sin(angle + wing_angle) * head_length)
            
            poly = QPolygonF([p2_f, p_wing1, p_wing2])
            
            if is_preview:
                # In preview, we just draw the outline
                painter.drawPolygon(poly)
            else:
                painter.setBrush(QBrush(color))
                painter.drawPolygon(poly)
                
        painter.restore()

    def _get_star_vertices(self, center, rx, ry, irx, iry, n):
        points = []
        angle_step = math.pi / n
        start_angle = -math.pi / 2 # Pointing up
        
        for i in range(2 * n):
            angle = start_angle + i * angle_step
            curr_rx = rx if i % 2 == 0 else irx
            curr_ry = ry if i % 2 == 0 else iry
            points.append(QPointF(center.x() + curr_rx * math.cos(angle), center.y() + curr_ry * math.sin(angle)))
        return points

    def _get_star_path(self, center, rx, ry, irx, iry, n):
        path = QPainterPath()
        pts = self._get_star_vertices(center, rx, ry, irx, iry, n)
        if not pts:
            return path
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        path.closeSubpath()
        return path

    def regularpoly_mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MouseButton.RightButton:
            if self.is_moving_shape:
                 self.finalize_operation()
            else:
                 self.reset_mode()
            return

        pos = self._to_image_pixel(e)
        
        # Check for handle interaction first
        if self.is_moving_shape:
            handle = self._poly_handle_hit(self._event_widget_pos(e))
            if handle:
                self.is_dragging_handle = handle
                return
            
            if self._is_selection_hit(pos):
                self.is_dragging_shape = True
                self._prev_move_pos = pos  # delta-based: remember press position
                return
            else:
                self.finalize_operation()
                # Fall through to potentially start new one

        self.origin_pos = pos
        self.current_pos = pos
        self.active_shape_fn = "drawStar"
        self.preview_pen = constants.PREVIEW_PEN
        self.selectionActive = False
        
        self.shape_pen = QPen(
                            self.primary_color,
                            self.config["size"],
                            Qt.PenStyle.SolidLine if self.config.get("contour") else Qt.PenStyle.NoPen,
                            Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin,
                        )
        brush_color = self.primary_color if not self.config.get("contour") else self.secondary_color
        self.shape_brush = QBrush(brush_color)

        self.timer_event = self.regularpoly_timerEvent
        self.status_message_changed.emit("Drag to define size. Later use handles to adjust radii.")

    def regularpoly_mouseMoveEvent(self, e):
        pos_pixel = self._to_image_pixel(e)
        pos_widget = self._event_widget_pos(e)
        
        if getattr(self, "is_dragging_handle", None):
            center = self.moving_rect.center()
            dist = math.sqrt((pos_pixel.x() - center.x())**2 + (pos_pixel.y() - center.y())**2)
            if self.is_dragging_handle == "inner":
                # Update inner radius ratio
                outer_r = self.moving_rect.width() / 2
                if outer_r > 0:
                    self.config["poly_inner_radius"] = max(0.01, min(1.0, dist / outer_r))
            self.update()
            return

        self.current_pos = pos_pixel
        if self.is_moving_shape and self.is_dragging_shape:
            prev = getattr(self, "_prev_move_pos", None)
            if prev is not None:
                dx = pos_pixel.x() - prev.x()
                dy = pos_pixel.y() - prev.y()
                self.moving_rect.translate(dx, dy)
            self._prev_move_pos = pos_pixel
            self.update()
        
    def regularpoly_mouseReleaseEvent(self, e):
        if getattr(self, "is_dragging_handle", None):
            self.is_dragging_handle = None
            return

        if self.is_moving_shape:
            self.is_dragging_shape = False
            self._prev_move_pos = None  # clear delta baseline
            return

        if self.origin_pos is not None and self.current_pos is not None:
            center = self.origin_pos
            dist = math.sqrt((self.current_pos.x() - center.x())**2 + (self.current_pos.y() - center.y())**2)
            outer_r = int(dist)
            # Create a bounding square for the polygon (for generic move logic compatibility)
            self.moving_rect = QRect(int(center.x() - outer_r), int(center.y() - outer_r), 
                                     int(outer_r * 2), int(outer_r * 2))
            
            if outer_r > 2:
                self.is_moving_shape = True
                self.locked = True
                self.status_message_changed.emit("Drag inside to move. Drag handle to adjust star inner radius. Double-click or Right-click to commit.")
            else:
                self.reset_mode()
        
    def regularpoly_mouseDoubleClickEvent(self, e):
        self._commit_any_active_shape()



    def regularpoly_timerEvent(self, final=False):
        if not final:
            # Poll mouse position during both drag and hover phases for off-window support
            self.current_pos = self._to_image_pixel(None)
        self.update()

    def _poly_handle_hit(self, pos_widget):
        if not self.mode == "regularpoly" or not self.is_moving_shape or not self.moving_rect:
            return None
            
        s = self.scale
        center = self.moving_rect.center()
        rx = self.moving_rect.width() / 2
        ry = self.moving_rect.height() / 2
        ratio = self.config["poly_inner_radius"]
        n = self.config["poly_vertices"]
        
        pts = self._get_star_vertices(center, rx, ry, rx * ratio, ry * ratio, n)
        if not pts: return None
        
        # Rotate the inner handle point by shape_rotation before hit-testing
        raw_inner = pts[1]
        rotation = getattr(self, "shape_rotation", 0)
        if rotation != 0:
            cx, cy = center.x(), center.y()
            rot_rad = math.radians(rotation)
            dx, dy = raw_inner.x() - cx, raw_inner.y() - cy
            rx = dx * math.cos(rot_rad) - dy * math.sin(rot_rad)
            ry = dx * math.sin(rot_rad) + dy * math.cos(rot_rad)
            raw_inner = QPointF(cx + rx, cy + ry)
        h_inner = QPointF(raw_inner.x() * s, raw_inner.y() * s)
        
        if (QPointF(pos_widget) - h_inner).manhattanLength() < 10:
            return "inner"
        return None

    def _commit_star(self):
        if not self.moving_rect:
            return
        self.timer_cleanup()
        pixmap = self.pixmap()
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, self.config.get("antialias", False))
        
        p.setPen(self.shape_pen)
        if self.config["fill"]:
            p.setBrush(self.shape_brush)
        
        if getattr(self, "shape_rotation", 0) != 0:
            center = self.moving_rect.center()
            if hasattr(center, "toPoint"): center = center.toPoint()
            p.translate(center.x(), center.y())
            p.rotate(self.shape_rotation)
            p.translate(-center.x(), -center.y())

        center = self.moving_rect.center()
        rx = self.moving_rect.width() / 2
        ry = self.moving_rect.height() / 2
        ratio = self.config["poly_inner_radius"]
        n = self.config["poly_vertices"]
        path = self._get_star_path(center, rx, ry, rx * ratio, ry * ratio, n)
        p.drawPath(path)
        p.end()
        self.setPixmap(pixmap)
        self.is_moving_shape = False
        self.reset_mode()

