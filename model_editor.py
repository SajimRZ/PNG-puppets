import sys
import json
import math
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QGraphicsView, QGraphicsScene, 
                             QGraphicsItem, QGraphicsObject, QGraphicsPixmapItem, QVBoxLayout, 
                             QHBoxLayout, QWidget, QPushButton, QTreeWidget, QTreeWidgetItem, 
                             QInputDialog, QFileDialog, QRadioButton, QButtonGroup, QLabel, 
                             QSplitter, QCheckBox, QSpinBox, QGroupBox, QDoubleSpinBox, QMessageBox,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from PyQt5.QtCore import Qt, QPointF, QRectF, QPoint
from PyQt5.QtGui import QPixmap, QPen, QPainter, QColor, QTransform, QFont


# --- ULTRA-SAFE DARK MODE STYLESHEET ---
DARK_THEME = """
QWidget, QMainWindow, QDialog, QMessageBox { background-color: #2b2b2b; color: #e0e0e0; font-family: 'Segoe UI', Arial, sans-serif; }
QLabel, QCheckBox, QRadioButton { color: #e0e0e0; background: transparent; }
QPushButton { background-color: #3d3d3d; color: #e0e0e0; border: 1px solid #555; padding: 6px; border-radius: 4px; }
QPushButton:hover { background-color: #4d4d4d; border: 1px solid #777; }
QPushButton:pressed { background-color: #5d5d5d; }
QTreeWidget, QListView, QTableView, QTableWidget, QAbstractItemView { background-color: #1e1e1e; alternate-background-color: #242424; color: #e0e0e0; border: 1px solid #444; border-radius: 4px; }
QTreeWidget::item:selected, QListView::item:selected, QTableWidget::item:selected { background-color: #2d5a88; color: white; }
QGraphicsView { border: none; background-color: #1a1a1a; }
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit { background-color: #1e1e1e; color: #e0e0e0; border: 1px solid #555; padding: 4px; border-radius: 3px; }
QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 10px; padding-top: 15px; color: #e0e0e0; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; color: #e0e0e0; }
QSplitter::handle { background-color: #444; }
QHeaderView::section { background-color: #3d3d3d; color: #e0e0e0; border: 1px solid #555; padding: 4px; }
"""

class CanvasView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.panning = False
        self.pan_start = QPoint()

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
                # The image is no longer a child of the bone, but it still keeps its own
                # pivot/offset defined in the bone's local coordinate space, so dragging
                # still works the same way from the user's perspective.
                local_start = self.manip_node.mapFromScene(self.scene_drag_start)
                local_end = self.manip_node.mapFromScene(event.scenePos())
                new_offset = self.image_offset_start + (local_end - local_start)
                self.manip_node.set_image_offset(new_offset)
                
            elif mode == "POSE":
                n_pos = self.manip_node.scenePos()
                m_pos = event.scenePos()
                current_angle = math.atan2(m_pos.y() - n_pos.y(), m_pos.x() - n_pos.x())
                diff_deg = math.degrees(current_angle - self.angle_offset)
                self.manip_node.setRotation(self.node_start_rot + diff_deg)
                    
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
    """
    Represents a joint/bone in the rig.

    IMPORTANT ARCHITECTURE NOTE:
    The bone hierarchy (parent/child BoneNode chain) is still used for posing/FK — moving
    or rotating a bone still moves/rotates all of its descendant bones, exactly as before.

    However, the bone's PNG (self.pixmap_item) is intentionally NOT a child of this item.
    In Qt's scene graph, a child item ALWAYS paints on top of its parent no matter what
    zValue() it has -- zValue only sorts siblings. That means if the pixmap were a child of
    the bone, a "hand" bone's image could never be drawn behind a "torso" ancestor's image,
    regardless of any Z-level you assign.

    To give the images a true GLOBAL stacking order, each bone's pixmap_item lives directly
    in the scene as a top-level sibling of every other bone's pixmap_item. Its position/
    rotation is kept in sync with the bone's transform manually (see sync_pixmap), and its
    zValue (self.image_z) is therefore compared against ALL other images in the whole rig,
    completely independent of skeleton depth.
    """

    def __init__(self, name, editor):
        super().__init__()
        self.name = name
        self.editor = editor
        
        self.image_paths = []
        self.expression_index = 0
        self.flip_h = False
        self.flip_v = False
        self.image_rotation = 0.0
        self.image_offset = QPointF(0.0, 0.0)   # pivot/offset of the image, in this bone's local space
        self.image_z = 0                        # GLOBAL z-level for the image (independent of bone hierarchy)
        
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(0)
        
        # Detached, top-level pixmap item -- NOT parented to self. See class docstring.
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setZValue(self.image_z)

    def boundingRect(self):
        rect = QRectF(-10, -10, 20, 20)
        if not self.pixmap_item.pixmap().isNull():
            # mapRectFromItem works between any two items sharing a scene, even without a
            # parent/child relationship, so the image area is still clickable/selectable.
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

        # Any of these mean this bone's world transform (and therefore every descendant
        # bone's world transform) may have changed -- resync the detached image(s).
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
        """ Sets this bone's GLOBAL image Z-level. This is completely separate from the
            bone's own zValue() (which only affects the little joint-dot markers) and is
            not subject to parent/child override -- it's a flat, whole-rig stacking order. """
        self.image_z = z
        self.pixmap_item.setZValue(z)

    def sync_pixmap(self):
        """ Recomputes the world transform for this bone's (detached) pixmap item, from:
              1. this bone's own sceneTransform() (position + rotation, inherited through
                 the whole bone-parent chain -- this is what makes posing work), combined
                 with
              2. this bone's own local image pivot offset / rotation / flip.
            zValue is deliberately NOT touched here -- it's controlled independently via
            set_image_z() / the Z-Order sidebar. """
        self.prepareGeometryChange()

        if self.pixmap_item.pixmap().isNull():
            return

        w = self.pixmap_item.pixmap().width()
        h = self.pixmap_item.pixmap().height()
        cx, cy = w / 2.0, h / 2.0

        # Build the bone-local image arrangement: rotate/flip around the image's own
        # center, then shift by the pivot offset -- same math as before, just no longer
        # expressed as a child item's pos()/transform(), but as one explicit matrix.
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
        """ Resyncs this bone's image, then recurses into every descendant bone, since a
            parent's transform change cascades into every child's world position even
            though Qt won't fire itemChange on the children themselves. """
        self.sync_pixmap()
        for child in self.childItems():
            if isinstance(child, BoneNode):
                child.sync_subtree_pixmaps()


class PuppetEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PNG Puppet Pro Editor")
        self.resize(1500, 900)
        self.setStyleSheet(DARK_THEME)
        
        self.nodes = {} 
        self.tree_items = {} 
        self.current_mode = "MOVE_JOINT"
        
        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)
        
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        
        # --- 1. Left Panel (Tools, Hierarchy & Properties) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        
        # Modes
        mode_groupbox = QGroupBox("Tools")
        mode_layout = QVBoxLayout()
        self.mode_group = QButtonGroup(self)
        
        btn_mode_joint = QRadioButton("Move Joint (Assembly)")
        btn_mode_joint.setChecked(True)
        btn_mode_joint.clicked.connect(lambda: self.set_mode("MOVE_JOINT"))
        
        btn_mode_img = QRadioButton("Move Image Offset (Pivot)")
        btn_mode_img.clicked.connect(lambda: self.set_mode("MOVE_IMAGE"))
        
        btn_mode_pose = QRadioButton("Pose (Rotate Joint FK)")
        btn_mode_pose.clicked.connect(lambda: self.set_mode("POSE"))
        
        self.mode_group.addButton(btn_mode_joint)
        self.mode_group.addButton(btn_mode_img)
        self.mode_group.addButton(btn_mode_pose)
        
        mode_layout.addWidget(btn_mode_joint)
        mode_layout.addWidget(btn_mode_img)
        mode_layout.addWidget(btn_mode_pose)
        mode_groupbox.setLayout(mode_layout)
        left_layout.addWidget(mode_groupbox)
        
        # Hierarchy
        hier_groupbox = QGroupBox("Skeleton Hierarchy")
        hier_layout = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Bones"])
        self.tree.itemSelectionChanged.connect(self.on_tree_selection)
        hier_layout.addWidget(self.tree)
        
        btn_h_layout_top = QHBoxLayout()
        btn_add = QPushButton("Add Bone")
        btn_add.clicked.connect(self.add_bone)
        btn_del = QPushButton("Delete Bone")
        btn_del.setStyleSheet("QPushButton { background-color: #8b3a3a; border: 1px solid #5a2222; } QPushButton:hover { background-color: #a84747; }")
        btn_del.clicked.connect(self.delete_bone)
        btn_h_layout_top.addWidget(btn_add)
        btn_h_layout_top.addWidget(btn_del)
        hier_layout.addLayout(btn_h_layout_top)
        
        btn_ren = QPushButton("Rename Selected Bone")
        btn_ren.clicked.connect(self.rename_bone)
        hier_layout.addWidget(btn_ren)
        
        hier_groupbox.setLayout(hier_layout)
        left_layout.addWidget(hier_groupbox)
        
        # Node Properties
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
        
        # Global Actions
        left_layout.addStretch()
        btn_load = QPushButton("Load Rig from JSON")
        btn_load.setStyleSheet("font-weight: bold; padding: 10px; background-color: #0277bd; border: 1px solid #01579b;")
        btn_load.clicked.connect(self.import_json)
        left_layout.addWidget(btn_load)
        
        btn_export = QPushButton("Save / Export JSON")
        btn_export.setStyleSheet("font-weight: bold; padding: 10px; background-color: #2e7d32; border: 1px solid #1b5e20;")
        btn_export.clicked.connect(self.export_json)
        left_layout.addWidget(btn_export)
        
        # --- 2. Center Panel (Canvas) ---
        self.scene = RigScene(self)
        self.scene.selectionChanged.connect(self.on_scene_selection)
        self.view = CanvasView(self.scene)
        
        # Setup HUD Overlay
        self.hud_label = QLabel(self.view)
        self.hud_label.setStyleSheet("color: #4CAF50; font-size: 14px; font-weight: bold; background-color: rgba(0, 0, 0, 180); padding: 8px; border-radius: 5px;")
        self.hud_label.move(10, 10)
        self.hud_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hud_label.hide()

        # --- 3. Right Panel (Z-Order Manager Sidebar) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        
        z_groupbox = QGroupBox("Z-Order Manager (Layer Stack)")
        z_panel_layout = QVBoxLayout()
        
        self.z_table = QTableWidget()
        self.z_table.setColumnCount(2)
        self.z_table.setHorizontalHeaderLabels(["Bone Name", "Z-Level"])
        self.z_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.z_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.z_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.z_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.z_table.itemSelectionChanged.connect(self.on_z_table_selection)
        
        z_panel_layout.addWidget(self.z_table)
        
        btn_sort_z = QPushButton("Sort Stack (High to Low)")
        btn_sort_z.clicked.connect(self.refresh_z_table)
        z_panel_layout.addWidget(btn_sort_z)
        
        info_lbl = QLabel("Higher Z-Level renders the PNG on top of ALL other bones' PNGs,\n"
                           "regardless of skeleton hierarchy (a hand can go behind a torso).")
        info_lbl.setStyleSheet("color: #888888; font-size: 11px;")
        info_lbl.setWordWrap(True)
        z_panel_layout.addWidget(info_lbl)
        
        z_groupbox.setLayout(z_panel_layout)
        right_layout.addWidget(z_groupbox)

        # Assemble Splitter Layout
        splitter.addWidget(left_panel)
        splitter.addWidget(self.view)
        splitter.addWidget(right_panel)
        splitter.setSizes([320, 880, 300])

    def set_mode(self, mode):
        self.current_mode = mode

    def update_info_hud(self):
        node = self.get_selected_node()
        if node:
            text = (f"Bone: {node.name}  |  Local Pos: ({node.pos().x():.1f}, {node.pos().y():.1f})  "
                    f"|  Rot: {node.rotation():.1f}°  |  Img Z: {node.image_z}")
            self.hud_label.setText(text)
            self.hud_label.adjustSize()
            self.hud_label.show()
        else:
            self.hud_label.hide()

    def refresh_z_table(self):
        """ Re-populates the Z-Order sidebar table, sorted by each bone's GLOBAL image
            Z-level (descending). This is independent of the bone hierarchy. """
        self.z_table.blockSignals(True)
        selected_node = self.get_selected_node()
        selected_name = selected_node.name if selected_node else None
        
        # Sort bones by their global image Z-value (Highest visual layer at top of table)
        sorted_nodes = sorted(self.nodes.values(), key=lambda n: n.image_z, reverse=True)
        
        self.z_table.setRowCount(len(sorted_nodes))
        select_row = -1
        
        for row, node in enumerate(sorted_nodes):
            # Column 0: Bone Name
            name_item = QTableWidgetItem(node.name)
            name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.z_table.setItem(row, 0, name_item)
            
            # Column 1: Editable Z-Level SpinBox
            spin = QSpinBox()
            spin.setRange(-9999, 9999)
            spin.setValue(int(node.image_z))
            spin.valueChanged.connect(lambda val, n=node: self.on_z_table_value_change(n, val))
            self.z_table.setCellWidget(row, 1, spin)
            
            if selected_name and node.name == selected_name:
                select_row = row
                
        if select_row != -1:
            self.z_table.setCurrentCell(select_row, 0)
        else:
            self.z_table.clearSelection()
            
        self.z_table.blockSignals(False)

    def sync_z_table_selection(self):
        """ Syncs the table highlight row with scene selection without re-sorting. """
        node = self.get_selected_node()
        self.z_table.blockSignals(True)
        if node:
            for row in range(self.z_table.rowCount()):
                item = self.z_table.item(row, 0)
                if item and item.text() == node.name:
                    self.z_table.setCurrentItem(item)
                    break
        else:
            self.z_table.clearSelection()
        self.z_table.blockSignals(False)

    def on_z_table_selection(self):
        """ Triggered when a row in the Z-Order sidebar is clicked. """
        selected_items = self.z_table.selectedItems()
        if selected_items:
            row = selected_items[0].row()
            name_item = self.z_table.item(row, 0)
            if name_item:
                name = name_item.text()
                node = self.nodes.get(name)
                if node and node != self.get_selected_node():
                    self.scene.blockSignals(True)
                    self.scene.clearSelection()
                    node.setSelected(True)
                    
                    # Update Tree Widget
                    tree_item = self.tree_items.get(name)
                    if tree_item:
                        self.tree.blockSignals(True)
                        self.tree.setCurrentItem(tree_item)
                        self.tree.blockSignals(False)
                        
                    self.sync_properties_ui()
                    self.update_info_hud()
                    self.scene.blockSignals(False)
                    self.scene.update()

    def on_z_table_value_change(self, node, value):
        """ Triggered when a spinbox value inside the Z-Order sidebar is changed. """
        node.set_image_z(value)
        self.scene.update()
        self.update_info_hud()

    def add_bone(self):
        name, ok = QInputDialog.getText(self, "Add Bone", "Bone Name:")
        if not ok or not name or name in self.nodes:
            return
            
        node = BoneNode(name, self)
        self.nodes[name] = node
        self.scene.addItem(node)
        self.scene.addItem(node.pixmap_item)
        
        tree_item = QTreeWidgetItem([name])
        self.tree_items[name] = tree_item
        
        selected_items = self.tree.selectedItems()
        if selected_items:
            parent_name = selected_items[0].text(0)
            parent_node = self.nodes[parent_name]
            node.setParentItem(parent_node)
            selected_items[0].addChild(tree_item)
            node.setPos(50, 50) 
            selected_items[0].setExpanded(True)
        else:
            self.tree.addTopLevelItem(tree_item)
            center = self.view.mapToScene(self.view.viewport().rect().center())
            node.setPos(center)

        node.sync_pixmap()
        self.refresh_z_table()

    def rename_bone(self):
        selected = self.get_selected_node()
        if not selected: return
        
        old_name = selected.name
        new_name, ok = QInputDialog.getText(self, "Rename Bone", "New Name:", text=old_name)
        
        if ok and new_name and new_name != old_name:
            if new_name in self.nodes:
                QMessageBox.warning(self, "Error", "A bone with this name already exists!")
                return
                
            self.nodes[new_name] = self.nodes.pop(old_name)
            self.tree_items[new_name] = self.tree_items.pop(old_name)
            
            selected.name = new_name
            self.tree_items[new_name].setText(0, new_name)
            self.refresh_z_table()
            self.update_info_hud()

    def delete_bone(self):
        selected = self.get_selected_node()
        if not selected: return
        
        def remove_node_and_children(node):
            for child in list(node.childItems()):
                if isinstance(child, BoneNode):
                    remove_node_and_children(child)
                    
            tree_item = self.tree_items.pop(node.name, None)
            if tree_item:
                parent_item = tree_item.parent()
                if parent_item:
                    parent_item.removeChild(tree_item)
                else:
                    index = self.tree.indexOfTopLevelItem(tree_item)
                    self.tree.takeTopLevelItem(index)
            
            self.nodes.pop(node.name, None)
            if node.pixmap_item.scene():
                self.scene.removeItem(node.pixmap_item)
            self.scene.removeItem(node)

        remove_node_and_children(selected)
        self.refresh_z_table()
        self.sync_properties_ui()

    def assign_image(self):
        selected = self.get_selected_node()
        if not selected: return
        
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PNG(s)", "", "Images (*.png)")
        if paths:
            for path in paths:
                selected.add_image(path)
            self.sync_properties_ui()

    def adjust_z(self, delta):
        selected = self.get_selected_node()
        if selected:
            selected.set_image_z(selected.image_z + delta)
            self.refresh_z_table()

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
                self.sync_z_table_selection()
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
            
        self.sync_z_table_selection()
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

    def clear_rig(self):
        self.scene.clearSelection()
        self.scene.clear() 
        self.nodes.clear()
        self.tree_items.clear()
        self.tree.clear()
        self.refresh_z_table()
        self.update_info_hud()

    def import_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load JSON Rig", "", "JSON Files (*.json)")
        if not path: return
        
        with open(path, 'r') as f:
            try:
                data = json.load(f)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load JSON:\n{e}")
                return
                
        self.clear_rig()
        bones_data = data.get("bones", {})
        
        # Step 1: Create all nodes (and their detached pixmap items) in dict first
        for name, b_data in bones_data.items():
            node = BoneNode(name, self)
            self.nodes[name] = node
            self.scene.addItem(node)
            self.scene.addItem(node.pixmap_item)
            tree_item = QTreeWidgetItem([name])
            self.tree_items[name] = tree_item
            
        # Step 2: Establish hierarchy and apply properties
        for name, b_data in bones_data.items():
            node = self.nodes[name]
            parent_name = b_data.get("parent")
            
            if parent_name and parent_name in self.nodes:
                node.setParentItem(self.nodes[parent_name])
                self.tree_items[parent_name].addChild(self.tree_items[name])
            else:
                self.tree.addTopLevelItem(self.tree_items[name])
                
            node.setPos(b_data.get("local_x", 0), b_data.get("local_y", 0))
            node.setRotation(b_data.get("rotation", 0))
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
            
            # Apply the expression FIRST so it triggers the auto-center behavior
            if node.image_paths:
                node.set_expression(node.expression_index)

            # THEN overwrite the auto-center with the actual saved offsets from the JSON
            saved_offset = QPointF(b_data.get("image_offset_x", 0), b_data.get("image_offset_y", 0))
            node.set_image_offset(saved_offset)
            
            node.sync_pixmap()
            
        # Step 3: Final full resync pass. Since bones can appear in any order in the JSON,
        # a child might have been positioned before its parent's own position/rotation was
        # applied -- this guarantees every image ends up matching the final pose.
        for node in self.nodes.values():
            node.sync_pixmap()
        
        self.tree.expandAll()
        self.refresh_z_table()
        print(f"Loaded successfully from {path}")

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON", "", "JSON Files (*.json)")
        if not path: return
            
        data = {"bones": {}}
        for name, node in self.nodes.items():
            parent_item = node.parentItem()
            parent_name = parent_item.name if isinstance(parent_item, BoneNode) else None
            
            data["bones"][name] = {
                "parent": parent_name,
                "local_x": round(node.pos().x(), 2),
                "local_y": round(node.pos().y(), 2),
                "rotation": round(node.rotation(), 2),
                "image_paths": node.image_paths, 
                "expression_index": node.expression_index,
                "flip_h": node.flip_h,
                "flip_v": node.flip_v,
                "image_rotation": round(node.image_rotation, 2),
                "image_offset_x": round(node.image_offset.x(), 2),
                "image_offset_y": round(node.image_offset.y(), 2),
                "z_value": node.image_z  
            }
            
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Exported successfully to {path}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PuppetEditor()
    window.show()
    sys.exit(app.exec_())