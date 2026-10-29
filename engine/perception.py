"""
engine/perception.py  — AlphaVision Perception Engine
======================================================
Asynchronous sensory loop running on a background daemon thread.

Sensors:
  • Vision  — MediaPipe FaceMesh (face orientation, eye-gaze, fatigue cues, presence).
              Falls back to a lightweight OpenCV Haar-cascade detector when MediaPipe
              is unavailable, so the system always degrades gracefully.
  • Desktop — Active window title via win32gui, idle time via win32api.GetLastInputInfo,
              local clock context (late-night, work-hours, etc.).

Emits PerceptionSnapshot dataclass objects to brain.py via a thread-safe queue.
No raw frames are ever forwarded externally; only computed state deltas.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import threading
import queue
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("alphavision.perception")

# ---------------------------------------------------------------------------
# Optional heavy imports — degrade gracefully if not installed or if the
# module is a test stub that lacks `.solutions`.
# ---------------------------------------------------------------------------
try:
    import mediapipe as mp
    if hasattr(mp, "solutions") and hasattr(mp.solutions, "face_mesh"):
        _mp_face_mesh = mp.solutions.face_mesh
        _mp_drawing = getattr(mp.solutions, "drawing_utils", None)
        _MP_AVAILABLE = True
    else:
        try:
            from mediapipe.python.solutions import face_mesh as _mp_face_mesh
            from mediapipe.python.solutions import drawing_utils as _mp_drawing
            _MP_AVAILABLE = True
        except (ImportError, ModuleNotFoundError):
            _MP_AVAILABLE = False
            _mp_face_mesh = None   # type: ignore
            _mp_drawing   = None   # type: ignore
            logger.info("[PERCEPTION] MediaPipe solutions namespace not found — using Haar-cascade presence detection.")
except Exception as e:
    _MP_AVAILABLE = False
    _mp_face_mesh = None   # type: ignore
    _mp_drawing   = None   # type: ignore
    logger.info(f"[PERCEPTION] MediaPipe unavailable ({e}) — using Haar-cascade presence detection.")

try:
    import win32gui
    import win32api
    _WIN32_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _WIN32_AVAILABLE = False
    logger.warning("[PERCEPTION] pywin32 not available — desktop sensor will use fallback stubs.")


# ---------------------------------------------------------------------------
# Window categorization tags and priority rules
# Specific web services & media apps MUST be evaluated before generic browsers!
# ---------------------------------------------------------------------------
_WINDOW_TAGS: dict[str, str] = {
    # 1. Specific media & web services (must precede generic browser names like chrome/edge)
    "youtube": "youtube",
    "netflix": "netflix",
    "spotify": "music",
    "twitch": "gaming",
    "crunchyroll": "anime",

    # 2. Coding & Development
    "code": "coding",
    "vscode": "coding",
    "visual studio": "coding",
    "pycharm": "coding",
    "sublime": "coding",
    "vim": "coding",
    "neovim": "coding",

    # 2b. Physics & Science (must precede generic browser names)
    "physics": "physics",
    "quantum": "physics",
    "mechanics": "physics",
    "arxiv": "physics",
    "desmos": "physics",
    "geogebra": "physics",
    "mathematica": "physics",
    "matlab": "physics",

    # 2c. PDF, Books & Document Reading (must precede generic browsers)
    "book.pdf": "reading",
    ".pdf": "reading",
    "pdf": "reading",
    "adobe": "reading",
    "acrobat": "reading",
    "foxit": "reading",
    "sumatra": "reading",
    "reader": "reading",
    "kindle": "reading",
    "epub": "reading",

    # 2d. Math & Academic Study (must precede generic browsers)
    "maths": "study",
    "math": "study",
    "calculus": "study",
    "algebra": "study",
    "geometry": "study",
    "cbse": "study",
    "ncert": "study",
    "chemistry": "study",
    "biology": "study",
    "khan academy": "study",

    # 3. Gaming
    "valorant": "gaming",
    "steam": "gaming",
    "game": "gaming",

    # 4. Communication & Social
    "discord": "social",
    "slack": "social",
    "teams": "social",

    # 5. Terminal & Shell
    "terminal": "terminal",
    "cmd": "terminal",
    "powershell": "terminal",

    # 6. Writing & Docs
    "notepad": "writing",
    "word": "writing",
    "docs": "writing",

    # 7. Generic browsers (evaluated LAST so e.g. "YouTube - Chrome" -> "youtube")
    "chrome": "browsing",
    "firefox": "browsing",
    "edge": "browsing",
    "brave": "browsing",
    "opera": "browsing",
}


def tag_window(title: str) -> str:
    """Categorizes active window title into activity tags with strict precedence."""
    if not title:
        return "misc"
    title_low = title.lower()
    for kw, tag in _WINDOW_TAGS.items():
        if kw in title_low:
            return tag
    return "misc"


_tag_window = tag_window


# ---------------------------------------------------------------------------
# Chat Box Detection & Hysteresis Debouncer
# ---------------------------------------------------------------------------
class ChatBoxDebouncer:
    """
    Two-way hysteresis state confirmation filter for chat box presence.
    Prevents rapid flip-flop flickering caused by antialiasing, cursor blinking,
    or transient overlay popups.

    Requires:
      - At least `open_confirm_sec` (default 0.6s) of continuous positive detections
        before transitioning to OPEN.
      - At least `close_confirm_sec` (default 1.0s) of continuous negative detections
        before transitioning to CLOSED.
      - At least `min_state_dwell_sec` (default 1.0s) between state flips.
    """
    def __init__(
        self,
        open_confirm_sec: float = 0.60,
        close_confirm_sec: float = 1.00,
        min_state_dwell_sec: float = 1.00,
        initial_state: bool = True,
    ):
        self.open_confirm_sec = open_confirm_sec
        self.close_confirm_sec = close_confirm_sec
        self.min_state_dwell_sec = min_state_dwell_sec

        self.is_open: bool = initial_state
        self._raw_streak_state: bool = initial_state
        self._raw_streak_start: float = time.time()
        self._last_state_change: float = time.time()

    def update(self, raw_detected: bool, now: Optional[float] = None) -> Tuple[bool, bool]:
        """
        Feeds a raw sensor detection sample.
        Returns: (current_debounced_state, state_just_changed)
        """
        if now is None:
            now = time.time()

        if raw_detected != self._raw_streak_state:
            self._raw_streak_state = raw_detected
            self._raw_streak_start = now

        streak_duration = now - self._raw_streak_start
        dwell_duration = now - self._last_state_change
        state_changed = False

        if dwell_duration >= self.min_state_dwell_sec:
            if not self.is_open and raw_detected:
                if streak_duration >= self.open_confirm_sec:
                    self.is_open = True
                    self._last_state_change = now
                    state_changed = True
            elif self.is_open and not raw_detected:
                if streak_duration >= self.close_confirm_sec:
                    self.is_open = False
                    self._last_state_change = now
                    state_changed = True

        return self.is_open, state_changed


def detect_opaque_input_box(screen_bgr: np.ndarray) -> bool:
    """
    Detects MateEngine's 100% opaque white 'Talk to me!' input box anywhere on screen.
    Tolerates cursor blinking, font antialiasing, and aspect ratio jitter.
    Ignores semi-transparent dark bubbles and top titlebars.
    """
    try:
        H, W = screen_bgr.shape[:2]
        gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

        # Opaque white UI mask (240+ catches full white regardless of monitor gamma)
        _, white_mask = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)

        # Bridge across the grey 'Talk to me!' letters inside the box
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (18, 6))
        closed_mask = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Resolution-independent size bounds
        min_w, max_w = int(W * 0.12), int(W * 0.45)
        min_h, max_h = int(H * 0.03), int(H * 0.15)

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)

            # Ignore elements in the top 15% of the screen (window titlebars, browser tabs)
            if y < int(H * 0.15):
                continue

            if min_w <= w <= max_w and min_h <= h <= max_h:
                aspect = w / float(h)
                # Relaxed aspect bounds (2.2 to 9.0) prevents boundary jitter
                if not (2.2 <= aspect <= 9.0):
                    continue

                # 1. Pill solidity check
                roi_closed = closed_mask[y:y+h, x:x+w]
                if (cv2.countNonZero(roi_closed) / float(w * h)) < 0.75:
                    continue

                # 2. Bottom-right quadrant MUST be flat, unshaded white
                br_roi = gray[y + int(h * 0.50) : y + int(h * 0.85), x + int(w * 0.45) : x + int(w * 0.90)]
                if br_roi.size == 0:
                    continue
                if np.mean(br_roi) < 235 or np.std(br_roi) > 9.0:
                    continue

                # 3. Top-left quadrant has text or placeholder pixels (< 220)
                tl_roi = gray[y + int(h * 0.15) : y + int(h * 0.55), x + int(w * 0.04) : x + int(w * 0.45)]
                if tl_roi.size == 0:
                    continue
                text_pixels = np.sum(tl_roi < 220)
                if text_pixels >= 4:
                    return True

        return False
    except Exception as e:
        logger.warning("[SENSOR WARN] detect_opaque_input_box: %s", e)
        return True


# ---------------------------------------------------------------------------
# Flicker-Free Desktop Screen Grabber (Anti-DWM Contention & Strict DC Cleanup)
# ---------------------------------------------------------------------------
# Globally configure mss to suppress CAPTUREBLT so Windows DWM never hides the cursor
try:
    import mss
    if hasattr(mss, "windows"):
        setattr(mss.windows, "CAPTUREBLT", 0)
    try:
        import mss.windows.gdi
        setattr(mss.windows.gdi, "CAPTUREBLT", 0)
    except Exception:
        pass
except Exception:
    pass


def capture_screen_gdi(left: int = 0, top: int = 0, width: int = 0, height: int = 0) -> Optional[np.ndarray]:
    """
    Captures the desktop screen using Win32 GDI BitBlt strictly without CAPTUREBLT.
    Ensures every GetDC/GetWindowDC is strictly paired with ReleaseDC, DeleteDC,
    and DeleteObject in a finally block to eliminate GDI handle leaks and DWM contention.
    """
    if not _WIN32_AVAILABLE:
        return None

    hwnd_target = 0
    hdc_screen = None
    hdc_mem = None
    hbitmap = None
    old_bitmap = None

    try:
        import win32gui, win32ui, win32con
        hwnd_target = win32gui.GetDesktopWindow()
        if width <= 0 or height <= 0:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd_target)
            width = right - left
            height = bottom - top

        hdc_screen = win32gui.GetWindowDC(hwnd_target)
        if not hdc_screen:
            return None

        mfc_screen = win32ui.CreateDCFromHandle(hdc_screen)
        hdc_mem = mfc_screen.CreateCompatibleDC()

        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_screen, width, height)
        hbitmap = save_bitmap.GetHandle()
        old_bitmap = hdc_mem.SelectObject(save_bitmap)

        # Strictly use SRCCOPY (0x00CC0020) without CAPTUREBLT (0x40000000)
        # to ensure Windows DWM never hides or redraws the mouse pointer
        hdc_mem.BitBlt((0, 0), (width, height), mfc_screen, (left, top), win32con.SRCCOPY)

        bmp_info = save_bitmap.GetInfo()
        bmp_str = save_bitmap.GetBitmapBits(True)
        img_np = np.frombuffer(bmp_str, dtype=np.uint8).reshape((bmp_info['bmHeight'], bmp_info['bmWidth'], 4))
        return cv2.cvtColor(img_np, cv2.COLOR_BGRA2BGR)
    except Exception as exc:
        logger.debug("[PERCEPTION] GDI screen capture fallback: %s", exc)
        return None
    finally:
        # Strict Win32 DC and GDI Object cleanup
        if hdc_mem is not None:
            try:
                if old_bitmap is not None:
                    hdc_mem.SelectObject(old_bitmap)
                hdc_mem.DeleteDC()
            except Exception:
                pass
        if hbitmap is not None:
            try:
                import win32gui
                win32gui.DeleteObject(hbitmap)
            except Exception:
                pass
        if hdc_screen is not None and hwnd_target is not None:
            try:
                import win32gui
                win32gui.ReleaseDC(hwnd_target, hdc_screen)
            except Exception:
                pass


class SafeScreenGrabber:
    """
    High-performance, non-flickering desktop screen grabber.
    Guarantees:
      - CAPTUREBLT flag disabled (SRCCOPY only), preventing mouse pointer flickering.
      - Throttled capture rate (default 0.65s between captures = ~1.5 FPS max).
      - Reactive window-focus gate: triggers immediate capture when active
        window title or foreground HWND changes.
      - Safe fallback to GDI or None if desktop DC is locked or headless.
    """
    def __init__(self, min_interval_sec: float = 0.65):
        self.min_interval_sec = min_interval_sec
        self._last_grab_time = 0.0
        self._last_foreground_hwnd = 0
        self._sct = None
        self._lock = threading.Lock()

        # Suppress CAPTUREBLT flag in mss globally
        try:
            import mss
            if hasattr(mss, "windows"):
                setattr(mss.windows, "CAPTUREBLT", 0)
            try:
                import mss.windows.gdi
                setattr(mss.windows.gdi, "CAPTUREBLT", 0)
            except Exception:
                pass
            sct_cls = getattr(mss, "MSS", mss.mss)
            self._sct = sct_cls()
        except Exception as exc:
            logger.debug("[PERCEPTION] mss init in SafeScreenGrabber: %s", exc)

    def should_grab(self, now: Optional[float] = None) -> bool:
        """
        Determines whether a screen grab is warranted based on:
          1) Foreground window change (user switched apps), OR
          2) At least `min_interval_sec` (500ms-1000ms) elapsed since last grab.
        """
        if now is None:
            now = time.time()

        curr_hwnd = 0
        if _WIN32_AVAILABLE:
            try:
                curr_hwnd = win32gui.GetForegroundWindow()
            except Exception:
                curr_hwnd = 0

        if curr_hwnd and curr_hwnd != self._last_foreground_hwnd:
            self._last_foreground_hwnd = curr_hwnd
            return True

        return (now - self._last_grab_time) >= self.min_interval_sec

    def grab_bgr(self, force: bool = False) -> Optional[np.ndarray]:
        """
        Captures the primary monitor into a BGR numpy array without cursor flickering.
        Returns None if throttled or if capture fails.
        """
        now = time.time()
        if not force and not self.should_grab(now):
            return None

        with self._lock:
            self._last_grab_time = now
            # 1. Attempt mss with CAPTUREBLT=0 first (highest performance)
            if self._sct is not None:
                try:
                    monitor = self._sct.monitors[1] if len(self._sct.monitors) > 1 else self._sct.monitors[0]
                    sct_img = self._sct.grab(monitor)
                    img_np = np.array(sct_img)
                    return cv2.cvtColor(img_np, cv2.COLOR_BGRA2BGR)
                except Exception as e:
                    logger.debug("[PERCEPTION] sct.grab failed (switching to GDI): %s", e)

            # 2. Fallback to direct GDI BitBlt with strict DC cleanup
            return capture_screen_gdi()

    def close(self):
        with self._lock:
            if self._sct is not None:
                try:
                    self._sct.close()
                except Exception:
                    pass
                self._sct = None


# ---------------------------------------------------------------------------
# Public snapshot type consumed by brain.py
# ---------------------------------------------------------------------------
@dataclass
class PerceptionSnapshot:
    """One instantaneous reading from all sensors."""
    timestamp: float = field(default_factory=time.time)

    # Vision
    face_detected: bool = False
    eye_contact: bool = False          # Both eyes visible and roughly on-screen
    head_pitch: float = 0.0            # Degrees; negative = looking down
    head_yaw: float = 0.0              # Degrees; negative = looking away
    fatigue_cue: bool = False          # Eye-rub / heavy blink pattern
    presence: bool = True              # Is the user at the desk?

    # Desktop
    active_window: str = ""
    idle_seconds: float = 0.0

    # Time context
    hour: int = 0                      # Local 24h hour
    is_late_night: bool = False        # 22:00–05:00
    is_work_hours: bool = False        # 09:00–18:00

    # Snapshot blob (downscaled JPEG bytes) — only set on major state changes
    frame_blob: Optional[bytes] = None  # raw JPEG bytes, never the full frame


# ---------------------------------------------------------------------------
# Landmark indices (MediaPipe FaceMesh 468-point model)
# ---------------------------------------------------------------------------
# Nose tip: 4 | Left eye outer: 33 | Right eye outer: 263
# Left iris approx centre: 468 | Right iris approx centre: 473
_NOSE_TIP   = 4
_L_EYE_OUT  = 33
_R_EYE_OUT  = 263
_L_EYE_IN   = 133
_R_EYE_IN   = 362
_CHIN       = 152
_FOREHEAD   = 10


def _estimate_head_pose(landmarks, iw: int, ih: int) -> Tuple[float, float]:
    """
    Rough pitch / yaw from 2-D landmark geometry.
    Returns (pitch_deg, yaw_deg) — no solvePnP needed for state estimation.
    """
    def lm(idx):
        p = landmarks.landmark[idx]
        return np.array([p.x * iw, p.y * ih])

    nose = lm(_NOSE_TIP)
    forehead = lm(_FOREHEAD)
    chin = lm(_CHIN)
    l_eye = lm(_L_EYE_OUT)
    r_eye = lm(_R_EYE_OUT)

    face_height = np.linalg.norm(forehead - chin) + 1e-6
    face_width  = np.linalg.norm(r_eye - l_eye) + 1e-6
    face_centre = (l_eye + r_eye) / 2.0

    # Pitch: nose sits below face centre when tilted down
    nose_off_y = (nose[1] - face_centre[1]) / face_height
    pitch_deg  = float(np.degrees(np.arctan(nose_off_y)))

    # Yaw: nose deviates horizontally from face midpoint
    nose_off_x = (nose[0] - face_centre[0]) / face_width
    yaw_deg    = float(np.degrees(np.arctan(nose_off_x)) * 2)

    return pitch_deg, yaw_deg


def _detect_eye_contact(landmarks, iw: int, ih: int) -> bool:
    """
    Heuristic: user is making eye contact if both eyes are in the upper half
    of the frame and yaw/pitch is roughly centred (±25°).
    """
    def lm(idx):
        p = landmarks.landmark[idx]
        return np.array([p.x * iw, p.y * ih])

    l_eye = lm(_L_EYE_OUT)
    r_eye = lm(_R_EYE_OUT)

    eyes_in_upper_half = l_eye[1] < ih * 0.55 and r_eye[1] < ih * 0.55
    _, yaw = _estimate_head_pose(landmarks, iw, ih)
    return eyes_in_upper_half and abs(yaw) < 25.0


def _detect_fatigue_blink(eye_landmarks, prev_ear: float) -> Tuple[bool, float]:
    """
    Eye Aspect Ratio (EAR) drop as a lightweight fatigue / eye-rub detector.
    Returns (is_fatigued, current_ear).
    """
    # We only have 2-D; use vertical-to-horizontal eye opening ratio
    # Using MediaPipe left eye contour landmarks 160,144 (vertical) and 33,133 (horizontal)
    def dist(a, b):
        return np.linalg.norm(np.array([a.x, a.y]) - np.array([b.x, b.y]))

    try:
        lms = eye_landmarks.landmark
        v1 = dist(lms[160], lms[144])
        v2 = dist(lms[158], lms[153])
        h  = dist(lms[33],  lms[133]) + 1e-6
        ear = (v1 + v2) / (2.0 * h)
        fatigued = ear < 0.20 and prev_ear > 0.22   # sudden drop
        return fatigued, ear
    except Exception:
        return False, prev_ear


# ---------------------------------------------------------------------------
# Low-Light Night Contrast Enhancement (CLAHE + Gamma Boost)
# ---------------------------------------------------------------------------
def enhance_low_light(frame: np.ndarray, brightness_thresh: float = 95.0) -> np.ndarray:
    """
    If average frame brightness is low (nighttime or dim indoor lighting),
    applies CLAHE (Contrast Limited Adaptive Histogram Equalization) on the L-channel
    in LAB color space and a gentle gamma boost (0.75) for deep shadows so handheld
    objects, book titles, and formulas remain sharp and legible instead of dark blur.
    Returns the original frame untouched if brightness exceeds brightness_thresh.
    """
    if frame is None or frame.size == 0:
        return frame
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mean_val = float(np.mean(gray))
        if mean_val >= brightness_thresh:
            return frame

        # 1. CLAHE on L-channel in LAB space to enhance local contrast without hue shifts
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        cl = clahe.apply(l_channel)
        enhanced = cv2.cvtColor(cv2.merge((cl, a_channel, b_channel)), cv2.COLOR_LAB2BGR)

        # 2. Light gamma curve boost if scene is particularly dark (< 60 mean)
        if mean_val < 60.0:
            table = np.array([((i / 255.0) ** 0.75) * 255 for i in range(256)]).astype("uint8")
            enhanced = cv2.LUT(enhanced, table)

        return enhanced
    except Exception as exc:
        logger.debug("[PERCEPTION] enhance_low_light fallback: %s", exc)
        return frame


# ---------------------------------------------------------------------------
# Hardware Webcam Worker (Isolated Non-blocking Daemon Thread)
# ---------------------------------------------------------------------------
class CameraWorker(threading.Thread):
    """
    Isolated daemon thread dedicated exclusively to hardware webcam capture.
    Guarantees:
      - cv2.VideoCapture is NEVER instantiated on the main thread or request threads.
      - Uses cv2.CAP_DSHOW on Windows to avoid Media Foundation startup hangs.
      - 2.0-second initialization timeout. If camera is busy or unavailable:
          • Logs: '[PERCEPTION] Webcam unavailable/busy. Running headless with OS-level sensors only.'
          • Sets self.camera_available = False
          • Does NOT crash or retry in a tight loop.
      - Thread-safe atomic pointer for latest downscaled frame snapshot guarded by a mutex.
      - get_latest_frame() returns immediately with None if offline (zero blocking).
    """

    def __init__(self, camera_index: int = 0, target_fps: int = 15):
        super().__init__(daemon=True, name="CameraWorker")
        self.camera_index = camera_index
        self.target_fps = max(1, target_fps)
        self.camera_available = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._stop_event = threading.Event()
        self._cap: Optional[cv2.VideoCapture] = None

    def run(self):
        # 1. Non-blocking initialization guarded by a 2.0s timeout
        cap_holder: list[Optional[cv2.VideoCapture]] = [None]
        err_holder: list[Optional[Exception]] = [None]

        def _open_device():
            try:
                backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
                c = cv2.VideoCapture(self.camera_index, backend)
                if c.isOpened():
                    c.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    c.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    c.set(cv2.CAP_PROP_FPS, self.target_fps)
                    cap_holder[0] = c
                else:
                    c.release()
            except Exception as exc:
                err_holder[0] = exc

        init_thread = threading.Thread(target=_open_device, daemon=True, name="CameraInitThread")
        init_thread.start()
        init_thread.join(timeout=2.0)

        if init_thread.is_alive() or cap_holder[0] is None:
            logger.warning("[PERCEPTION] Webcam unavailable/busy. Running headless with OS-level sensors only.")
            self.camera_available = False
            # Sleep quietly without tight loop until shutdown
            while not self._stop_event.is_set():
                time.sleep(1.0)
            return

        self._cap = cap_holder[0]
        self.camera_available = True
        logger.info("[PERCEPTION] Webcam initialized successfully (DirectShow 640x480 @ %dfps).", self.target_fps)

        frame_interval = 1.0 / self.target_fps
        consecutive_read_failures = 0

        try:
            while not self._stop_event.is_set():
                t0 = time.time()
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    consecutive_read_failures += 1
                    if consecutive_read_failures > 15:
                        logger.warning("[PERCEPTION] Webcam disconnected or dropped frames. Switching to headless mode.")
                        self.camera_available = False
                        break
                    time.sleep(0.05)
                    continue

                consecutive_read_failures = 0
                with self._frame_lock:
                    self._latest_frame = frame

                elapsed = time.time() - t0
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

            # If stopped due to errors, idle quietly
            while not self._stop_event.is_set():
                time.sleep(1.0)

        except Exception as exc:
            logger.warning("[PERCEPTION] Camera worker error: %s", exc)
            self.camera_available = False
        finally:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            with self._frame_lock:
                self._latest_frame = None
            logger.info("[PERCEPTION] Camera worker released cleanly.")

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Returns the latest frame copy or None immediately with zero blocking."""
        if not self.camera_available:
            return None
        with self._frame_lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def capture_frame_blob(
        self,
        max_dimension: int = 512,
        quality: int = 75,
        enhance_lighting: bool = True,
        width: Optional[int] = None,
    ) -> Optional[bytes]:
        """Thread-safe snapshot capture as downscaled JPEG bytes with night contrast boost."""
        frame = self.get_latest_frame()
        if frame is None:
            return None
        return PerceptionEngine.capture_frame_blob(
            frame,
            max_dimension=max_dimension,
            quality=quality,
            enhance_lighting=enhance_lighting,
            width=width,
        )

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Perception Engine (main class)
# ---------------------------------------------------------------------------
class PerceptionEngine:
    """
    Spawns a single daemon thread. Consumers read from `snapshot_queue`.
    Throttled to 1-2 FPS with automated frame downscaling and motion gating
    to keep desktop CPU/GPU consumption minimal.
    """

    # Throttled sensory loop: 0.50s = 2 FPS max
    PROCESS_EVERY_N = 1
    POLL_INTERVAL   = 0.50   # seconds between sensory evaluations (2 FPS)

    # Major-state-change thresholds
    AWAY_THRESHOLD_SEC  = 20.0   # no face → presence=False after this
    RETURN_THRESHOLD_SEC = 3.0   # face detected for this long → presence=True

    def __init__(
        self,
        camera_index: int = 0,
        snapshot_queue: Optional[queue.Queue] = None,
        frame_source=None,
    ):
        self._cam_idx       = camera_index
        self.snapshot_queue = snapshot_queue or queue.Queue(maxsize=8)
        self._frame_source  = frame_source
        self._stop_event    = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Dedicated non-blocking hardware camera worker (never runs on main thread)
        self.camera_worker: Optional[CameraWorker] = None
        if self._frame_source is None and self._cam_idx is not None and self._cam_idx >= 0:
            self.camera_worker = CameraWorker(camera_index=self._cam_idx, target_fps=15)

        # Internal rolling state
        self._prev_snapshot: Optional[PerceptionSnapshot] = None
        self._prev_gray_thumb: Optional[np.ndarray] = None
        self._face_absent_since: Optional[float] = None
        self._face_present_since: Optional[float] = None
        self._prev_ear: float = 0.3
        self._frame_counter: int = 0

        # Haar fallback
        self._haar_cascade: Optional[cv2.CascadeClassifier] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        if self.camera_worker is not None:
            self.camera_worker.start()
        self._thread = threading.Thread(target=self._run, daemon=True, name="PerceptionWorker")
        self._thread.start()
        logger.info("[PERCEPTION] Sensory loop started (cam=%s).", self._cam_idx)

    def stop(self):
        self._stop_event.set()
        if self.camera_worker is not None:
            self.camera_worker.stop()
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("[PERCEPTION] Sensory loop stopped.")

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Thread-safe access to the latest captured frame or None if offline."""
        if self._frame_source is not None:
            try:
                return self._frame_source()
            except Exception:
                return None
        if self.camera_worker is not None:
            return self.camera_worker.get_latest_frame()
        return None

    @property
    def camera_available(self) -> bool:
        if self.camera_worker is not None:
            return self.camera_worker.camera_available
        return self._frame_source is not None

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------
    def _run(self):
        # Safe Haar fallback initialization
        self._haar_cascade = None
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            try:
                haar_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                if os.path.exists(haar_path):
                    self._haar_cascade = cv2.CascadeClassifier(haar_path)
            except Exception as exc:
                logger.debug("[PERCEPTION] Haar cascade classifier not loaded: %s", exc)

        face_mesh_ctx = None
        if _MP_AVAILABLE:
            try:
                face_mesh_ctx = _mp_face_mesh.FaceMesh(
                    max_num_faces=1,
                    refine_landmarks=True,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.4,
                )
            except Exception as exc:
                logger.warning("[PERCEPTION] MediaPipe FaceMesh initialization failed: %s", exc)
                face_mesh_ctx = None

        try:
            while not self._stop_event.is_set():
                frame = None
                if self._frame_source is not None:
                    try:
                        frame = self._frame_source()
                    except Exception as e:
                        logger.debug("[PERCEPTION] Frame source read exception: %s", e)
                elif self.camera_worker is not None:
                    frame = self.camera_worker.get_latest_frame()

                # Build snapshot from frame + OS sensors (runs smoothly with or without camera)
                snap = self._build_snapshot(frame, face_mesh_ctx)

                # Only enqueue if meaningful change OR 10-s heartbeat
                if self._is_significant_change(snap):
                    if not self.snapshot_queue.full():
                        self.snapshot_queue.put(snap)
                    self._prev_snapshot = snap

                time.sleep(self.POLL_INTERVAL)

        finally:
            if face_mesh_ctx:
                try:
                    face_mesh_ctx.close()
                except Exception:
                    pass
            logger.info("[PERCEPTION] Sensory loop exited cleanly.")

    # ------------------------------------------------------------------
    # Snapshot construction
    # ------------------------------------------------------------------
    def _build_snapshot(
        self,
        frame: Optional[np.ndarray],
        face_mesh_ctx,
    ) -> PerceptionSnapshot:

        snap = PerceptionSnapshot()
        now  = time.localtime()
        snap.hour         = now.tm_hour
        snap.is_late_night = snap.hour >= 22 or snap.hour < 5
        snap.is_work_hours = 9 <= snap.hour < 18

        # ---- Desktop sensor ----
        snap.active_window = self._get_active_window()
        snap.idle_seconds  = self._get_idle_seconds()

        # ---- Vision ----
        if frame is not None:
            self._frame_counter += 1
            if self._frame_counter % self.PROCESS_EVERY_N == 0:
                snap = self._apply_vision(frame, snap, face_mesh_ctx)

            # Carry previous vision state on skipped frames
            elif self._prev_snapshot:
                snap.face_detected = self._prev_snapshot.face_detected
                snap.eye_contact   = self._prev_snapshot.eye_contact
                snap.head_pitch    = self._prev_snapshot.head_pitch
                snap.head_yaw      = self._prev_snapshot.head_yaw
                snap.fatigue_cue   = self._prev_snapshot.fatigue_cue
                snap.presence      = self._prev_snapshot.presence
        else:
            # No camera → infer from idle
            snap.presence = snap.idle_seconds < 120.0

        # ---- Presence state machine (only when camera is available) ----
        if self.camera_available:
            snap = self._update_presence(snap)

        return snap

    def _apply_vision(
        self,
        frame: np.ndarray,
        snap: PerceptionSnapshot,
        face_mesh_ctx,
    ) -> PerceptionSnapshot:
        ih_orig, iw_orig = frame.shape[:2]

        # 1. Downscale to max 320x240 to conserve CPU/GPU cycles
        target_w = 320
        if iw_orig > target_w:
            scale = target_w / float(iw_orig)
            proc_frame = cv2.resize(frame, (target_w, int(ih_orig * scale)), interpolation=cv2.INTER_AREA)
        else:
            proc_frame = frame

        ih, iw = proc_frame.shape[:2]

        # 2. Motion Gating: if scene is static and previous face state is known, skip heavy inference
        gray_thumb = cv2.resize(proc_frame, (64, 48), interpolation=cv2.INTER_NEAREST)
        if len(gray_thumb.shape) == 3:
            gray_thumb = cv2.cvtColor(gray_thumb, cv2.COLOR_BGR2GRAY)

        motion_detected = True
        if self._prev_gray_thumb is not None and self._prev_snapshot is not None:
            diff = cv2.absdiff(gray_thumb, self._prev_gray_thumb)
            if np.mean(diff) < 2.0:
                motion_detected = False

        self._prev_gray_thumb = gray_thumb

        if not motion_detected and self._prev_snapshot is not None:
            # Re-use previous face orientation without running CV
            snap.face_detected = self._prev_snapshot.face_detected
            snap.eye_contact   = self._prev_snapshot.eye_contact
            snap.head_pitch    = self._prev_snapshot.head_pitch
            snap.head_yaw      = self._prev_snapshot.head_yaw
            snap.fatigue_cue   = False
            return snap

        rgb = cv2.cvtColor(proc_frame, cv2.COLOR_BGR2RGB)

        if _MP_AVAILABLE and face_mesh_ctx:
            results = face_mesh_ctx.process(rgb)
            if results.multi_face_landmarks:
                lms = results.multi_face_landmarks[0]
                snap.face_detected = True
                snap.head_pitch, snap.head_yaw = _estimate_head_pose(lms, iw, ih)
                snap.eye_contact  = _detect_eye_contact(lms, iw, ih)
                fatigued, self._prev_ear = _detect_fatigue_blink(lms, self._prev_ear)
                snap.fatigue_cue  = fatigued
            else:
                snap.face_detected = False
        else:
            # Fast Haar fallback on downscaled frame
            if self._haar_cascade is not None:
                try:
                    gray  = cv2.cvtColor(proc_frame, cv2.COLOR_BGR2GRAY)
                    faces = self._haar_cascade.detectMultiScale(gray, 1.15, 4, minSize=(30, 30))
                    snap.face_detected = len(faces) > 0
                    snap.eye_contact   = snap.face_detected   # assume contact if detected
                except Exception:
                    snap.face_detected = False
                    snap.eye_contact   = False
            else:
                snap.face_detected = False
                snap.eye_contact   = False

        return snap

    def _update_presence(self, snap: PerceptionSnapshot) -> PerceptionSnapshot:
        now = time.time()
        if snap.face_detected:
            self._face_absent_since = None
            if self._face_present_since is None:
                self._face_present_since = now
            if now - self._face_present_since >= self.RETURN_THRESHOLD_SEC:
                snap.presence = True
        else:
            self._face_present_since = None
            if self._face_absent_since is None:
                self._face_absent_since = now
            if now - self._face_absent_since >= self.AWAY_THRESHOLD_SEC:
                snap.presence = False
            elif self._prev_snapshot:
                snap.presence = self._prev_snapshot.presence  # hold state during brief absence

        return snap

    # ------------------------------------------------------------------
    # Significance gate — avoid flooding brain with identical state
    # ------------------------------------------------------------------
    def _is_significant_change(self, snap: PerceptionSnapshot) -> bool:
        prev = self._prev_snapshot
        if prev is None:
            return True
        if time.time() - prev.timestamp > 10.0:          # 10-s heartbeat
            return True
        if snap.presence != prev.presence:
            return True
        if snap.fatigue_cue and not prev.fatigue_cue:
            return True
        if snap.active_window != prev.active_window:
            return True
        # Large head-pose shift (>15°)
        if abs(snap.head_yaw - prev.head_yaw) > 15 or abs(snap.head_pitch - prev.head_pitch) > 15:
            return True
        return False

    # ------------------------------------------------------------------
    # OS helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _get_active_window() -> str:
        if _WIN32_AVAILABLE:
            try:
                hwnd  = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd)
                return title[:120] if title else ""
            except Exception:
                return ""
        return ""

    @staticmethod
    def _get_idle_seconds() -> float:
        if _WIN32_AVAILABLE:
            try:
                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
                lii = LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(lii)
                ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
            except Exception:
                return 0.0
        return 0.0

    @staticmethod
    def _open_camera() -> Optional[cv2.VideoCapture]:
        try:
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            if not cap.isOpened():
                return None
            return cap
        except Exception as exc:
            logger.error("[PERCEPTION] Camera open failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Snapshot utility: capture one downscaled JPEG for multimodal API
    # ------------------------------------------------------------------
    @staticmethod
    def capture_frame_blob(
        frame: np.ndarray,
        max_dimension: int = 512,
        quality: int = 75,
        enhance_lighting: bool = True,
        width: Optional[int] = None,
    ) -> Optional[bytes]:
        """
        Downscale a BGR frame (max 512px on longest side or 640x480) with JPEG quality=75,
        applying CLAHE / low-light contrast enhancement if scene lighting is dark.
        """
        if frame is None or frame.size == 0:
            return None
        try:
            target_dim = width if width is not None else max_dimension
            if enhance_lighting:
                frame = enhance_low_light(frame)
            h, w = frame.shape[:2]
            max_side = max(h, w)
            if max_side > target_dim:
                scale = float(target_dim) / float(max_side)
                resized = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            else:
                resized = frame
            ret, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ret:
                return buf.tobytes()
        except Exception as exc:
            logger.warning("[PERCEPTION] capture_frame_blob error: %s", exc)
        return None
