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
import io
import os
import sys
import time
import threading
import queue
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any

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

    # Snapshot blob (downscaled JPEG bytes) — legacy unified field retained for compatibility
    frame_blob: Optional[bytes] = None  # raw JPEG bytes, never the full frame

    # Separate high-definition visual blobs (screen + webcam + optional active ROI crop)
    screen_blob: Optional[bytes] = None
    cam_blob: Optional[bytes] = None
    focus_crop_blob: Optional[bytes] = None

    # Visual change / attention telemetry
    screen_delta: float = 0.0
    cam_delta: float = 0.0
    screen_phash_dist: int = 0
    visual_trigger_reason: str = ""


# ---------------------------------------------------------------------------
# In-Memory JPEG Encoding, Perceptual Hash & Dynamic Cursor/Window ROI Crop
# ---------------------------------------------------------------------------
def _encode_jpeg_bytesio(frame_bgr: Optional[np.ndarray], quality: int = 85) -> Optional[bytes]:
    """Encodes a BGR ndarray to JPEG strictly in memory via io.BytesIO (zero disk I/O)."""
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    try:
        ok, encoded = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return None
        buf = io.BytesIO(encoded.tobytes())
        return buf.getvalue()
    except Exception as exc:
        logger.debug("[PERCEPTION] _encode_jpeg_bytesio error: %s", exc)
        return None


def compute_dhash64(frame_bgr: Optional[np.ndarray]) -> int:
    """Computes a fast 64-bit difference hash (dHash) over a 9x8 grayscale thumbnail."""
    if frame_bgr is None or frame_bgr.size == 0:
        return 0
    small = cv2.resize(frame_bgr, (9, 8), interpolation=cv2.INTER_AREA)
    if len(small.shape) == 3:
        small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    diff = small[:, 1:] > small[:, :-1]
    bits = 0
    for bit in diff.flatten():
        bits = (bits << 1) | int(bool(bit))
    return bits


def hamming_distance64(h1: int, h2: int) -> int:
    """Hamming distance between two 64-bit dHashes."""
    return bin((h1 ^ h2) & ((1 << 64) - 1)).count("1")


def _detect_small_text_or_errors(screen_bgr: Optional[np.ndarray], activity_tag: str = "misc") -> bool:
    """
    Fast heuristic checking whether the screen contains dense small text
    (IDE/PDF/terminal) or red error highlights warranting a native high-res ROI crop.
    """
    if screen_bgr is None or screen_bgr.size == 0:
        return False
    sh, sw = screen_bgr.shape[:2]
    if sw <= 1280:
        return False
    if activity_tag in ("coding", "terminal", "reading", "study", "physics"):
        return True
    try:
        thumb = cv2.resize(screen_bgr, (320, 180), interpolation=cv2.INTER_AREA)
        b, g, r = cv2.split(thumb)
        red_mask = (r > 165) & ((r.astype(np.int16) - np.maximum(g, b).astype(np.int16)) > 65)
        if float(np.mean(red_mask)) > 0.008:
            return True
        gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
        edges = cv2.Laplacian(gray, cv2.CV_16S, ksize=1)
        edge_density = float(np.mean(np.abs(edges) > 22))
        return edge_density > 0.14
    except Exception:
        return False


def extract_focus_roi_crop(
    screen_bgr: Optional[np.ndarray],
    crop_w: int = 768,
    crop_h: int = 512,
) -> Optional[np.ndarray]:
    """
    Extracts a native-resolution (un-downscaled) crop around the cursor or active
    foreground window so small code, formulas, and error logs remain razor-sharp.
    """
    if screen_bgr is None or screen_bgr.size == 0:
        return None
    sh, sw = screen_bgr.shape[:2]
    if sw <= crop_w and sh <= crop_h:
        return None

    cw = min(crop_w, sw)
    ch = min(crop_h, sh)
    cx_px, cy_px = sw // 2, sh // 2

    if _WIN32_AVAILABLE:
        try:
            prim_w = max(1, int(win32api.GetSystemMetrics(0)))
            prim_h = max(1, int(win32api.GetSystemMetrics(1)))
            scale_x = sw / float(prim_w)
            scale_y = sh / float(prim_h)

            used_cursor = False
            get_cursor = getattr(win32gui, "GetCursorPos", None)
            if callable(get_cursor):
                vx, vy = get_cursor()
                if 0 <= vx < prim_w and 0 <= vy < prim_h:
                    cx_px = int(vx * scale_x)
                    cy_px = int(vy * scale_y)
                    used_cursor = True

            if not used_cursor:
                get_fg = getattr(win32gui, "GetForegroundWindow", None)
                get_rect = getattr(win32gui, "GetWindowRect", None)
                if callable(get_fg) and callable(get_rect):
                    hwnd = get_fg()
                    if hwnd:
                        l, t, r, b = get_rect(hwnd)
                        if r > l and b > t:
                            cx_px = int(((l + r) * 0.5) * scale_x)
                            cy_px = int((t + (b - t) * 0.4) * scale_y)
        except Exception:
            pass

    x1 = max(0, min(sw - cw, cx_px - cw // 2))
    y1 = max(0, min(sh - ch, cy_px - ch // 2))
    crop = screen_bgr[y1 : y1 + ch, x1 : x1 + cw]
    return crop.copy() if crop.size > 0 else None


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
        self._last_cam_delta: float = 0.0
        self._last_face_area_ratio: float = 0.0
        self._last_cam_motion_reason: str = ""

        # Haar fallback
        self._haar_cascade: Optional[cv2.CascadeClassifier] = None

        # Thread-safe screen frame pointer & delta trackers
        self._screen_lock = threading.Lock()
        self._latest_screen_frame: Optional[np.ndarray] = None
        self._prev_screen_thumb: Optional[np.ndarray] = None
        self._prev_screen_dhash: Optional[int] = None
        self._prev_screen_red_ratio: float = 0.0
        self._internal_grabber: Optional[SafeScreenGrabber] = None

        # Persistent worker pool for parallel screen/cam/ROI capture & encoding
        self._io_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="PerceptionVisionIO",
        )

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
        try:
            self._io_executor.shutdown(wait=False)
        except Exception:
            pass
        if self._internal_grabber is not None:
            try:
                self._internal_grabber.close()
            except Exception:
                pass
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

                # Grab latest screen frame (or non-flickering fallback) & evaluate deltas
                screen_bgr = self.get_latest_screen_frame()
                if screen_bgr is None and _WIN32_AVAILABLE:
                    if self._internal_grabber is None:
                        self._internal_grabber = SafeScreenGrabber(min_interval_sec=0.65)
                    screen_bgr = self._internal_grabber.grab_bgr()
                    if screen_bgr is not None:
                        self.update_screen_frame(screen_bgr)

                scr_delta, ph_dist, scr_reason = self._evaluate_screen_delta(screen_bgr)
                snap.screen_delta = scr_delta
                snap.screen_phash_dist = ph_dist
                snap.cam_delta = self._last_cam_delta
                if scr_reason:
                    snap.visual_trigger_reason = scr_reason
                elif self._last_cam_motion_reason:
                    snap.visual_trigger_reason = self._last_cam_motion_reason

                # Only enqueue if meaningful change, visual delta, OR 10-s heartbeat
                if self._is_significant_change(snap):
                    try:
                        blobs = self.capture_separate_blobs(
                            screen_bgr=screen_bgr,
                            cam_bgr=frame,
                            include_roi="auto",
                            activity_tag=tag_window(snap.active_window),
                        )
                        snap.screen_blob = blobs.get("screen_blob")
                        snap.cam_blob = blobs.get("cam_blob")
                        snap.focus_crop_blob = blobs.get("focus_crop_blob")
                        if snap.frame_blob is None:
                            snap.frame_blob = snap.screen_blob or snap.cam_blob
                    except Exception as cap_err:
                        logger.debug("[PERCEPTION] Parallel snapshot capture in loop: %s", cap_err)
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
        cam_diff = 0.0
        if self._prev_gray_thumb is not None and self._prev_snapshot is not None:
            diff = cv2.absdiff(gray_thumb, self._prev_gray_thumb)
            cam_diff = float(np.mean(diff))
            if cam_diff < 2.0:
                motion_detected = False

        self._prev_gray_thumb = gray_thumb
        self._last_cam_delta = cam_diff
        self._last_cam_motion_reason = ""

        if not motion_detected and self._prev_snapshot is not None:
            # Re-use previous face orientation without running CV
            snap.face_detected = self._prev_snapshot.face_detected
            snap.eye_contact   = self._prev_snapshot.eye_contact
            snap.head_pitch    = self._prev_snapshot.head_pitch
            snap.head_yaw      = self._prev_snapshot.head_yaw
            snap.fatigue_cue   = False
            return snap

        if cam_diff >= 8.5:
            self._last_cam_motion_reason = "cam_motion"

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

                xs = [p.x for p in lms.landmark]
                ys = [p.y for p in lms.landmark]
                area_ratio = max(0.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
                if self._last_face_area_ratio > 0.02 and area_ratio > self._last_face_area_ratio * 1.32:
                    self._last_cam_motion_reason = "face_closer"
                self._last_face_area_ratio = area_ratio
            else:
                snap.face_detected = False
                self._last_face_area_ratio = 0.0
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
    # Significance gate & delta evaluation
    # ------------------------------------------------------------------
    def _evaluate_screen_delta(self, screen_bgr: Optional[np.ndarray]) -> Tuple[float, int, str]:
        """Measures screen layout/error delta via 32x18 luma absdiff, 64-bit dHash, and red-error ratio."""
        if screen_bgr is None or screen_bgr.size == 0:
            return 0.0, 0, ""
        try:
            thumb_bgr = cv2.resize(screen_bgr, (32, 18), interpolation=cv2.INTER_AREA)
            thumb_gray = cv2.cvtColor(thumb_bgr, cv2.COLOR_BGR2GRAY)
            curr_dhash = compute_dhash64(screen_bgr)

            b, g, r = cv2.split(thumb_bgr)
            red_ratio = float(np.mean((r > 165) & ((r.astype(np.int16) - np.maximum(g, b).astype(np.int16)) > 65)))

            diff_mean = 0.0
            dhash_dist = 0
            reason = ""

            if self._prev_screen_thumb is not None:
                diff_mean = float(np.mean(cv2.absdiff(thumb_gray, self._prev_screen_thumb)))
            if self._prev_screen_dhash is not None:
                dhash_dist = hamming_distance64(curr_dhash, self._prev_screen_dhash)

            if (red_ratio - self._prev_screen_red_ratio) > 0.008 and red_ratio > 0.012:
                reason = "error_flash"
            elif dhash_dist >= 6 or diff_mean >= 4.5:
                reason = "screen_delta"

            self._prev_screen_thumb = thumb_gray
            self._prev_screen_dhash = curr_dhash
            self._prev_screen_red_ratio = red_ratio
            return diff_mean, dhash_dist, reason
        except Exception as exc:
            logger.debug("[PERCEPTION] _evaluate_screen_delta error: %s", exc)
            return 0.0, 0, ""

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
        if snap.visual_trigger_reason or snap.screen_delta >= 4.5 or snap.cam_delta >= 8.5:
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
    # Screen Frame Cache & Unified Multimodal Composite API
    # ------------------------------------------------------------------
    def update_screen_frame(self, screen_frame: Optional[np.ndarray]):
        """Thread-safe update of the latest desktop screen frame."""
        if screen_frame is None or screen_frame.size == 0:
            return
        with self._screen_lock:
            self._latest_screen_frame = screen_frame.copy()

    def get_latest_screen_frame(self) -> Optional[np.ndarray]:
        """Thread-safe retrieval of the latest desktop screen frame."""
        with self._screen_lock:
            return self._latest_screen_frame.copy() if self._latest_screen_frame is not None else None

    def capture_separate_blobs(
        self,
        screen_bgr: Optional[np.ndarray] = None,
        cam_bgr: Optional[np.ndarray] = None,
        screen_max_width: int = 1600,
        screen_quality: int = 85,
        cam_width: int = 640,
        cam_quality: int = 85,
        include_roi: Any = "auto",
        roi_quality: int = 88,
        enhance_lighting: bool = True,
        activity_tag: str = "misc",
    ) -> Dict[str, Optional[bytes]]:
        """
        Captures and JPEG-encodes separate sharp frames concurrently in memory (io.BytesIO):
          - Full desktop screen (up to 1600x900, quality=85)
          - Clean webcam frame (640x480, quality=85, conditional low-light CLAHE boost)
          - Optional native-resolution 768x512 cursor/window ROI crop when small text/errors exist
        """
        if cam_bgr is None:
            cam_bgr = self.get_latest_frame()
        if screen_bgr is None:
            screen_bgr = self.get_latest_screen_frame()
        if screen_bgr is None and _WIN32_AVAILABLE:
            if self._internal_grabber is None:
                self._internal_grabber = SafeScreenGrabber(min_interval_sec=0.25)
            screen_bgr = self._internal_grabber.grab_bgr(force=True)

        def _prep_screen() -> Optional[bytes]:
            if screen_bgr is None or screen_bgr.size == 0:
                return None
            sh, sw = screen_bgr.shape[:2]
            proc = screen_bgr
            if sw > screen_max_width and sw > 0:
                scale = screen_max_width / float(sw)
                proc = cv2.resize(
                    screen_bgr,
                    (screen_max_width, max(1, int(sh * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            return _encode_jpeg_bytesio(proc, quality=screen_quality)

        def _prep_cam() -> Optional[bytes]:
            if cam_bgr is None or cam_bgr.size == 0:
                return None
            ch, cw = cam_bgr.shape[:2]
            proc = cam_bgr
            if enhance_lighting:
                proc = enhance_low_light(proc)
            if cw > cam_width and cw > 0:
                scale = cam_width / float(cw)
                proc = cv2.resize(
                    proc,
                    (cam_width, max(1, int(ch * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            return _encode_jpeg_bytesio(proc, quality=cam_quality)

        def _prep_roi() -> Optional[bytes]:
            if screen_bgr is None or screen_bgr.size == 0:
                return None
            want_roi = (
                True if include_roi is True
                else (_detect_small_text_or_errors(screen_bgr, activity_tag) if include_roi == "auto" else False)
            )
            if not want_roi:
                return None
            crop = extract_focus_roi_crop(screen_bgr)
            return _encode_jpeg_bytesio(crop, quality=roi_quality)

        fut_scr = self._io_executor.submit(_prep_screen)
        fut_cam = self._io_executor.submit(_prep_cam)
        fut_roi = self._io_executor.submit(_prep_roi)

        return {
            "screen_blob": fut_scr.result(),
            "cam_blob": fut_cam.result(),
            "focus_crop_blob": fut_roi.result(),
        }

    def capture_composite_blob(
        self,
        screen_bgr: Optional[np.ndarray] = None,
        target_screen_width: int = 1024,
        pip_width: int = 280,
        quality: int = 75,
        enhance_lighting: bool = True,
        draw_pip_border: bool = True,
    ) -> Optional[bytes]:
        """
        Thread-safe snapshot capture as a unified composite JPEG:
        Full desktop screen (1024px) with live camera Picture-in-Picture (PiP) inset
        in the bottom-right corner.
        """
        cam_frame = self.get_latest_frame()
        screen_frame = screen_bgr if screen_bgr is not None else self.get_latest_screen_frame()
        if screen_frame is None and _WIN32_AVAILABLE:
            screen_frame = capture_screen_gdi()

        return compose_screen_and_camera(
            screen_bgr=screen_frame,
            cam_bgr=cam_frame,
            target_screen_width=target_screen_width,
            pip_width=pip_width,
            quality=quality,
            enhance_lighting=enhance_lighting,
            draw_pip_border=draw_pip_border,
        )

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


# ---------------------------------------------------------------------------
# High-Definition Composite Screen + Camera Framework
# ---------------------------------------------------------------------------
def compose_screen_and_camera_bgr(
    screen_bgr: Optional[np.ndarray],
    cam_bgr: Optional[np.ndarray],
    target_screen_width: int = 1024,
    pip_width: int = 280,
    enhance_lighting: bool = True,
    draw_pip_border: bool = True,
) -> Optional[np.ndarray]:
    """
    Composes a desktop screenshot and camera frame into a single unified
    multimodal frame with webcam Picture-in-Picture (PiP) inset in the bottom-right corner.

    Guarantees:
      - Screen is scaled to target_screen_width (default 1024px) preserving aspect ratio.
      - Webcam is enhanced for low light via CLAHE and inset into the bottom-right
        corner with a high-contrast border and 'WEBCAM' identifier tag.
      - Graceful fallback: returns camera if screen is None, or screen if camera is None.
      - Returns None if both frames are None or empty.
    """
    if (screen_bgr is None or screen_bgr.size == 0) and (cam_bgr is None or cam_bgr.size == 0):
        return None

    # Fallback 1: Only camera is available
    if screen_bgr is None or screen_bgr.size == 0:
        if cam_bgr is None or cam_bgr.size == 0:
            return None
        frame = cam_bgr.copy()
        if enhance_lighting:
            frame = enhance_low_light(frame)
        h, w = frame.shape[:2]
        if w > target_screen_width and w > 0:
            scale = float(target_screen_width) / float(w)
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        return frame

    # Base: Desktop Screen scaled to target_screen_width preserving aspect ratio
    sh_orig, sw_orig = screen_bgr.shape[:2]
    if sw_orig != target_screen_width and sw_orig > 0:
        s_scale = float(target_screen_width) / float(sw_orig)
        composite = cv2.resize(screen_bgr, (target_screen_width, int(sh_orig * s_scale)), interpolation=cv2.INTER_AREA)
    else:
        composite = screen_bgr.copy()

    # Overlay: Live webcam PiP in bottom-right corner if available
    if cam_bgr is not None and cam_bgr.size > 0:
        cam_proc = cam_bgr.copy()
        if enhance_lighting:
            cam_proc = enhance_low_light(cam_proc)

        ch_orig, cw_orig = cam_proc.shape[:2]
        if cw_orig > 0:
            c_scale = float(pip_width) / float(cw_orig)
            pip_h = int(ch_orig * c_scale)
            pip_w = pip_width
            pip_img = cv2.resize(cam_proc, (pip_w, pip_h), interpolation=cv2.INTER_AREA)

            comp_h, comp_w = composite.shape[:2]
            margin = 16
            # Ensure PiP fits cleanly within composite bounds
            if pip_w + margin < comp_w and pip_h + margin < comp_h:
                x_offset = comp_w - pip_w - margin
                y_offset = comp_h - pip_h - margin

                # Inset webcam frame
                composite[y_offset : y_offset + pip_h, x_offset : x_offset + pip_w] = pip_img

                # Draw high-contrast clean border around webcam inset
                if draw_pip_border:
                    cv2.rectangle(
                        composite,
                        (x_offset - 2, y_offset - 2),
                        (x_offset + pip_w + 1, y_offset + pip_h + 1),
                        (30, 30, 30),
                        2
                    )
                    cv2.rectangle(
                        composite,
                        (x_offset, y_offset),
                        (x_offset + pip_w, y_offset + pip_h),
                        (230, 230, 230),
                        1
                    )
                    # Small translucent background banner for "WEBCAM" label
                    cv2.rectangle(
                        composite,
                        (x_offset + 4, y_offset + 4),
                        (x_offset + 64, y_offset + 18),
                        (10, 10, 10),
                        -1
                    )
                    cv2.putText(
                        composite,
                        "WEBCAM",
                        (x_offset + 7, y_offset + 15),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.35,
                        (0, 255, 200),
                        1,
                        cv2.LINE_AA,
                    )

    return composite


def compose_screen_and_camera(
    screen_bgr: Optional[np.ndarray],
    cam_bgr: Optional[np.ndarray],
    target_screen_width: int = 1024,
    pip_width: int = 280,
    quality: int = 75,
    enhance_lighting: bool = True,
    draw_pip_border: bool = True,
) -> Optional[bytes]:
    """
    Composes desktop screen and live webcam into a single high-fidelity JPEG byte buffer.
    Guarantees fast API upload (<100KB), sub-second latency, and single-token allocation (~258 tokens).
    """
    composite = compose_screen_and_camera_bgr(
        screen_bgr=screen_bgr,
        cam_bgr=cam_bgr,
        target_screen_width=target_screen_width,
        pip_width=pip_width,
        enhance_lighting=enhance_lighting,
        draw_pip_border=draw_pip_border,
    )
    if composite is None:
        return None
    try:
        ret, buf = cv2.imencode(".jpg", composite, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ret:
            return buf.tobytes()
    except Exception as exc:
        logger.warning("[PERCEPTION] compose_screen_and_camera encoding error: %s", exc)
    return None
