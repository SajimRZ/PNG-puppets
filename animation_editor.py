"""
animation_editor.py

A companion tool to model_editor.py for authoring keyframe animations on a
PNG-puppet rig that was saved with model_editor.

File responsibilities (kept strictly separate)
---------------------------------------------
* RIG JSON (the file produced by model_editor.py) is READ-ONLY here. It tells
  us which bones exist, their parents, image paths, etc. We never write
  rotation values back into it.
* ANIMATION JSON is a SEPARATE file. It contains ONLY:
    - the rig identity (just enough to point at which rig it animates), and
    - the keyframe list: per keyframe, which bone gets which rotation,
      with the transition interval and easing curve.
  Rotation data captured in this editor NEVER touches the rig file.

Key design decisions / constraints inherited from model_editor.py
---------------------------------------------------------------
* Uses PyQt5, the same dark theme, and the same widget palette.
* The detached-pixmap bone architecture (BoneNode.pixmap_item is a TOP-LEVEL
  scene item so global image Z-level works regardless of bone hierarchy) is
  preserved verbatim.
* The bone JSON schema is read exactly the way model_editor wrote it, so a
  rig file round-trips between the two tools.
* Modes (Move Joint / Move Image / Pose) are reused so the user can pose the
  rig in POSE mode, hit "Capture", and the snapshot records every bone's FK
  rotation at that instant.

Features
--------
* Canvas: same wheel zoom + middle-button pan + selection behaviour as the
  model editor.
* Timeline: a list of keyframes at the bottom of the window. Each row shows
  index, time (ms), easing, and a delete button.
* Capture: snaps every bone's current rotation into the timeline at the end.
  Re-capturing on an EXISTING keyframe (selected row) updates that keyframe's
  rotation snapshot instead of appending.
* Interval: per-keyframe duration in milliseconds (default 100 ms).
* Easing: linear / ease-in / ease-out / ease-in-out for the transition INTO
  the keyframe.
* Play: walks the timeline once, interpolating bone rotations with the chosen
  easing curve. Stop returns to the originally-captured pose.
"""

import sys
import json
import math
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
                             QGraphicsItem, QGraphicsObject, QGraphicsPixmapItem, QVBoxLayout,
                             QHBoxLayout, QWidget, QPushButton, QTreeWidget, QTreeWidgetItem,
                             QInputDialog, QFileDialog, QRadioButton, QButtonGroup, QLabel,
                             QSplitter, QCheckBox, QSpinBox, QGroupBox, QDoubleSpinBox, QMessageBox,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                             QComboBox, QListWidget, QListWidgetItem)
from PyQt5.QtCore import Qt, QPointF, QRectF, QTimer
from PyQt5.QtGui import QPixmap, QPen, QPainter, QColor, QTransform


# --- DARK THEME (kept consistent with model_editor.py) ---
DARK_THEME = """
QWidget, QMainWindow, QDialog, QMessageBox { background-color: #2b2b2b; color: #e0e0e0; font-family: 'Segoe UI', Arial, sans-serif; }
QLabel, QCheckBox, QRadioButton { color: #e0e0e0; background: transparent; }
QPushButton { background-color: #3d3d3d; color: #e0e0e0; border: 1px solid #555; padding: 6px; border-radius: 4px; }
QPushButton:hover { background-color: #4d4d4d; border: 1px solid #777; }
QPushButton:pressed { background-color: #5d5d5d; }
QTreeWidget, QListView, QListWidget, QTableView, QTableWidget, QAbstractItemView { background-color: #1e1e1e; alternate-background-color: #242424; color: #e0e0e0; border: 1px solid #444; border-radius: 4px; }
QTreeWidget::item:selected, QListView::item:selected, QListWidget::item:selected, QTableWidget::item:selected { background-color: #2d5a88; color: white; }
QGraphicsView { border: none; background-color: #1a1a1a; }
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit { background-color: #1e1e1e; color: #e0e0e0; border: 1px solid #555; padding: 4px; border-radius: 3px; }
QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 10px; padding-top: 15px; color: #e0e0e0; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; color: #e0e0e0; }
QSplitter::handle { background-color: #444; }
QHeaderView::section { background-color: #3d3d3d; color: #e0e0e0; border: 1px solid #555; padding: 4px; }
"""

# Available easing curves. These are the curves applied to the transition
# INTO the keyframe (i.e. the time between keyframe N-1 and keyframe N).
EASING_TYPES = ["linear", "ease-in", "ease-out", "ease-in-out"]


def apply_easing(t, easing):
    """Map a normalized progress value t in [0,1] through the chosen easing curve.

    All curves are well-known animation conventions and clamp t to [0,1] so
    callers don't have to. math is done in normalized space so it's reusable
    for any value type (here: bone rotations in degrees).
    """
    t = max(0.0, min(1.0, t))
    if easing == "linear":
        return t
    if easing == "ease-in":
        return t * t
    if easing == "ease-out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    if easing == "ease-in-out":
        # classic smoothstep
        return t * t * (3.0 - 2.0 * t)
    return t


# ============================================================================
# CANVAS / BONE STACK
# ============================================================================
# These three classes (CanvasView, RigScene, BoneNode) are intentionally kept
# as close to model_editor.py as possible so the two tools stay visually and
# behaviorally consistent. Only minor adjustments are made where animation
# playback needs to inject poses without disturbing the user's edits.

class CanvasView(QGraphicsView):
    """Same pan / zoom behaviour as model_editor.py."""

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.panning = False
        self.pan_start = None

    def wheelEvent(self, event):
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor
        if event.angleDelta().y() > 0:
            self.scale(zoom_in_factor, zoom_in_factor)
        else:
            self.scale(zoom_out_factor, zoom_out_factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.panning = True
            self.pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.panning:
            delta = event.pos() - self.pan_start
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.pan_start = event.pos()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class RigScene(QGraphicsScene):
    """Same drag handlers as model_editor.py so you can pose the rig in POSE
    mode just like in the model editor. The editor reference is used to read
    the currently selected bone and the current manipulation mode."""

    def __init__(self, editor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        self.manipulating = False
        self.manip_node = None
        self.scene_drag_start = QPointF()
        self.local_pos_start = QPointF()
        self.image_offset_start = QPointF()
        self.node_start_rot = 0.0
        self.angle_offset = 0.0

    def drawForeground(self, painter, rect):
        painter.setPen(QPen(QColor(100, 255, 100, 180), 2, Qt.DashLine))
        for item in self.items():
            if isinstance(item, BoneNode):
                parent = item.parentItem()
                if parent and isinstance(parent, BoneNode):
                    painter.drawLine(item.scenePos(), parent.scenePos())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.manip_node = self.editor.get_selected_node()
            if self.manip_node:
                self.manipulating = True
                self.scene_drag_start = event.scenePos()
                self.local_pos_start = self.manip_node.pos()
                self.image_offset_start = QPointF(self.manip_node.image_offset)

                mode = self.editor.current_mode
                if mode == "POSE":
                    n_pos = self.manip_node.scenePos()
                    m_pos = event.scenePos()
                    self.angle_offset = math.atan2(m_pos.y() - n_pos.y(), m_pos.x() - n_pos.x())
                    self.node_start_rot = self.manip_node.rotation()

                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.manipulating and self.manip_node:
            mode = self.editor.current_mode
            if mode == "MOVE_JOINT":
                parent = self.manip_node.parentItem()
                if parent:
                    p_start = parent.mapFromScene(self.scene_drag_start)
                    p_end = parent.mapFromScene(event.scenePos())
                    self.manip_node.setPos(self.local_pos_start + (p_end - p_start))
                else:
                    delta = event.scenePos() - self.scene_drag_start
                    self.manip_node.setPos(self.local_pos_start + delta)

            elif mode == "MOVE_IMAGE":
                local_start = self.manip_node.mapFromScene(self.scene_drag_start)
                local_end = self.manip_node.mapFromScene(event.scenePos())
                new_offset = self.image_offset_start + (local_end - local_start)
                self.manip_node.set_image_offset(new_offset)

            elif mode == "POSE":
                n_pos = self.manip_node.scenePos()
                m_pos = event.scenePos()
                current_angle = math.atan2(m_pos.y() - n_pos.y(), m_pos.x() - n_pos.x())
                diff_deg = math.degrees(current_angle - self.angle_offset)
                limit = self.manip_node.rotation_limit
                rotation = max(-limit, min(limit, self.node_start_rot + diff_deg))
                self.manip_node.setRotation(rotation)

            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.manipulating:
            self.manipulating = False
            self.manip_node = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class BoneNode(QGraphicsObject):
    """Identical behaviour to model_editor.BoneNode. See the long docstring there
    for the rationale on detached pixmap items + global image Z-order."""

    def __init__(self, name, editor):
        super().__init__()
        self.name = name
        self.editor = editor

        self.image_paths = []
        self.expression_index = 0
        self.flip_h = False
        self.flip_v = False
        self.image_rotation = 0.0
        self.image_offset = QPointF(0.0, 0.0)
        self.image_z = 0
        self.rotation_limit = 360.0

        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(0)

        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setZValue(self.image_z)

    def boundingRect(self):
        rect = QRectF(-10, -10, 20, 20)
        if not self.pixmap_item.pixmap().isNull():
            img_rect = self.mapRectFromItem(self.pixmap_item, self.pixmap_item.boundingRect())
            rect = rect.united(img_rect)
        return rect

    def paint(self, painter, option, widget):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 80, 80) if self.isSelected() else QColor(80, 180, 255))
        painter.drawEllipse(-8, -8, 16, 16)

    def itemChange(self, change, value):
        if change in (QGraphicsItem.ItemPositionChange, QGraphicsItem.ItemRotationChange):
            if self.scene():
                self.scene().update()
                if self.isSelected():
                    self.editor.update_info_hud()

        if change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemRotationHasChanged,
                      QGraphicsItem.ItemTransformHasChanged, QGraphicsItem.ItemParentHasChanged):
            self.sync_subtree_pixmaps()

        return super().itemChange(change, value)

    def add_image(self, path):
        self.image_paths.append(path)
        if len(self.image_paths) == 1:
            self.set_expression(0)

    def set_expression(self, index):
        if 0 <= index < len(self.image_paths):
            self.expression_index = index
            pixmap = QPixmap(self.image_paths[index])
            self.pixmap_item.setPixmap(pixmap)

            if index == 0 and len(self.image_paths) == 1:
                self.image_offset = QPointF(-pixmap.width() / 2.0, -pixmap.height() / 2.0)

            self.sync_pixmap()

    def set_flips(self, h, v):
        self.flip_h = h
        self.flip_v = v
        self.sync_pixmap()

    def set_image_rotation(self, deg):
        self.image_rotation = deg
        self.sync_pixmap()

    def set_image_offset(self, offset):
        self.image_offset = QPointF(offset)
        self.sync_pixmap()

    def set_image_z(self, z):
        self.image_z = z
        self.pixmap_item.setZValue(z)

    def set_rotation_limit(self, limit):
        self.rotation_limit = max(0.0, abs(float(limit)))
        self.setRotation(max(-self.rotation_limit, min(self.rotation_limit, self.rotation())))

    def sync_pixmap(self):
        self.prepareGeometryChange()
        if self.pixmap_item.pixmap().isNull():
            return

        w = self.pixmap_item.pixmap().width()
        h = self.pixmap_item.pixmap().height()
        cx, cy = w / 2.0, h / 2.0

        local = QTransform()
        local.translate(self.image_offset.x(), self.image_offset.y())
        local.translate(cx, cy)
        local.rotate(self.image_rotation)
        sx = -1 if self.flip_h else 1
        sy = -1 if self.flip_v else 1
        local.scale(sx, sy)
        local.translate(-cx, -cy)

        self.pixmap_item.setPos(0, 0)
        self.pixmap_item.setTransform(local * self.sceneTransform())

    def sync_subtree_pixmaps(self):
        self.sync_pixmap()
        for child in self.childItems():
            if isinstance(child, BoneNode):
                child.sync_subtree_pixmaps()


# ============================================================================
# ANIMATION DATA MODEL
# ============================================================================

class Keyframe:
    """A single keyframe in the timeline.

    Stores the rotation snapshot for every bone at this moment in the
    animation. The interval is the time taken to TRAVEL INTO this keyframe
    from the previous one (in milliseconds). The easing curve describes the
    shape of that transition.
    """

    def __init__(self, interval_ms=100, easing="linear", rotations=None):
        self.interval_ms = float(interval_ms)
        self.easing = easing if easing in EASING_TYPES else "linear"
        # rotations: dict {bone_name: rotation_degrees}
        self.rotations = dict(rotations) if rotations else {}

    def to_dict(self):
        return {
            "interval_ms": round(self.interval_ms, 2),
            "easing": self.easing,
            "rotations": {name: round(value, 2) for name, value in self.rotations.items()}
        }

    @staticmethod
    def from_dict(d):
        if not isinstance(d, dict):
            return Keyframe()
        return Keyframe(
            interval_ms=d.get("interval_ms", 100),
            easing=d.get("easing", "linear"),
            rotations=d.get("rotations", {})
        )


# ============================================================================
# TIMELINE WIDGET
# ============================================================================

class TimelineWidget(QWidget):
    """The bottom-of-screen animation timeline.

    Each row is one keyframe and shows: index, time (ms), easing, and a
    delete button. The list is the single source of truth for the order of
    keyframes; changing it reorders the underlying data.
    """

    keyframeSelectionChanged = None  # set by AnimationEditor
    keyframeRecaptureRequested = None  # set by AnimationEditor (right-click "Recapture")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyframes = []  # list[Keyframe]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Animation Timeline")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        # Header row
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel(""), 0)            # checkbox column
        header_layout.addWidget(QLabel("#"), 0)
        header_layout.addWidget(QLabel("Interval (ms)"), 1)
        header_layout.addWidget(QLabel("Easing"), 1)
        header_layout.addWidget(QLabel("Bones Captured"), 2)
        header_layout.addWidget(QLabel(""), 0)            # delete button column
        layout.addLayout(header_layout)

        # Scrollable keyframe list.
        # We deliberately turn OFF QListWidget's row-selection mechanism --
        # selection is driven by the per-row checkbox below, not by clicking
        # on the row. This avoids the situation where dragging the easing
        # combo / interval spin accidentally selects the row and changes the
        # capture target.
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.NoSelection)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        # Right-click context menu for explicit per-row actions (e.g. recapture)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)

        # Index of the currently "selected" (= checkbox-checked) row, or -1
        # if none. This is the single source of truth for "which keyframe
        # will Capture overwrite?". Only one row can be checked at a time.
        self._checked_index = -1

        layout.addWidget(self.list_widget)

        # Footer: total time + capture/delete buttons
        footer_layout = QHBoxLayout()
        self.total_time_label = QLabel("Total: 0 ms")
        self.total_time_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        footer_layout.addWidget(self.total_time_label)
        footer_layout.addStretch()

        layout.addLayout(footer_layout)

    # ---------- public API used by AnimationEditor ----------

    def rebuild(self, keyframes):
        """Replace the entire keyframe list with new data."""
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.keyframes = list(keyframes)
        self._checked_index = -1
        for i, kf in enumerate(self.keyframes):
            self._add_row_widget(i, kf)
        self.list_widget.blockSignals(False)
        self._refresh_total_time()

    def add_keyframe(self, kf, select=False):
        """Append a keyframe to the timeline.

        The default is select=False so consecutive Capture presses keep
        appending new keyframes -- the just-captured row stays unchecked,
        so the next Capture doesn't overwrite it.
        """
        self.keyframes.append(kf)
        idx = len(self.keyframes) - 1
        self._add_row_widget(idx, kf)
        if select:
            self._set_checked(idx)
        self._refresh_total_time()

    def deselect(self):
        """Clear the current checkbox selection (used after a non-overwriting capture)."""
        self._set_checked(-1)

    def update_keyframe(self, idx, kf):
        """Replace the keyframe at idx (used for re-capture and edits)."""
        if 0 <= idx < len(self.keyframes):
            self.keyframes[idx] = kf
            self._refresh_row_widget(idx)
            self._refresh_total_time()

    def remove_keyframe(self, idx):
        if not (0 <= idx < len(self.keyframes)):
            return
        del self.keyframes[idx]
        self.list_widget.takeItem(idx)
        # Row indices in item widgets no longer match self.keyframes, so
        # re-bind every row to the correct index.
        for i in range(self.list_widget.count()):
            self._bind_row_widget(i)
        # Fix up the checked-index if it pointed at or past the removed row.
        if self._checked_index == idx:
            self._checked_index = -1
        elif self._checked_index > idx:
            self._checked_index -= 1
        self._refresh_total_time()

    def selected_index(self):
        """Return the index of the currently-checked row, or -1 if none."""
        return self._checked_index

    def clear(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.blockSignals(False)
        self.keyframes.clear()
        self._checked_index = -1
        self._refresh_total_time()

    def total_duration_ms(self):
        return sum(max(0.0, kf.interval_ms) for kf in self.keyframes)

    # ---------- internal helpers ----------

    def _set_checked(self, idx):
        """Programmatically check exactly one row (or uncheck all if idx==-1).

        Keeps the checkbox widget, the cached index, and the optional
        keyframeSelectionChanged callback in sync. Used by the checkbox
        toggle, by add_keyframe(select=True), and by deselect()."""
        self._checked_index = idx
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            cb = getattr(item, "_select_check", None)
            if cb is None:
                continue
            want = (i == idx)
            if cb.isChecked() != want:
                cb.blockSignals(True)
                cb.setChecked(want)
                cb.blockSignals(False)
        if self.keyframeSelectionChanged is not None:
            self.keyframeSelectionChanged(idx)

    def _on_checkbox_toggled(self, row, checked):
        """Single-row checkbox handler. Enforces mutual exclusion and the
        'click the same one again to unselect' rule."""
        if checked:
            # Checking one row means all others must uncheck.
            self._set_checked(row)
        else:
            # Unchecking. If this was the active row, drop selection
            # entirely. If it wasn't the active row, force it back on
            # (the user might have clicked a different box that we just
            # unchecked programmatically -- this guarantees UI state
            # matches the model).
            if row == self._checked_index:
                self._set_checked(-1)
            else:
                # Re-check the real selected row, or leave nothing checked.
                self._set_checked(self._checked_index)

    def _show_context_menu(self, pos):
        """Right-click on a row: offer 'Recapture this keyframe' etc."""
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        row = self.list_widget.row(item)
        # We don't auto-check the row from a right-click (the checkbox is
        # the explicit "I want to overwrite" gesture), but the recapture
        # action below works on the targeted row regardless of selection.

        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self.list_widget)
        recapture_action = menu.addAction("Recapture this keyframe")
        recapture_action.triggered.connect(
            lambda checked=False, r=row: self._on_recapture_requested(r)
        )
        delete_action = menu.addAction("Delete this keyframe")
        delete_action.triggered.connect(
            lambda checked=False, r=row: self.remove_keyframe(r)
        )
        menu.exec_(self.list_widget.viewport().mapToGlobal(pos))

    def _on_recapture_requested(self, row):
        if self.keyframeRecaptureRequested is not None:
            self.keyframeRecaptureRequested(row)

    def _add_row_widget(self, idx, kf):
        """Build a custom widget for one row and stash it on the QListWidgetItem."""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(4, 2, 4, 2)

        # Per-row checkbox: ticking it marks this row as the one Capture
        # Keyframe will overwrite. Ticking it again (or ticking a different
        # row) clears this one.
        select_check = QCheckBox()
        select_check.setToolTip(
            "Check this row to mark it as the capture-overwrite target.\n"
            "Capture Keyframe will update the rotations of the checked row "
            "instead of appending a new one."
        )
        select_check.setChecked(idx == self._checked_index)
        select_check.toggled.connect(
            lambda checked, i=idx: self._on_checkbox_toggled(i, checked)
        )
        row_layout.addWidget(select_check, 0)

        idx_label = QLabel(str(idx + 1))
        idx_label.setMinimumWidth(24)
        row_layout.addWidget(idx_label, 0)

        interval_spin = QDoubleSpinBox()
        interval_spin.setRange(0.0, 600000.0)
        interval_spin.setSuffix(" ms")
        interval_spin.setDecimals(1)
        interval_spin.setValue(kf.interval_ms)
        interval_spin.valueChanged.connect(
            lambda val, i=idx: self._on_interval_changed(i, val)
        )
        row_layout.addWidget(interval_spin, 1)

        easing_combo = QComboBox()
        easing_combo.addItems(EASING_TYPES)
        easing_combo.setCurrentText(kf.easing)
        easing_combo.currentTextChanged.connect(
            lambda text, i=idx: self._on_easing_changed(i, text)
        )
        row_layout.addWidget(easing_combo, 1)

        bones_label = QLabel(self._bones_summary(kf))
        bones_label.setStyleSheet("color: #b0b0b0;")
        bones_label.setWordWrap(True)
        row_layout.addWidget(bones_label, 2)
        kf._bones_label_ref = bones_label  # used to refresh in place

        del_btn = QPushButton("X")
        del_btn.setFixedWidth(32)
        del_btn.setStyleSheet(
            "QPushButton { background-color: #8b3a3a; border: 1px solid #5a2222; "
            "padding: 2px; } QPushButton:hover { background-color: #a84747; }"
        )
        del_btn.clicked.connect(lambda checked=False, i=idx: self._on_delete_clicked(i))
        row_layout.addWidget(del_btn, 0)

        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(row_widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, row_widget)

        # Stash the editable widgets so _refresh_row_widget can sync them
        item._select_check = select_check
        item._interval_spin = interval_spin
        item._easing_combo = easing_combo
        item._idx_label = idx_label

    def _bind_row_widget(self, row_idx):
        """Re-stash refs after a delete (rows in QListWidget shift)."""
        item = self.list_widget.item(row_idx)
        if not item:
            return
        item._idx_label.setText(str(row_idx + 1))

    def _refresh_row_widget(self, idx):
        item = self.list_widget.item(idx)
        if not item:
            return
        kf = self.keyframes[idx]
        item._idx_label.setText(str(idx + 1))
        block_i = item._interval_spin.blockSignals(True)
        item._interval_spin.setValue(kf.interval_ms)
        item._interval_spin.blockSignals(block_i)
        block_e = item._easing_combo.blockSignals(True)
        item._easing_combo.setCurrentText(kf.easing)
        item._easing_combo.blockSignals(block_e)
        if hasattr(kf, "_bones_label_ref") and kf._bones_label_ref is not None:
            kf._bones_label_ref.setText(self._bones_summary(kf))

    def _bones_summary(self, kf):
        n = len(kf.rotations)
        return f"{n} bone(s)"

    def _refresh_total_time(self):
        total = self.total_duration_ms()
        self.total_time_label.setText(f"Total: {total:.1f} ms ({total / 1000.0:.2f} s)")

    def _on_interval_changed(self, idx, val):
        if 0 <= idx < len(self.keyframes):
            self.keyframes[idx].interval_ms = float(val)
            self._refresh_total_time()

    def _on_easing_changed(self, idx, text):
        if 0 <= idx < len(self.keyframes):
            self.keyframes[idx].easing = text if text in EASING_TYPES else "linear"

    def _on_delete_clicked(self, idx):
        self.remove_keyframe(idx)


# ============================================================================
# MAIN EDITOR WINDOW
# ============================================================================

class AnimationEditor(QMainWindow):
    """The top-level animation editor window.

    Layout:
        Left  panel: tools + skeleton tree + bone properties
        Center panel: canvas (same QGraphicsView as model_editor)
        Bottom panel: animation timeline (always visible)
    """

    DEFAULT_INTERVAL_MS = 100.0
    DEFAULT_EASING = "linear"
    PLAYBACK_TIMER_MS = 16  # ~60 fps

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PNG Puppet Animation Editor")
        self.resize(1500, 1000)
        self.setStyleSheet(DARK_THEME)

        self.nodes = {}
        self.tree_items = {}
        self.current_mode = "POSE"  # default to POSE in animation editor -- you almost
                                    # always want to rotate joints here.

        # Path of the rig JSON the user loaded. We remember this so when we
        # save an animation we can record which rig it animates.
        self.rig_source_path = None
        # Optional explicit rig name (could come from the rig JSON or be set
        # by the user); used to identify which rig an animation belongs to.
        self.rig_name = None

        # Original rotations snapshot. When playback finishes (or is stopped)
        # we restore the rig to exactly the pose the user last left it in,
        # so editing after a play feels seamless.
        self.original_rotations = {}

        # Path of the animation JSON the user currently has open (if any).
        # Lets "Save" behave like an overwrite of the same file.
        self.animation_source_path = None

        # Playback state
        self._playing = False
        self._play_start_time_ms = 0
        self._play_index = 0  # which transition we're currently in
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._tick_playback)

        self.init_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Horizontal splitter holds the left panel + canvas
        self.main_splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(self.main_splitter, 1)

        # ---- Left panel: tools, hierarchy, properties ----
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)

        # Modes
        mode_groupbox = QGroupBox("Tools")
        mode_layout = QVBoxLayout()
        self.mode_group = QButtonGroup(self)

        btn_mode_pose = QRadioButton("Pose (Rotate Joint FK)")
        btn_mode_pose.setChecked(True)
        btn_mode_pose.clicked.connect(lambda: self.set_mode("POSE"))

        btn_mode_joint = QRadioButton("Move Joint (Assembly)")
        btn_mode_joint.clicked.connect(lambda: self.set_mode("MOVE_JOINT"))

        btn_mode_img = QRadioButton("Move Image Offset (Pivot)")
        btn_mode_img.clicked.connect(lambda: self.set_mode("MOVE_IMAGE"))

        self.mode_group.addButton(btn_mode_pose)
        self.mode_group.addButton(btn_mode_joint)
        self.mode_group.addButton(btn_mode_img)

        mode_layout.addWidget(btn_mode_pose)
        mode_layout.addWidget(btn_mode_joint)
        mode_layout.addWidget(btn_mode_img)
        mode_groupbox.setLayout(mode_layout)
        left_layout.addWidget(mode_groupbox)

        # Hierarchy
        hier_groupbox = QGroupBox("Skeleton Hierarchy")
        hier_layout = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Bones"])
        self.tree.itemSelectionChanged.connect(self.on_tree_selection)
        hier_layout.addWidget(self.tree)
        hier_groupbox.setLayout(hier_layout)
        left_layout.addWidget(hier_groupbox)

        # Bone properties (mirror of model_editor's per-bone block)
        prop_groupbox = QGroupBox("Selected Bone Properties")
        prop_layout = QVBoxLayout()

        btn_img = QPushButton("Add PNG / Expression")
        btn_img.clicked.connect(self.assign_image)
        prop_layout.addWidget(btn_img)

        expr_layout = QHBoxLayout()
        expr_layout.addWidget(QLabel("Expression Index:"))
        self.expr_spin = QSpinBox()
        self.expr_spin.setMinimum(0)
        self.expr_spin.setMaximum(0)
        self.expr_spin.valueChanged.connect(self.on_property_changed)
        expr_layout.addWidget(self.expr_spin)
        prop_layout.addLayout(expr_layout)

        flip_layout = QHBoxLayout()
        self.flip_h_cb = QCheckBox("Flip Horizontal")
        self.flip_v_cb = QCheckBox("Flip Vertical")
        self.flip_h_cb.stateChanged.connect(self.on_property_changed)
        self.flip_v_cb.stateChanged.connect(self.on_property_changed)
        flip_layout.addWidget(self.flip_h_cb)
        flip_layout.addWidget(self.flip_v_cb)
        prop_layout.addLayout(flip_layout)

        img_rot_layout = QHBoxLayout()
        img_rot_layout.addWidget(QLabel("Image Base Rotation (Deg):"))
        self.img_rot_spin = QDoubleSpinBox()
        self.img_rot_spin.setRange(-360.0, 360.0)
        self.img_rot_spin.setSingleStep(1.0)
        self.img_rot_spin.valueChanged.connect(self.on_property_changed)
        img_rot_layout.addWidget(self.img_rot_spin)
        prop_layout.addLayout(img_rot_layout)

        z_layout = QHBoxLayout()
        btn_z_up = QPushButton("Bring Forward (Z+)")
        btn_z_up.clicked.connect(lambda: self.adjust_z(1))
        btn_z_down = QPushButton("Send Backward (Z-)")
        btn_z_down.clicked.connect(lambda: self.adjust_z(-1))
        z_layout.addWidget(btn_z_down)
        z_layout.addWidget(btn_z_up)
        prop_layout.addLayout(z_layout)

        prop_groupbox.setLayout(prop_layout)
        left_layout.addWidget(prop_groupbox)

        # Bottom of left panel: rig-wide actions
        # NOTE: rig files are READ-ONLY in this editor. The editor only
        # writes animation files. The left panel therefore only has the rig
        # import button here; animation save/load live in the bottom toolbar
        # so they're near the timeline they belong to.
        left_layout.addStretch()
        btn_load_rig = QPushButton("Load Rig JSON")
        btn_load_rig.setStyleSheet(
            "font-weight: bold; padding: 10px; background-color: #0277bd; "
            "border: 1px solid #01579b;"
        )
        btn_load_rig.clicked.connect(self.import_json)
        left_layout.addWidget(btn_load_rig)

        # ---- Center: canvas ----
        self.scene = RigScene(self)
        self.scene.selectionChanged.connect(self.on_scene_selection)
        self.view = CanvasView(self.scene)

        self.hud_label = QLabel(self.view)
        self.hud_label.setStyleSheet(
            "color: #4CAF50; font-size: 14px; font-weight: bold; "
            "background-color: rgba(0, 0, 0, 180); padding: 8px; border-radius: 5px;"
        )
        self.hud_label.move(10, 10)
        self.hud_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hud_label.hide()

        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(self.view)
        self.main_splitter.setSizes([340, 1160])

        # ---- Bottom: animation timeline ----
        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(8, 8, 8, 8)
        bottom_layout.setSpacing(6)

        # Toolbar above the timeline
        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("Default Interval (ms):"))
        self.default_interval_spin = QDoubleSpinBox()
        self.default_interval_spin.setRange(0.0, 600000.0)
        self.default_interval_spin.setValue(self.DEFAULT_INTERVAL_MS)
        self.default_interval_spin.setDecimals(1)
        toolbar.addWidget(self.default_interval_spin)

        toolbar.addWidget(QLabel("Default Easing:"))
        self.default_easing_combo = QComboBox()
        self.default_easing_combo.addItems(EASING_TYPES)
        self.default_easing_combo.setCurrentText(self.DEFAULT_EASING)
        toolbar.addWidget(self.default_easing_combo)

        toolbar.addStretch()

        btn_capture = QPushButton("Capture Keyframe")
        btn_capture.setStyleSheet(
            "font-weight: bold; padding: 6px 14px; background-color: #0277bd; "
            "border: 1px solid #01579b;"
        )
        btn_capture.setToolTip(
            "Snapshot every bone's current rotation into the timeline.\n"
            "If a keyframe is selected, it will be UPDATED instead of appending."
        )
        btn_capture.clicked.connect(self.capture_keyframe)
        toolbar.addWidget(btn_capture)

        self.play_button = QPushButton("Play")
        self.play_button.setStyleSheet(
            "font-weight: bold; padding: 6px 14px; background-color: #2e7d32; "
            "border: 1px solid #1b5e20;"
        )
        self.play_button.clicked.connect(self.toggle_playback)
        toolbar.addWidget(self.play_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setStyleSheet(
            "font-weight: bold; padding: 6px 14px; background-color: #8b3a3a; "
            "border: 1px solid #5a2222;"
        )
        self.stop_button.clicked.connect(self.stop_playback)
        toolbar.addWidget(self.stop_button)

        btn_clear_anim = QPushButton("Clear Animation")
        btn_clear_anim.setToolTip(
            "Remove all keyframes from the timeline. The rig on the canvas "
            "is not affected."
        )
        btn_clear_anim.clicked.connect(self.clear_animation)
        toolbar.addWidget(btn_clear_anim)

        toolbar.addStretch()

        btn_load_anim = QPushButton("Load Animation JSON")
        btn_load_anim.setToolTip(
            "Load an animation JSON file (separate from the rig JSON). "
            "If the rig referenced inside doesn't match the currently loaded "
            "rig, bone names that don't exist will be ignored."
        )
        btn_load_anim.clicked.connect(self.import_animation_json)
        toolbar.addWidget(btn_load_anim)

        btn_save_anim = QPushButton("Save Animation JSON")
        btn_save_anim.setStyleSheet(
            "font-weight: bold; padding: 6px 14px; background-color: #2e7d32; "
            "border: 1px solid #1b5e20;"
        )
        btn_save_anim.setToolTip(
            "Save the current keyframes to a NEW animation JSON file. "
            "Only rotation data is written -- the rig file is never modified."
        )
        btn_save_anim.clicked.connect(self.export_animation_json)
        toolbar.addWidget(btn_save_anim)

        bottom_layout.addLayout(toolbar)

        # Timeline itself
        self.timeline = TimelineWidget()
        self.timeline.keyframeSelectionChanged = self._on_timeline_selection
        self.timeline.keyframeRecaptureRequested = self._recapture_keyframe_at
        bottom_layout.addWidget(self.timeline)

        outer.addWidget(bottom_panel, 0)

        self.statusBar().showMessage(
            "Open a rig JSON file to get started. "
            "Rig files are read-only; animations are saved to a separate file."
        )

    # ------------------------------------------------------------------
    # Selection / mode helpers
    # ------------------------------------------------------------------
    def set_mode(self, mode):
        self.current_mode = mode

    def update_info_hud(self):
        node = self.get_selected_node()
        if node:
            text = (
                f"Bone: {node.name}  |  Local Pos: ({node.pos().x():.1f}, {node.pos().y():.1f})  "
                f"|  Rot: {node.rotation():.1f}°  |  Img Z: {node.image_z}"
            )
            self.hud_label.setText(text)
            self.hud_label.adjustSize()
            self.hud_label.show()
        else:
            self.hud_label.hide()

    def get_selected_node(self):
        selected = self.scene.selectedItems()
        if selected and isinstance(selected[0], BoneNode):
            return selected[0]
        return None

    def on_tree_selection(self):
        selected = self.tree.selectedItems()
        if selected:
            name = selected[0].text(0)
            node = self.nodes.get(name)
            if node:
                self.scene.blockSignals(True)
                self.scene.clearSelection()
                node.setSelected(True)
                self.sync_properties_ui()
                self.update_info_hud()
                self.scene.blockSignals(False)
                self.scene.update()

    def on_scene_selection(self):
        node = self.get_selected_node()
        if node:
            tree_item = self.tree_items.get(node.name)
            if tree_item:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(tree_item)
                self.tree.blockSignals(False)
        else:
            self.tree.blockSignals(True)
            self.tree.clearSelection()
            self.tree.blockSignals(False)

        self.sync_properties_ui()
        self.update_info_hud()

    def sync_properties_ui(self):
        node = self.get_selected_node()

        self.expr_spin.blockSignals(True)
        self.flip_h_cb.blockSignals(True)
        self.flip_v_cb.blockSignals(True)
        self.img_rot_spin.blockSignals(True)

        if node:
            self.expr_spin.setEnabled(True)
            self.flip_h_cb.setEnabled(True)
            self.flip_v_cb.setEnabled(True)
            self.img_rot_spin.setEnabled(True)

            max_idx = max(0, len(node.image_paths) - 1)
            self.expr_spin.setMaximum(max_idx)
            self.expr_spin.setValue(node.expression_index)
            self.flip_h_cb.setChecked(node.flip_h)
            self.flip_v_cb.setChecked(node.flip_v)
            self.img_rot_spin.setValue(node.image_rotation)
        else:
            self.expr_spin.setEnabled(False)
            self.flip_h_cb.setEnabled(False)
            self.flip_v_cb.setEnabled(False)
            self.img_rot_spin.setEnabled(False)

            self.expr_spin.setValue(0)
            self.flip_h_cb.setChecked(False)
            self.flip_v_cb.setChecked(False)
            self.img_rot_spin.setValue(0)

        self.expr_spin.blockSignals(False)
        self.flip_h_cb.blockSignals(False)
        self.flip_v_cb.blockSignals(False)
        self.img_rot_spin.blockSignals(False)

    def on_property_changed(self):
        node = self.get_selected_node()
        if node:
            node.set_expression(self.expr_spin.value())
            node.set_flips(self.flip_h_cb.isChecked(), self.flip_v_cb.isChecked())
            node.set_image_rotation(self.img_rot_spin.value())

    def assign_image(self):
        selected = self.get_selected_node()
        if not selected:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PNG(s)", "", "Images (*.png)")
        if paths:
            for path in paths:
                selected.add_image(path)
            self.sync_properties_ui()

    def adjust_z(self, delta):
        selected = self.get_selected_node()
        if selected:
            selected.set_image_z(selected.image_z + delta)
            self.scene.update()

    # ------------------------------------------------------------------
    # Rig clear / load / save
    # ------------------------------------------------------------------
    def clear_rig(self):
        """Reset the rig (bones + scene). Does NOT touch the animation timeline
        so loading a new rig over an existing animation doesn't lose work."""
        self.stop_playback(restore=True)
        self.scene.clearSelection()
        self.scene.clear()
        self.nodes.clear()
        self.tree_items.clear()
        self.tree.clear()
        self.original_rotations.clear()
        self.rig_source_path = None
        self.rig_name = None
        self.update_info_hud()

    def clear_animation(self):
        """Wipe the timeline (keyframes + the animation file path). The rig
        stays intact."""
        if not self.timeline.keyframes:
            return
        reply = QMessageBox.question(
            self,
            "Clear animation?",
            f"Remove all {len(self.timeline.keyframes)} keyframes from the timeline?\n"
            "This cannot be undone (use Save Animation JSON first if needed).",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.stop_playback(restore=True)
        self.timeline.clear()
        self.animation_source_path = None
        self.statusBar().showMessage("Animation cleared.")

    def import_json(self):
        """Load a RIG JSON file (the file produced by model_editor.py).

        Rig files are read-only here -- nothing about the rig gets written
        back. If the user wants to keep their current timeline, they can
        cancel the rig-clear prompt below."""
        path, _ = QFileDialog.getOpenFileName(self, "Load Rig JSON", "", "JSON Files (*.json)")
        if not path:
            return

        # If there are already keyframes in the timeline, ask the user before
        # clearing the rig, since switching rigs typically means the existing
        # animation no longer applies. They can choose to keep it anyway.
        if self.timeline.keyframes:
            reply = QMessageBox.question(
                self,
                "Replace rig?",
                "Loading a new rig will clear the current rig on the canvas.\n"
                "Your existing keyframes will be kept in the timeline -- they "
                "just won't apply to any bone that doesn't exist in the new rig.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return

        with open(path, 'r') as f:
            try:
                data = json.load(f)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load JSON:\n{e}")
                return

        # Reset the rig (keeps the animation timeline intact on purpose).
        self.scene.clearSelection()
        self.scene.clear()
        self.nodes.clear()
        self.tree_items.clear()
        self.tree.clear()
        self.original_rotations.clear()

        bones_data = data.get("bones", {})

        # Step 1: create all nodes + detached pixmaps
        for name, b_data in bones_data.items():
            node = BoneNode(name, self)
            self.nodes[name] = node
            self.scene.addItem(node)
            self.scene.addItem(node.pixmap_item)
            tree_item = QTreeWidgetItem([name])
            self.tree_items[name] = tree_item

        # Step 2: hierarchy + properties
        for name, b_data in bones_data.items():
            node = self.nodes[name]
            parent_name = b_data.get("parent")

            if parent_name and parent_name in self.nodes:
                node.setParentItem(self.nodes[parent_name])
                self.tree_items[parent_name].addChild(self.tree_items[name])
            else:
                self.tree.addTopLevelItem(self.tree_items[name])

            node.setPos(b_data.get("local_x", 0), b_data.get("local_y", 0))
            node.set_rotation_limit(b_data.get("rotation_limit", 360.0))
            # The RIG file may carry a default rotation (the model's "rest"
            # pose). We respect it but DO NOT treat it as part of any
            # animation; rotations are only ever written to animation files.
            node.setRotation(
                max(-node.rotation_limit, min(node.rotation_limit, b_data.get("rotation", 0)))
            )
            node.set_image_z(b_data.get("z_value", 0))

            for p in b_data.get("image_paths", []):
                if os.path.exists(p):
                    node.add_image(p)
                else:
                    print(f"Warning: Image path not found: {p}")

            node.expression_index = b_data.get("expression_index", 0)
            node.flip_h = b_data.get("flip_h", False)
            node.flip_v = b_data.get("flip_v", False)
            node.image_rotation = b_data.get("image_rotation", 0.0)

            if node.image_paths:
                node.set_expression(node.expression_index)

            saved_offset = QPointF(
                b_data.get("image_offset_x", 0), b_data.get("image_offset_y", 0)
            )
            node.set_image_offset(saved_offset)

            node.sync_pixmap()

        # Final resync pass (matches model_editor behavior)
        for node in self.nodes.values():
            node.sync_pixmap()

        # Remember which rig this animation is targeting.
        self.rig_source_path = path
        # Prefer an explicit "name" field on the rig if model_editor ever adds
        # one; otherwise fall back to the file's basename.
        self.rig_name = (
            data.get("name") if isinstance(data.get("name"), str) else os.path.splitext(os.path.basename(path))[0]
        )

        # If the rig file happened to contain an old "animation" block (e.g.
        # because someone used an earlier version of this editor), load it
        # transparently -- but only if the user doesn't already have a
        # timeline. Otherwise the existing keyframes win.
        if not self.timeline.keyframes:
            anim_data = data.get("animation", {})
            keyframes_data = anim_data.get("keyframes", []) if isinstance(anim_data, dict) else []
            if keyframes_data:
                loaded_keyframes = [Keyframe.from_dict(k) for k in keyframes_data]
                self.timeline.rebuild(loaded_keyframes)

        self.tree.expandAll()
        kf_count = len(self.timeline.keyframes)
        self.statusBar().showMessage(
            f"Loaded rig: {path}  ({len(self.nodes)} bones, {kf_count} keyframes)"
        )

    # ------------------------------------------------------------------
    # Animation-only import / export
    # ------------------------------------------------------------------
    def import_animation_json(self):
        """Load a keyframe animation file produced by this editor.

        The animation file is self-describing -- it records which rig it
        animates and which bones it touches -- so loading one is independent
        of loading a rig. If a rig is already loaded, keyframes for bones
        that don't exist on the rig are skipped silently."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Animation JSON", "", "JSON Files (*.json)"
        )
        if not path:
            return

        with open(path, 'r') as f:
            try:
                data = json.load(f)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load animation JSON:\n{e}")
                return

        keyframes_data = data.get("keyframes", []) if isinstance(data, dict) else []
        loaded_keyframes = [Keyframe.from_dict(k) for k in keyframes_data]

        # Filter out bones that aren't in the currently-loaded rig so we
        # don't carry stale rotation snapshots for bones that no longer
        # exist. This also avoids confusion during playback.
        if self.nodes:
            kept = []
            dropped_per_kf = []
            for kf in loaded_keyframes:
                kept_rotations = {
                    name: rot for name, rot in kf.rotations.items() if name in self.nodes
                }
                dropped = len(kf.rotations) - len(kept_rotations)
                dropped_per_kf.append(dropped)
                kf.rotations = kept_rotations
                kept.append(kf)
            loaded_keyframes = kept
            total_dropped = sum(dropped_per_kf)
            if total_dropped:
                self.statusBar().showMessage(
                    f"Note: {total_dropped} bone rotation(s) referenced bones "
                    "not present in the loaded rig and were ignored."
                )

        self.timeline.rebuild(loaded_keyframes)
        self.animation_source_path = path
        self.statusBar().showMessage(
            f"Loaded animation: {path}  ({len(loaded_keyframes)} keyframes)"
        )

    def export_animation_json(self):
        """Write the current keyframes to a NEW animation JSON file.

        Only rotation snapshots, interval, and easing are written. The rig
        file is never modified -- we just record which rig it animates so a
        renderer can find it again."""
        if not self.timeline.keyframes:
            QMessageBox.information(
                self, "No keyframes",
                "There are no keyframes to save. Capture at least one first."
            )
            return

        # Default the suggested filename to <rig_name>_anim.json if we know
        # the rig, otherwise animation.json.
        if self.rig_name:
            suggested = f"{self.rig_name}_anim.json"
        else:
            suggested = "animation.json"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Animation JSON", suggested, "JSON Files (*.json)"
        )
        if not path:
            return

        self._write_animation_file(path)

    def _write_animation_file(self, path):
        """Internal helper: serialize the timeline + rig pointer to disk."""
        data = {
            "format": "lumi_avatar_animation",
            "version": 1,
            "rig_name": self.rig_name,
            "rig_source_path": self.rig_source_path,
            "keyframes": [kf.to_dict() for kf in self.timeline.keyframes],
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
        self.animation_source_path = path
        self.statusBar().showMessage(
            f"Saved animation: {path}  ({len(self.timeline.keyframes)} keyframes)"
        )

    # ------------------------------------------------------------------
    # Keyframe capture
    # ------------------------------------------------------------------
    def capture_keyframe(self):
        if not self.nodes:
            QMessageBox.information(self, "No rig", "Load a rig first.")
            return

        # Snapshot every bone's current rotation.
        rotations = {name: node.rotation() for name, node in self.nodes.items()}

        sel = self.timeline.selected_index()
        if sel is None or sel < 0:
            # No selection => append a new keyframe. Critically, we pass
            # select=False so the new row is NOT left selected -- otherwise
            # the very next Capture press would silently overwrite it
            # instead of adding keyframe #2.
            kf = Keyframe(
                interval_ms=self.default_interval_spin.value(),
                easing=self.default_easing_combo.currentText(),
                rotations=rotations,
            )
            self.timeline.add_keyframe(kf, select=False)
            self.statusBar().showMessage(
                f"Captured keyframe #{len(self.timeline.keyframes)} "
                "(pose continues from here -- capture again for keyframe 2, "
                "right-click a row to overwrite)"
            )
        else:
            # A row is selected => treat Capture as an explicit overwrite.
            existing = self.timeline.keyframes[sel]
            existing.rotations = rotations
            self.timeline.update_keyframe(sel, existing)
            self.statusBar().showMessage(f"Updated keyframe #{sel + 1}")

    def _recapture_keyframe_at(self, row):
        """Triggered by right-click 'Recapture this keyframe'. Snaps the
        current pose into the chosen keyframe, regardless of selection state."""
        if not self.nodes:
            return
        if not (0 <= row < len(self.timeline.keyframes)):
            return
        rotations = {name: node.rotation() for name, node in self.nodes.items()}
        existing = self.timeline.keyframes[row]
        existing.rotations = rotations
        self.timeline.update_keyframe(row, existing)
        self.statusBar().showMessage(f"Re-captured keyframe #{row + 1}")

    def _on_timeline_selection(self, row):
        # Selecting a keyframe in the timeline can do one of two things:
        # 1) If the user is just browsing, we highlight it (no auto-jump
        #    -- jumping the rig would interrupt posing).
        # 2) If the user clicks "Go to Keyframe" via double-click handler
        #    below, we apply the pose.
        pass

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def _snapshot_original_pose(self):
        self.original_rotations = {
            name: node.rotation() for name, node in self.nodes.items()
        }

    def _restore_original_pose(self):
        for name, rot in self.original_rotations.items():
            node = self.nodes.get(name)
            if node:
                node.setRotation(rot)
                node.sync_subtree_pixmaps()

    def toggle_playback(self):
        if self._playing:
            self.stop_playback(restore=True)
        else:
            self.start_playback()

    def start_playback(self):
        if not self.timeline.keyframes:
            QMessageBox.information(self, "No keyframes", "Capture at least one keyframe first.")
            return
        if len(self.timeline.keyframes) < 2:
            # A single keyframe is trivially "the rest pose", but the spec
            # says playback walks THROUGH keyframes, so require 2+.
            QMessageBox.information(
                self, "Not enough keyframes",
                "You need at least 2 keyframes to play an animation."
            )
            return

        self._snapshot_original_pose()
        self._playing = True
        self.play_button.setText("Pause")
        self.play_button.setStyleSheet(
            "font-weight: bold; padding: 6px 14px; background-color: #ef6c00; "
            "border: 1px solid #b04a00;"
        )
        # Start at the first keyframe's pose immediately
        self._apply_keyframe_pose(self.timeline.keyframes[0])
        self._play_index = 1  # we're interpolating TOWARDS keyframes[_play_index]
        self._play_start_time_ms = self._now_ms()
        self._play_timer.start(self.PLAYBACK_TIMER_MS)
        self.statusBar().showMessage("Playing animation...")

    def stop_playback(self, restore=True):
        if self._play_timer.isActive():
            self._play_timer.stop()
        self._playing = False
        self.play_button.setText("Play")
        self.play_button.setStyleSheet(
            "font-weight: bold; padding: 6px 14px; background-color: #2e7d32; "
            "border: 1px solid #1b5e20;"
        )
        if restore:
            self._restore_original_pose()
            self.statusBar().showMessage("Stopped.")

    def _now_ms(self):
        # QDateTime is heavier than we need; perf_counter gives ms with sub-ms
        # precision and is monotonic (immune to wall-clock changes).
        return __import__('time').perf_counter() * 1000.0

    def _tick_playback(self):
        if not self._playing:
            return

        if self._play_index >= len(self.timeline.keyframes):
            self.stop_playback(restore=False)
            self._apply_keyframe_pose(self.timeline.keyframes[-1])
            return

        now = self._now_ms()
        elapsed = now - self._play_start_time_ms

        target_kf = self.timeline.keyframes[self._play_index]
        source_kf = self.timeline.keyframes[self._play_index - 1]

        duration = max(0.001, target_kf.interval_ms)  # avoid div-by-zero
        t = max(0.0, min(1.0, elapsed / duration))
        eased_t = apply_easing(t, target_kf.easing)

        # Interpolate every bone that exists in either snapshot. If a bone
        # wasn't in one of the snapshots, hold its current pose for that
        # segment -- that's the most intuitive behavior when bones are added
        # between captures.
        all_bones = set(source_kf.rotations.keys()) | set(target_kf.rotations.keys())
        all_bones |= set(self.nodes.keys())

        for bone_name in all_bones:
            node = self.nodes.get(bone_name)
            if not node:
                continue
            src = source_kf.rotations.get(bone_name, node.rotation())
            tgt = target_kf.rotations.get(bone_name, node.rotation())
            new_rot = src + (tgt - src) * eased_t
            # Respect each bone's rotation_limit just like dragging does.
            new_rot = max(-node.rotation_limit, min(node.rotation_limit, new_rot))
            node.setRotation(new_rot)
            node.sync_subtree_pixmaps()

        if t >= 1.0:
            # Advance to next transition
            self._apply_keyframe_pose(target_kf)
            self._play_index += 1
            self._play_start_time_ms = self._now_ms()

    def _apply_keyframe_pose(self, kf):
        """Snap bones to a keyframe's exact rotations without interpolation."""
        for name, rot in kf.rotations.items():
            node = self.nodes.get(name)
            if not node:
                continue
            rot_clamped = max(-node.rotation_limit, min(node.rotation_limit, rot))
            node.setRotation(rot_clamped)
            node.sync_subtree_pixmaps()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AnimationEditor()
    window.show()
    sys.exit(app.exec_())
