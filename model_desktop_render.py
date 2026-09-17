import sys
import json
import os
import re
import socket
import threading
import time
import ctypes
from ctypes import wintypes

from PyQt6.QtWidgets import QApplication, QGraphicsView, QGraphicsScene, QGraphicsObject, QGraphicsPixmapItem
from PyQt6.QtCore import Qt, QRectF, QObject, pyqtSignal, pyqtSlot, QThread
from PyQt6.QtGui import QImage, QPixmap, QTransform, QScreen


# --- Easy tuning variables ---
MODEL_SCALE = 0.15
MODEL_PADDING = 24
PHYSICS_FPS = 60
GRAVITY = 2800.0
FRICTION = 0.65
BOUNCE = -0.02
MAX_LEAN = 75.0
MAX_DROP = 45.0
LEAN_FACTOR = 0.03
DROP_FACTOR = 0.02
THROW_STRENGTH = 0.55
MAX_THROW_SPEED_X = 1000.0
MAX_THROW_SPEED_Y = 1000.0
PLATFORM_Y = 560  # Set a screen Y coordinate, or use None for the screen bottom.


class ImageAssetCache:
    """Loads each PNG once and persists its alpha-tight bounds between runs."""

    def __init__(self, cache_path):
        self.cache_path = cache_path
        self.entries = {}
        self.assets = {}
        self._load_metadata()

    def _load_metadata(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as cache_file:
                metadata = json.load(cache_file)
        except (OSError, ValueError):
            metadata = {}

        for path, entry in metadata.items():
            if isinstance(entry, dict) and "bbox" in entry:
                self.entries[path] = entry

    def _save_metadata(self):
        temporary_path = self.cache_path + ".tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as cache_file:
                json.dump(self.entries, cache_file, indent=2, sort_keys=True)
            os.replace(temporary_path, self.cache_path)
        except OSError as error:
            print(f"Warning: Could not save image bounds: {error}")

    @staticmethod
    def _file_signature(path):
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}

    def get(self, path):
        path = os.path.normcase(os.path.abspath(path))
        if path in self.assets:
            return self.assets[path]

        signature = self._file_signature(path)
        if signature is None:
            return None

        cached = self.entries.get(path)
        image = QImage(path)
        if image.isNull():
            return None

        width, height = image.width(), image.height()
        if (
            cached
            and cached.get("mtime_ns") == signature["mtime_ns"]
            and cached.get("size") == signature["size"]
            and cached.get("canvas") == [width, height]
        ):
            bbox = cached["bbox"]
        else:
            left, top, right, bottom = width, height, -1, -1
            for y in range(height):
                for x in range(width):
                    if image.pixelColor(x, y).alpha() > 0:
                        left = min(left, x)
                        top = min(top, y)
                        right = max(right, x)
                        bottom = max(bottom, y)

            bbox = [0, 0, 0, 0] if right < left else [
                left, top, right - left + 1, bottom - top + 1
            ]
            self.entries[path] = {
                **signature,
                "canvas": [width, height],
                "bbox": bbox,
            }
            self._save_metadata()

        if bbox[2] and bbox[3]:
            pixmap = QPixmap.fromImage(image.copy(*bbox))
        else:
            pixmap = QPixmap()

        asset = (pixmap, (width, height), tuple(bbox))
        self.assets[path] = asset
        return asset

class GlobalHotkeyListener(QThread):
    f9_pressed = pyqtSignal()

    def run(self):
        if sys.platform != "win32":
            print("Warning: Global F9 hotkey is only supported on Windows.")
            return

        user32 = ctypes.windll.user32
        HOTKEY_ID = 1
        VK_F9 = 0x78          
        MOD_NOREPEAT = 0x4000  
        WM_HOTKEY = 0x0312

        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, VK_F9):
            print("Warning: Could not register global F9 hotkey.")
            return

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.f9_pressed.emit()
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)


class PhysicsThread(QThread):
    """
    Dedicated background thread for lagless physics calculations.
    Maintains a consistent 60 FPS tick independent of the main GUI thread.
    """
    physics_tick = pyqtSignal(int, int, float, float)

    def __init__(self, screen_w, screen_h):
        super().__init__()
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.floor_y = screen_h if PLATFORM_Y is None else PLATFORM_Y
        self.x, self.y = 0.0, 0.0
        self.vx, self.vy = 0.0, 0.0
        self.target_x = None
        self.target_x_speed = 300.0
        
        # Kinematic constants
        self.gravity = GRAVITY
        self.friction = FRICTION
        self.bounce = BOUNCE
        
        self.is_dragging = False
        self.running = True
        self.last_time = time.time()

    def sync_drag(self, x, y):
        # Calculate velocity based on drag distance for natural tossing momentum
        now = time.time()
        delta = max(now - self.last_time, 0.001)
        raw_vx = (x - self.x) / delta
        raw_vy = (y - self.y) / delta
        self.vx = max(-MAX_THROW_SPEED_X, min(MAX_THROW_SPEED_X, raw_vx * THROW_STRENGTH))
        self.vy = max(-MAX_THROW_SPEED_Y, min(MAX_THROW_SPEED_Y, raw_vy * THROW_STRENGTH))
        self.x, self.y = float(x), float(y)
        self.last_time = now

    def move_to_x(self, x_position, speed):
        self.target_x = max(0.0, min(float(self.screen_w), float(x_position)))
        self.target_x_speed = max(1.0, abs(float(speed)))

    def run(self):
        fps = PHYSICS_FPS
        target_delta = 1.0 / fps
        
        while self.running:
            now = time.time()
            delta = now - self.last_time
            self.last_time = now

            if not self.is_dragging:
                # Apply gravity and velocity
                self.vy += self.gravity * delta

                if self.target_x is not None:
                    distance = self.target_x - self.x
                    step = self.target_x_speed * delta
                    if abs(distance) <= step:
                        self.x = self.target_x
                        self.target_x = None
                        self.vx = 0
                    else:
                        self.vx = self.target_x_speed if distance > 0 else -self.target_x_speed
                        self.x += self.vx * delta
                else:
                    self.x += self.vx * delta
                
                self.y += self.vy * delta
                
                # Floor collision logic
                if self.y >= self.floor_y:
                    self.y = self.floor_y
                    self.vy *= self.bounce
                    self.vx *= self.friction
                    
                    if abs(self.vy) < 25: self.vy = 0
                    if abs(self.vx) < 25: self.vx = 0

                # Screen bounds (Walls)
                if self.x <= 0:
                    self.x = 0
                    self.vx *= self.bounce
                elif self.x >= self.screen_w:
                    self.x = self.screen_w
                    self.vx *= self.bounce

            # Emit state to GUI for transform and kinematic dangling updates
            self.physics_tick.emit(int(self.x), int(self.y), self.vx, self.vy)
            
            elapsed = time.time() - now
            if elapsed < target_delta:
                time.sleep(target_delta - elapsed)


class RigBone(QGraphicsObject):
    def __init__(self, data, asset_cache):
        super().__init__()
        self.data = data
        self.asset_cache = asset_cache
        self.rotation_limit = max(0.0, abs(float(data.get("rotation_limit", 360.0))))
        self.base_rotation = max(
            -self.rotation_limit,
            min(self.rotation_limit, float(data.get("rotation", 0))),
        )
        self.setPos(data.get("local_x", 0), data.get("local_y", 0))
        self.setRotation(self.base_rotation)
        
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setZValue(data.get("z_value", 0))
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.loaded_path = None

    def boundingRect(self):
        return QRectF(-1, -1, 2, 2)

    def paint(self, painter, option, widget):
        pass

    def apply_image_transform(self):
        paths = self.data.get("image_paths", [])
        idx = self.data.get("expression_index", 0)
        
        if not paths or idx >= len(paths):
            return
            
        path = paths[idx]
        asset = self.asset_cache.get(path)
        if asset is None:
            return
        pixmap, canvas_size, bbox = asset
        if path != self.loaded_path:
            self.pixmap_item.setPixmap(pixmap)
            self.pixmap_item.setOffset(bbox[0], bbox[1])
            self.loaded_path = path

        off_x = self.data.get("image_offset_x", 0)
        off_y = self.data.get("image_offset_y", 0)
        rot = self.data.get("image_rotation", 0)
        flip_h = self.data.get("flip_h", False)
        flip_v = self.data.get("flip_v", False)

        w, h = canvas_size
        cx, cy = w / 2.0, h / 2.0

        local = QTransform()
        local.translate(off_x, off_y)
        local.translate(cx, cy)
        local.rotate(rot)
        local.scale(-1 if flip_h else 1, -1 if flip_v else 1)
        local.translate(-cx, -cy)

        self.pixmap_item.setTransform(local * self.sceneTransform())

    def update_subtree_transforms(self):
        self.apply_image_transform()
        for child in self.childItems():
            if isinstance(child, RigBone):
                child.update_subtree_transforms()


class DesktopMascot(QGraphicsView):
    def __init__(self, json_path, scale_factor=MODEL_SCALE):
        super().__init__()
        self.scale_factor = scale_factor
        self.asset_cache = ImageAssetCache(
            os.path.join(os.path.dirname(os.path.abspath(json_path)), ".image_bounds.json")
        )
        
        # NOTE: Removed WA_TransparentForMouseEvents so mouse dragging functions
        flags = (
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.setStyleSheet("background: transparent; border: none;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        self.bones = {}
        self.load_rig(json_path)

        # Initialize Physics after rig bounds are calculated
        screen = QApplication.primaryScreen().availableGeometry()
        self.physics = PhysicsThread(
            screen.width() - self.width(), screen.height() - self.height()
        )
        self.physics.x = screen.left()
        self.physics.y = screen.top()
        self.physics.physics_tick.connect(self._physics_process)
        self.physics.start()

    def load_rig(self, path):
        try:
            with open(path, 'r') as f:
                data = json.load(f).get("bones", {})
        except Exception as e:
            print(f"Failed to load rig: {e}")
            return
            
        for name, b_data in data.items():
            bone = RigBone(b_data, self.asset_cache)
            self.bones[name] = bone
            self.scene.addItem(bone)
            self.scene.addItem(bone.pixmap_item)
            
        for name, b_data in data.items():
            parent_name = b_data.get("parent")
            if parent_name and parent_name in self.bones:
                self.bones[name].setParentItem(self.bones[parent_name])
                
        for bone in self.bones.values():
            bone.apply_image_transform()
            
        rect = self.scene.itemsBoundingRect()
        self.scene.setSceneRect(rect)
        
        padding_scene = MODEL_PADDING / max(self.scale_factor, 0.01)
        padded_rect = rect.adjusted(
            -padding_scene, -padding_scene, padding_scene, padding_scene
        )
        self.scene.setSceneRect(padded_rect)
        self.resize(
            int(padded_rect.width() * self.scale_factor),
            int(padded_rect.height() * self.scale_factor),
        )
        self.resetTransform()
        self.scale(self.scale_factor, self.scale_factor)
        self.centerOn(rect.center())

    @pyqtSlot(int, int, float, float)
    def _physics_process(self, x, y, vx, vy):
        # Let the UI instantly follow the mouse if dragging; otherwise, use physics target
        if not self.physics.is_dragging:
            self.move(x, y)
        
        # Ragdoll kinematic adjustments mapping velocity vectors to limb rotations
        lean = max(-MAX_LEAN, min(MAX_LEAN, vx * LEAN_FACTOR))
        drop = max(-MAX_DROP, min(MAX_DROP, vy * DROP_FACTOR))
        
        def apply_offset(bone_name, offset):
            if bone_name in self.bones:
                b = self.bones[bone_name]
                rotation = max(
                    -b.rotation_limit,
                    min(b.rotation_limit, b.base_rotation + offset),
                )
                b.setRotation(rotation)
                b.update_subtree_transforms()
        
        apply_offset("Lhand", -lean - drop)
        apply_offset("Rhand", -lean + drop)
        apply_offset("legL", -lean)
        apply_offset("legR", -lean)
        apply_offset("hairBack", lean * 0.5)
        apply_offset("hairFront", lean * 0.7)

    # --- Mouse Events for Dragging ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.physics.is_dragging = True
            self.physics.x = self.x()
            self.physics.y = self.y()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, 'drag_offset'):
            new_pos = event.globalPosition().toPoint() - self.drag_offset
            self.move(new_pos)
            self.physics.sync_drag(new_pos.x(), new_pos.y())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.physics.is_dragging = False

    def closeEvent(self, event):
        self.physics.running = False
        self.physics.wait()
        super().closeEvent(event)


class PuppetController(QObject):
    sig_rotate_bone = pyqtSignal(str, float)
    sig_set_expression = pyqtSignal(str, int)
    sig_move_model_x = pyqtSignal(float, float)

    def __init__(self, mascot: DesktopMascot):
        super().__init__()
        self.mascot = mascot
        self.sig_rotate_bone.connect(self._handle_rotate_bone)
        self.sig_set_expression.connect(self._handle_set_expression)
        self.sig_move_model_x.connect(self._handle_move_model_x)

    def move_model_x(self, x_position, speed=300.0):
        """Move the whole avatar to an absolute screen X position at pixels per second."""
        self.sig_move_model_x.emit(float(x_position), float(speed))

    @pyqtSlot(str, float)
    def _handle_rotate_bone(self, bone_name, angle_deg):
        if bone_name in self.mascot.bones:
            bone = self.mascot.bones[bone_name]
            # Update base rotation so physics offsets play nicely with LLM commands
            bone.base_rotation = max(
                -bone.rotation_limit,
                min(bone.rotation_limit, angle_deg),
            )
            bone.setRotation(bone.base_rotation)
            bone.update_subtree_transforms()

    @pyqtSlot(str, int)
    def _handle_set_expression(self, bone_name, expr_index):
        if bone_name in self.mascot.bones:
            bone = self.mascot.bones[bone_name]
            bone.data["expression_index"] = expr_index
            bone.apply_image_transform()

    @pyqtSlot(float, float)
    def _handle_move_model_x(self, x_position, speed):
        physics = self.mascot.physics
        physics.move_to_x(x_position, speed)

    def parse_llm_stream(self, text_chunk: str):
        for match in re.finditer(r"<rotate:([a-zA-Z0-9_]+)=([-\d.]+)>", text_chunk):
            bone_name = match.group(1)
            angle = float(match.group(2))
            self.sig_rotate_bone.emit(bone_name, angle)

        for match in re.finditer(r"<expr:([a-zA-Z0-9_]+)=(\d+)>", text_chunk):
            bone_name = match.group(1)
            idx = int(match.group(2))
            self.sig_set_expression.emit(bone_name, idx)

        for match in re.finditer(r"<move:x=([-\d.]+)(?:,speed=([-\d.]+))?>", text_chunk):
            speed = float(match.group(2)) if match.group(2) else 300.0
            self.move_model_x(float(match.group(1)), speed)


class ControllerCommandServer(threading.Thread):
    """Accept newline-delimited controller commands from localhost clients."""

    def __init__(self, controller, host="127.0.0.1", port=8765):
        super().__init__(daemon=True)
        self.controller = controller
        self.host = host
        self.port = port
        self.stop_event = threading.Event()
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((host, port))
        self.server_socket.listen()
        self.server_socket.settimeout(0.5)

    def run(self):
        print(f"Controller command server listening on {self.host}:{self.port}")
        while not self.stop_event.is_set():
            try:
                client, _ = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop_event.is_set():
                    break
                raise

            with client:
                client.settimeout(1.0)
                data = b""
                try:
                    while not self.stop_event.is_set():
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                except socket.timeout:
                    pass

                for command in data.decode("utf-8", errors="ignore").splitlines():
                    self.controller.parse_llm_stream(command)

    def stop(self):
        self.stop_event.set()
        self.server_socket.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    target_file = "assets/save4.json"
    if not os.path.exists(target_file):
        print(f"Error: {target_file} not found.")
        
    mascot = DesktopMascot(target_file)
    controller = PuppetController(mascot)
    controller.parse_llm_stream("<expr:face=0>")
    command_server = ControllerCommandServer(controller)
    command_server.start()
    
    hotkey_listener = GlobalHotkeyListener()
    
    # Gracefully shut down threads on exit
    def quit_app():
        mascot.close()
        mascot.physics.running = False
        command_server.stop()
        hotkey_listener.requestInterruption()
        if mascot.physics.isRunning():
            mascot.physics.wait()
        if hotkey_listener.isRunning():
            hotkey_listener.wait()
        QApplication.quit()
        
    hotkey_listener.f9_pressed.connect(quit_app)
    hotkey_listener.start()
    
    mascot.show()
    
    sys.exit(app.exec())