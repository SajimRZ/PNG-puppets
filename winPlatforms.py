"""
windowDetector.py
=================

Windows dynamic window/platform detection for LumiAvatar.

Designed to be imported by LumiModel.py.

Coordinates:
    All detector coordinates are kept in Windows screen coordinates.

The module:
    - Detects all top-level windows
    - Ignores minimized windows
    - Ignores invisible windows
    - Ignores DWM-cloaked windows
    - Handles overlapping windows
    - Handles maximized windows
    - Handles multiple monitors
    - Handles Windows DPI scaling
    - Provides window bounding boxes
    - Provides usable top/bottom platform surfaces
    - Can optionally display a debug overlay

Normal LumiModel usage:

    from windowDetector import WindowPlatformDetector

    detector = WindowPlatformDetector()

    detector.update()

    boxes = detector.get_bounding_boxes()
    platforms = detector.get_platforms()

"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import time


# ============================================================
# WINDOWS API
# ============================================================

user32 = ctypes.WinDLL(
    "user32",
    use_last_error=True
)

dwmapi = ctypes.WinDLL(
    "dwmapi",
    use_last_error=True
)


# ============================================================
# DPI AWARENESS
# ============================================================

def enable_dpi_awareness():
    """
    Make the process Per-Monitor DPI aware.

    IMPORTANT:
    This should happen before Qt creates its application/screen
    objects.
    """

    # Windows 10+
    try:

        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (
            ctypes.c_void_p(-4)
        )

        user32.SetProcessDpiAwarenessContext.argtypes = [
            ctypes.c_void_p
        ]

        user32.SetProcessDpiAwarenessContext.restype = (
            wintypes.BOOL
        )

        success = user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )

        if success:
            return True

    except Exception:
        pass

    # Windows 8.1+
    try:

        shcore = ctypes.WinDLL(
            "shcore",
            use_last_error=True
        )

        shcore.SetProcessDpiAwareness.argtypes = [
            ctypes.c_int
        ]

        shcore.SetProcessDpiAwareness.restype = (
            ctypes.c_long
        )

        # PROCESS_PER_MONITOR_DPI_AWARE
        result = shcore.SetProcessDpiAwareness(2)

        if result == 0:
            return True

    except Exception:
        pass

    # Older Windows
    try:

        user32.SetProcessDPIAware()

        return True

    except Exception:
        return False


# Run before importing Qt.
enable_dpi_awareness()


# ============================================================
# WINDOWS STRUCTURES
# ============================================================

class RECT(ctypes.Structure):

    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


# ============================================================
# CONSTANTS
# ============================================================

GA_ROOT = 2

GWL_EXSTYLE = -20

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


# Windows that should not become platforms.
IGNORED_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
}


# ============================================================
# API DEFINITIONS
# ============================================================

EnumWindowsProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM
)


user32.EnumWindows.argtypes = [
    EnumWindowsProc,
    wintypes.LPARAM
]

user32.EnumWindows.restype = wintypes.BOOL


user32.IsWindow.argtypes = [
    wintypes.HWND
]

user32.IsWindow.restype = wintypes.BOOL


user32.IsWindowVisible.argtypes = [
    wintypes.HWND
]

user32.IsWindowVisible.restype = wintypes.BOOL


user32.IsIconic.argtypes = [
    wintypes.HWND
]

user32.IsIconic.restype = wintypes.BOOL


user32.IsZoomed.argtypes = [
    wintypes.HWND
]

user32.IsZoomed.restype = wintypes.BOOL


user32.GetAncestor.argtypes = [
    wintypes.HWND,
    wintypes.UINT
]

user32.GetAncestor.restype = wintypes.HWND


user32.GetWindowLongW.argtypes = [
    wintypes.HWND,
    ctypes.c_int
]

user32.GetWindowLongW.restype = ctypes.c_long


user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD)
]

user32.GetWindowThreadProcessId.restype = (
    wintypes.DWORD
)


user32.GetClassNameW.argtypes = [
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int
]

user32.GetClassNameW.restype = ctypes.c_int


user32.GetWindowTextLengthW.argtypes = [
    wintypes.HWND
]

user32.GetWindowTextLengthW.restype = ctypes.c_int


user32.GetWindowTextW.argtypes = [
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int
]

user32.GetWindowTextW.restype = ctypes.c_int


user32.GetWindowRect.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(RECT)
]

user32.GetWindowRect.restype = wintypes.BOOL


user32.GetDpiForWindow.argtypes = [
    wintypes.HWND
]

user32.GetDpiForWindow.restype = wintypes.UINT


dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD
]

# HRESULT = signed 32-bit integer
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


# ============================================================
# DATA CLASSES
# ============================================================

class WindowInfo:

    __slots__ = (
        "hwnd",
        "left",
        "top",
        "right",
        "bottom",
        "title",
        "class_name",
        "maximized",
        "z_index",
    )

    def __init__(
        self,
        hwnd,
        left,
        top,
        right,
        bottom,
        title,
        class_name,
        maximized,
        z_index
    ):

        self.hwnd = hwnd

        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

        self.title = title
        self.class_name = class_name

        self.maximized = maximized
        self.z_index = z_index

    @property
    def rect(self):

        return (
            self.left,
            self.top,
            self.right,
            self.bottom
        )


class Platform:

    __slots__ = (
        "x1",
        "x2",
        "y",
        "side",
        "hwnd",
    )

    def __init__(
        self,
        x1,
        x2,
        y,
        side,
        hwnd
    ):

        self.x1 = x1
        self.x2 = x2
        self.y = y
        self.side = side
        self.hwnd = hwnd

    @property
    def rect(self):

        return (
            self.x1,
            self.y,
            self.x2,
            self.y
        )

    def as_tuple(self):

        return (
            self.x1,
            self.x2,
            self.y,
            self.side,
            self.hwnd
        )

    def as_dict(self):

        return {
            "x1": self.x1,
            "x2": self.x2,
            "y": self.y,
            "side": self.side,
            "hwnd": self.hwnd,
        }


# ============================================================
# WINDOWS DETECTOR
# ============================================================

class WindowDetector:

    def __init__(self):

        self.process_id = os.getpid()

    # --------------------------------------------------------
    # WINDOW TITLE
    # --------------------------------------------------------

    @staticmethod
    def get_title(hwnd):

        length = user32.GetWindowTextLengthW(
            hwnd
        )

        if length <= 0:
            return ""

        buffer = ctypes.create_unicode_buffer(
            length + 1
        )

        user32.GetWindowTextW(
            hwnd,
            buffer,
            length + 1
        )

        return buffer.value

    # --------------------------------------------------------
    # WINDOW CLASS
    # --------------------------------------------------------

    @staticmethod
    def get_class(hwnd):

        buffer = ctypes.create_unicode_buffer(
            256
        )

        user32.GetClassNameW(
            hwnd,
            buffer,
            256
        )

        return buffer.value

    # --------------------------------------------------------
    # PROCESS ID
    # --------------------------------------------------------

    @staticmethod
    def get_process_id(hwnd):

        pid = wintypes.DWORD()

        user32.GetWindowThreadProcessId(
            hwnd,
            ctypes.byref(pid)
        )

        return pid.value

    # --------------------------------------------------------
    # DPI
    # --------------------------------------------------------

    @staticmethod
    def get_dpi(hwnd):

        dpi = user32.GetDpiForWindow(
            hwnd
        )

        if not dpi:
            return 96

        return dpi

    # --------------------------------------------------------
    # DWM BOUNDS
    # --------------------------------------------------------

    @staticmethod
    def get_dwm_bounds(hwnd):

        rect = RECT()

        result = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect)
        )

        if result == 0:

            return (
                rect.left,
                rect.top,
                rect.right,
                rect.bottom
            )

        # Fallback.
        if user32.GetWindowRect(
            hwnd,
            ctypes.byref(rect)
        ):

            return (
                rect.left,
                rect.top,
                rect.right,
                rect.bottom
            )

        return None

    # --------------------------------------------------------
    # CLOAKED
    # --------------------------------------------------------

    @staticmethod
    def is_cloaked(hwnd):

        value = wintypes.DWORD()

        result = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(value),
            ctypes.sizeof(value)
        )

        if result != 0:
            return False

        return value.value != 0

    # --------------------------------------------------------
    # VALID WINDOW
    # --------------------------------------------------------

    def valid_window(self, hwnd):

        if not hwnd:
            return False

        if not user32.IsWindow(hwnd):
            return False

        # Don't detect Lumi's own windows.
        if self.get_process_id(hwnd) == self.process_id:
            return False

        # Invisible.
        if not user32.IsWindowVisible(hwnd):
            return False

        # Minimized.
        if user32.IsIconic(hwnd):
            return False

        # DWM-cloaked.
        if self.is_cloaked(hwnd):
            return False

        # Must be top-level/root window.
        if user32.GetAncestor(
            hwnd,
            GA_ROOT
        ) != hwnd:

            return False

        class_name = self.get_class(
            hwnd
        )

        if class_name in IGNORED_CLASSES:
            return False

        # Ignore normal tool windows.
        style = user32.GetWindowLongW(
            hwnd,
            GWL_EXSTYLE
        )

        if style & WS_EX_TOOLWINDOW:

            if not (
                style & WS_EX_APPWINDOW
            ):

                return False

        return True

    # --------------------------------------------------------
    # ENUMERATE WINDOWS
    # --------------------------------------------------------

    def enumerate(self):

        windows = []

        @EnumWindowsProc
        def callback(hwnd, lparam):

            if not self.valid_window(hwnd):
                return True

            bounds = self.get_dwm_bounds(
                hwnd
            )

            if bounds is None:
                return True

            left, top, right, bottom = bounds

            if right <= left:
                return True

            if bottom <= top:
                return True

            # Ignore tiny windows.
            if right - left < 30:
                return True

            if bottom - top < 20:
                return True

            windows.append(
                WindowInfo(
                    hwnd=int(hwnd),

                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,

                    title=self.get_title(hwnd),

                    class_name=self.get_class(
                        hwnd
                    ),

                    maximized=bool(
                        user32.IsZoomed(hwnd)
                    ),

                    z_index=len(windows)
                )
            )

            return True

        user32.EnumWindows(
            callback,
            0
        )

        return windows


# ============================================================
# RECTANGLE GEOMETRY
# ============================================================

def rect_intersection(a, b):

    x1 = max(
        a[0],
        b[0]
    )

    y1 = max(
        a[1],
        b[1]
    )

    x2 = min(
        a[2],
        b[2]
    )

    y2 = min(
        a[3],
        b[3]
    )

    if x2 <= x1:
        return None

    if y2 <= y1:
        return None

    return (
        x1,
        y1,
        x2,
        y2
    )


def subtract_rect(
    source,
    blocker
):
    """
    Subtract blocker from source.

    Returns the remaining visible rectangles.
    """

    hit = rect_intersection(
        source,
        blocker
    )

    if hit is None:
        return [source]

    sx1, sy1, sx2, sy2 = source
    bx1, by1, bx2, by2 = hit

    pieces = []

    # Above blocker.
    if sy1 < by1:

        pieces.append(
            (
                sx1,
                sy1,
                sx2,
                by1
            )
        )

    # Below blocker.
    if by2 < sy2:

        pieces.append(
            (
                sx1,
                by2,
                sx2,
                sy2
            )
        )

    # Left.
    if sx1 < bx1:

        pieces.append(
            (
                sx1,
                by1,
                bx1,
                by2
            )
        )

    # Right.
    if bx2 < sx2:

        pieces.append(
            (
                bx2,
                by1,
                sx2,
                by2
            )
        )

    return pieces


# ============================================================
# OCCLUSION ENGINE
# ============================================================

class OcclusionEngine:

    def __init__(
        self,
        max_fragments=256
    ):

        self.max_fragments = (
            max_fragments
        )

    def calculate(
        self,
        windows
    ):
        """
        Determine which parts of each window are exposed.

        Windows are processed in Z-order.
        """

        visible = []

        blockers = []

        for window in windows:

            pieces = [
                window.rect
            ]

            for blocker in blockers:

                new_pieces = []

                for piece in pieces:

                    new_pieces.extend(
                        subtract_rect(
                            piece,
                            blocker
                        )
                    )

                    if (
                        len(new_pieces)
                        >= self.max_fragments
                    ):
                        break

                pieces = new_pieces

                if not pieces:
                    break

            if pieces:

                visible.append(
                    (
                        window,
                        pieces
                    )
                )

            # This window blocks windows below it.
            blockers.append(
                window.rect
            )

        return visible


# ============================================================
# PLATFORM GENERATOR
# ============================================================

class PlatformGenerator:

    def __init__(
        self,
        minimum_width=20
    ):

        self.minimum_width = (
            minimum_width
        )

    def generate(
        self,
        visible_windows
    ):
        """
        Generate ONLY REAL window borders.

        Top platform:
            piece.top == window.top

        Bottom platform:
            piece.bottom == window.bottom

        This is important.

        We do NOT treat artificial occlusion edges as
        platforms.
        """

        platforms = []

        for window, pieces in visible_windows:

            for x1, y1, x2, y2 in pieces:

                if (
                    x2 - x1
                    < self.minimum_width
                ):
                    continue

                # =================================================
                # TOP BORDER
                # =================================================

                if y1 == window.top:

                    platforms.append(
                        Platform(
                            x1=x1,
                            x2=x2,
                            y=window.top,
                            side="top",
                            hwnd=window.hwnd
                        )
                    )

                # =================================================
                # BOTTOM BORDER
                # =================================================

                if y2 == window.bottom:

                    platforms.append(
                        Platform(
                            x1=x1,
                            x2=x2,
                            y=window.bottom,
                            side="bottom",
                            hwnd=window.hwnd
                        )
                    )

        return self.merge_platforms(
            platforms
        )

    # --------------------------------------------------------

    def merge_platforms(
        self,
        platforms
    ):

        if not platforms:
            return []

        platforms.sort(
            key=lambda p: (
                p.hwnd,
                p.side,
                p.y,
                p.x1
            )
        )

        merged = []

        current = platforms[0]

        for platform in platforms[1:]:

            same_window = (
                platform.hwnd
                == current.hwnd
            )

            same_side = (
                platform.side
                == current.side
            )

            same_y = (
                platform.y
                == current.y
            )

            touching = (
                platform.x1
                <= current.x2 + 4
            )

            if (
                same_window
                and same_side
                and same_y
                and touching
            ):

                current = Platform(
                    x1=current.x1,
                    x2=max(
                        current.x2,
                        platform.x2
                    ),
                    y=current.y,
                    side=current.side,
                    hwnd=current.hwnd
                )

            else:

                merged.append(
                    current
                )

                current = platform

        merged.append(
            current
        )

        return merged


# ============================================================
# MAIN PUBLIC API
# ============================================================

class WindowPlatformDetector:

    def __init__(
        self,
        poll_interval=0.10,
        minimum_platform_width=20
    ):
        """
        poll_interval:
            Minimum time between Windows scans.

            0.10 = 10 scans/sec

        This can safely be called from a 60 FPS physics loop.
        """

        self.detector = (
            WindowDetector()
        )

        self.occlusion = (
            OcclusionEngine()
        )

        self.platform_generator = (
            PlatformGenerator(
                minimum_width=minimum_platform_width
            )
        )

        self.poll_interval = (
            poll_interval
        )

        self.windows = []

        self.visible_windows = []

        self.platforms = []

        self.last_signature = None

        self.last_update_time = 0.0

    # ========================================================
    # UPDATE
    # ========================================================

    def update(
        self,
        force=False
    ):
        """
        Refresh the detected windows.

        Returns:
            True  = detector performed a new scan
            False = previous result is still valid

        Example:

            detector.update()

        or:

            detector.update(force=True)
        """

        now = time.monotonic()

        if not force:

            if (
                now
                - self.last_update_time
                < self.poll_interval
            ):

                return False

        self.last_update_time = now

        windows = (
            self.detector.enumerate()
        )

        # ----------------------------------------------------
        # Detect geometry/Z-order changes.
        # ----------------------------------------------------

        signature = tuple(
            (
                w.hwnd,

                w.left,
                w.top,
                w.right,
                w.bottom,

                w.maximized,

                w.z_index
            )
            for w in windows
        )

        # Nothing changed.
        if (
            signature
            == self.last_signature
        ):

            self.windows = windows

            return True

        self.last_signature = signature

        self.windows = windows

        # ----------------------------------------------------
        # Occlusion.
        # ----------------------------------------------------

        self.visible_windows = (
            self.occlusion.calculate(
                windows
            )
        )

        # ----------------------------------------------------
        # Platforms.
        # ----------------------------------------------------

        self.platforms = (
            self.platform_generator.generate(
                self.visible_windows
            )
        )

        return True

    # ========================================================
    # GET WINDOW BOUNDING BOXES
    # ========================================================

    def get_bounding_boxes(
        self,
        visible_only=False
    ):
        """
        Return window bounding boxes.

        Default:
            all detected windows.

        Format:

            [
                {
                    "hwnd": 12345,
                    "x1": 100,
                    "y1": 200,
                    "x2": 800,
                    "y2": 700,
                    "width": 700,
                    "height": 500,
                    "title": "...",
                    "maximized": False
                },
                ...
            ]

        Coordinates are Windows screen coordinates.
        """

        if not visible_only:

            return [
                {
                    "hwnd": w.hwnd,

                    "x1": w.left,
                    "y1": w.top,

                    "x2": w.right,
                    "y2": w.bottom,

                    "width": (
                        w.right
                        - w.left
                    ),

                    "height": (
                        w.bottom
                        - w.top
                    ),

                    "title": w.title,

                    "class_name": (
                        w.class_name
                    ),

                    "maximized": (
                        w.maximized
                    ),

                    "z_index": (
                        w.z_index
                    )
                }

                for w in self.windows
            ]

        # Visible fragments only.
        result = []

        for window, pieces in (
            self.visible_windows
        ):

            for x1, y1, x2, y2 in pieces:

                result.append(
                    {
                        "hwnd": window.hwnd,

                        "x1": x1,
                        "y1": y1,

                        "x2": x2,
                        "y2": y2,

                        "width": x2 - x1,
                        "height": y2 - y1,

                        "title": window.title,

                        "class_name": (
                            window.class_name
                        ),

                        "maximized": (
                            window.maximized
                        ),

                        "z_index": (
                            window.z_index
                        )
                    }
                )

        return result

    # ========================================================
    # GET PLATFORMS
    # ========================================================

    def get_platforms(
        self,
        as_dict=True
    ):
        """
        Return usable window platforms.

        Each platform is either:

            side = "top"

        or:

            side = "bottom"

        Example:

            [
                {
                    "x1": 100,
                    "x2": 900,
                    "y": 500,
                    "side": "top",
                    "hwnd": 12345
                },

                {
                    "x1": 100,
                    "x2": 900,
                    "y": 900,
                    "side": "bottom",
                    "hwnd": 12345
                }
            ]
        """

        if as_dict:

            return [
                p.as_dict()
                for p in self.platforms
            ]

        return [
            p
            for p in self.platforms
        ]

    # ========================================================
    # GET VISIBLE WINDOWS
    # ========================================================

    def get_visible_boxes(self):

        result = []

        for window, pieces in (
            self.visible_windows
        ):

            for x1, y1, x2, y2 in pieces:

                result.append(
                    (
                        x1,
                        y1,
                        x2,
                        y2,
                        window.hwnd
                    )
                )

        return result

    # ========================================================
    # GET SIMPLE PLATFORM TUPLES
    # ========================================================

    def get_platform_tuples(self):

        """
        Lightweight version for physics.

        Returns:

            [
                (x1, x2, y, side, hwnd),
                ...
            ]
        """

        return [
            p.as_tuple()
            for p in self.platforms
        ]

    # ========================================================
    # FORCE REFRESH
    # ========================================================

    def refresh(self):

        self.update(
            force=True
        )

        return self.get_platforms()

    # ========================================================
    # DEBUG OVERLAY
    # ========================================================

    def run_debug(self):

        """
        Optional visual debugger.

        This is NOT required when importing the module into
        LumiModel.py.
        """

        from PyQt6.QtCore import (
            Qt,
            QTimer
        )

        from PyQt6.QtGui import (
            QPainter,
            QPen
        )

        from PyQt6.QtWidgets import (
            QApplication,
            QWidget
        )

        detector = self

        class DebugOverlay(QWidget):

            def __init__(self):

                super().__init__()

                self.setWindowTitle(
                    "Lumi Window Detector"
                )

                self.setWindowFlags(
                    Qt.WindowType.FramelessWindowHint
                    |
                    Qt.WindowType.WindowStaysOnTopHint
                    |
                    Qt.WindowType.Tool
                    |
                    Qt.WindowType.WindowTransparentForInput
                )

                self.setAttribute(
                    Qt.WidgetAttribute.WA_TranslucentBackground,
                    True
                )

                self.setAttribute(
                    Qt.WidgetAttribute.WA_ShowWithoutActivating,
                    True
                )

                self.windows = []
                self.platforms = []

                self.refresh_geometry()

                self.show()

            # --------------------------------------------

            def refresh_geometry(self):

                app = QApplication.instance()

                screens = app.screens()

                if not screens:
                    return

                left = None
                top = None
                right = None
                bottom = None

                for screen in screens:

                    g = screen.geometry()

                    if left is None:

                        left = g.left()
                        top = g.top()
                        right = g.right()
                        bottom = g.bottom()

                    else:

                        left = min(
                            left,
                            g.left()
                        )

                        top = min(
                            top,
                            g.top()
                        )

                        right = max(
                            right,
                            g.right()
                        )

                        bottom = max(
                            bottom,
                            g.bottom()
                        )

                self.qt_x = left
                self.qt_y = top

                self.qt_width = (
                    right
                    - left
                    + 1
                )

                self.qt_height = (
                    bottom
                    - top
                    + 1
                )

                self.setGeometry(
                    self.qt_x,
                    self.qt_y,
                    self.qt_width,
                    self.qt_height
                )

            # --------------------------------------------

            def physical_to_qt(
                self,
                x,
                y,
                hwnd
            ):

                dpi = user32.GetDpiForWindow(
                    hwnd
                )

                if not dpi:
                    dpi = 96

                scale = dpi / 96.0

                return (
                    round(x / scale),
                    round(y / scale)
                )

            # --------------------------------------------

            def paintEvent(self, event):

                painter = QPainter(self)

                painter.setRenderHint(
                    QPainter.RenderHint.Antialiasing,
                    False
                )

                # ========================================
                # ACTUAL WINDOW BOUNDS
                # ========================================

                green = QPen(
                    Qt.GlobalColor.green
                )

                green.setWidth(2)

                painter.setPen(
                    green
                )

                for window in (
                    detector.windows
                ):

                    x1, y1 = (
                        self.physical_to_qt(
                            window.left,
                            window.top,
                            window.hwnd
                        )
                    )

                    x2, y2 = (
                        self.physical_to_qt(
                            window.right,
                            window.bottom,
                            window.hwnd
                        )
                    )

                    x1 -= self.qt_x
                    y1 -= self.qt_y

                    x2 -= self.qt_x
                    y2 -= self.qt_y

                    painter.drawRect(
                        x1,
                        y1,
                        x2 - x1,
                        y2 - y1
                    )

                # ========================================
                # PLATFORM BORDERS
                # ========================================

                red = QPen(
                    Qt.GlobalColor.red
                )

                red.setWidth(3)

                painter.setPen(
                    red
                )

                for platform in (
                    detector.platforms
                ):

                    x1, y1 = (
                        self.physical_to_qt(
                            platform.x1,
                            platform.y,
                            platform.hwnd
                        )
                    )

                    x2, y2 = (
                        self.physical_to_qt(
                            platform.x2,
                            platform.y,
                            platform.hwnd
                        )
                    )

                    x1 -= self.qt_x
                    y1 -= self.qt_y

                    x2 -= self.qt_x
                    y2 -= self.qt_y

                    painter.drawLine(
                        x1,
                        y1,
                        x2,
                        y2
                    )

                painter.end()

        app = QApplication(
            sys.argv
        )

        overlay = DebugOverlay()

        timer = QTimer()

        timer.setInterval(
            100
        )

        def update():

            detector.update()

            overlay.windows = (
                detector.windows
            )

            overlay.platforms = (
                detector.platforms
            )

            overlay.refresh_geometry()

            overlay.update()

            print(
                "\r"
                f"Windows: "
                f"{len(detector.windows):3d} | "
                f"Platforms: "
                f"{len(detector.platforms):3d}",
                end="",
                flush=True
            )

        timer.timeout.connect(
            update
        )

        timer.start()

        update()

        sys.exit(
            app.exec()
        )


# ============================================================
# STANDALONE DEBUG MODE
# ============================================================

def main():

    if sys.platform != "win32":

        raise RuntimeError(
            "windowDetector.py requires Windows."
        )

    detector = WindowPlatformDetector(
        poll_interval=0.10
    )

    detector.run_debug()


if __name__ == "__main__":

    main()