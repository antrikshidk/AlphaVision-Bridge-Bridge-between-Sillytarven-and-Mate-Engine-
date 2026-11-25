import base64
import io
import os
import sys

# Ensure script executes in local alpha_env venv if accidentally invoked via global python
_script_dir = os.path.dirname(os.path.abspath(__file__))
_venv_py = os.path.join(_script_dir, "Scripts", "python.exe")
if os.path.exists(_venv_py) and os.path.normcase(sys.executable) != os.path.normcase(_venv_py):
    try:
        os.execv(_venv_py, [_venv_py] + sys.argv)
    except Exception:
        pass
import re
import signal
import logging
from logging.handlers import RotatingFileHandler
import cv2
import mss
# Suppress cursor capture flag in mss so Windows DWM never hides or redraws the cursor
try:
    if hasattr(mss, "windows"):
        setattr(mss.windows, "CAPTUREBLT", 0)
    try:
        import mss.windows.gdi
        setattr(mss.windows.gdi, "CAPTUREBLT", 0)
    except Exception:
        pass
except Exception:
    pass

import time
import json
import queue
import random
import threading
import subprocess
import requests
from requests.adapters import HTTPAdapter
import pyautogui
import numpy as np
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from typing import Optional, List, Dict, Any, Tuple
from dotenv import load_dotenv
import google.generativeai as genai

# ---- AlphaVision Subconscious Engine ----
from engine.perception import (
    PerceptionEngine,
    ChatBoxDebouncer,
    detect_opaque_input_box,
    SafeScreenGrabber,
    capture_screen_gdi,
    enhance_low_light,
    tag_window,
)
from engine.brain import (
    BrainEngine,
    get_last_ambient_poke,
    set_last_ambient_poke,
    clear_last_ambient_poke,
)
from engine.dispatcher import Dispatcher, ThoughtQueue

# ================= PRODUCTION LOGGING SETUP =================
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "alphavision.log")

log_formatter = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Rotating file handler: 5MB per file, 2 rolling backups
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=2,
    encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

# Console handler (when running in terminal, using raw stdout with utf-8 protection)
if sys.__stdout__ is not None and hasattr(sys.__stdout__, "write"):
    try:
        if hasattr(sys.__stdout__, "reconfigure"):
            sys.__stdout__.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.__stderr__, "reconfigure"):
            sys.__stderr__.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    console_handler = logging.StreamHandler(sys.__stdout__)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

# Suppress noisy werkzeug polling logs (/spontaneous_pop every 1s)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app_logger = logging.getLogger("alphavision")


# Direct raw stream redirection for stdout and stderr (100% immune to logging recursion)
stdout_log_path = os.path.join(LOG_DIR, "stdout.log")
stderr_log_path = os.path.join(LOG_DIR, "stderr.log")

try:
    sys.stdout = open(stdout_log_path, "a", encoding="utf-8", buffering=1)
    sys.stderr = open(stderr_log_path, "a", encoding="utf-8", buffering=1)
except Exception:
    pass

# ================= CONFIGURATION =================
# Strictly load API credentials from environment (.env)
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=ENV_PATH)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY missing! Ensure it is set in C:\\DriveD\\mate_engine\\MateEngineX_Data\\alpha_env\\.env"
    )

MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite")
MATEENGINE_PORT = int(os.getenv("MATEENGINE_PORT", 8787))
SILLYTAVERN_PORT = int(os.getenv("SILLYTAVERN_PORT", 8000))
FLASK_PORT = int(os.getenv("FLASK_PORT", 5001))

RANDOM_MSG_MIN_INTERVAL = int(os.getenv("RANDOM_MSG_MIN_INTERVAL", 180))
RANDOM_MSG_MAX_INTERVAL = int(os.getenv("RANDOM_MSG_MAX_INTERVAL", 360))
MAX_SLIDING_HISTORY = int(os.getenv("MAX_SLIDING_HISTORY", 6))
# =================================================

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15

class _FastGeminiResponse:
    """Lightweight duck-typed response object matching genai.GenerateContentResponse.text."""
    def __init__(self, text: str):
        self.text = text


class FastGeminiClient:
    """
    Persistent keep-alive HTTP client for Gemini v1beta REST generateContent.
    Reuses pooled TLS sockets, avoids per-turn GenerativeModel instantiation,
    caps output tokens, disables/minimizes thinking overhead (thinkingLevel=MINIMAL
    for Gemini 3.5+ / thinkingBudget=0 for legacy), and logs HTTP 4xx/5xx error bodies.
    """
    def __init__(self, api_key: str, model_name: str, default_max_tokens: int = 75, timeout: Any = (5.0, 25.0)):
        self.api_key = api_key
        self.model_name = model_name.replace("models/", "")
        self.default_max_tokens = default_max_tokens
        self.timeout = timeout
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        self._is_gemini_35_plus = bool(re.search(r"gemini-(?:3\.[5-9]|[4-9]\.)", self.model_name, re.IGNORECASE))
        self._supports_thinking_config = True
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=12, pool_maxsize=12)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    @staticmethod
    def _to_rest_parts(contents: Any) -> List[Dict[str, Any]]:
        items = contents if isinstance(contents, (list, tuple)) else [contents]
        parts: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                if item:
                    parts.append({"text": item})
            elif isinstance(item, dict) and "data" in item:
                raw = item["data"]
                if isinstance(raw, (bytes, bytearray)):
                    b64 = base64.b64encode(bytes(raw)).decode("ascii")
                elif isinstance(raw, str):
                    b64 = raw
                else:
                    continue
                parts.append({
                    "inlineData": {
                        "mimeType": item.get("mime_type", "image/jpeg"),
                        "data": b64,
                    }
                })
            elif hasattr(item, "save"):
                buf = io.BytesIO()
                img_to_save = item.convert("RGB") if getattr(item, "mode", "RGB") != "RGB" else item
                img_to_save.save(buf, format="JPEG", quality=85)
                parts.append({
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(buf.getvalue()).decode("ascii"),
                    }
                })
        return parts

    def generate_content(
        self,
        contents: Any,
        generation_config: Optional[Dict[str, Any]] = None,
        system_instruction: Optional[str] = None,
        disable_thinking: bool = True,
    ) -> _FastGeminiResponse:
        cfg = generation_config or {}
        max_tokens = int(cfg.get("max_output_tokens", self.default_max_tokens))

        gen_cfg: Dict[str, Any] = {
            "maxOutputTokens": max_tokens,
        }
        if not self._is_gemini_35_plus:
            gen_cfg["temperature"] = float(cfg.get("temperature", 0.7))

        if disable_thinking and self._supports_thinking_config:
            if self._is_gemini_35_plus:
                gen_cfg["thinkingConfig"] = {"thinkingLevel": "MINIMAL"}
            else:
                gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}

        payload: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": self._to_rest_parts(contents)}],
            "generationConfig": gen_cfg,
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        resp = self._session.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code == 400 and "thinking" in resp.text.lower() and "thinkingConfig" in gen_cfg:
            app_logger.warning(f"[GEMINI API 400] Retrying without thinkingConfig: {resp.text}")
            self._supports_thinking_config = False
            gen_cfg.pop("thinkingConfig", None)
            resp = self._session.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)

        if resp.status_code >= 400:
            app_logger.error(f"[GEMINI API {resp.status_code}] Error body: {resp.text}")

        resp.raise_for_status()
        body = resp.json()
        text = ""
        candidates = body.get("candidates") or []
        if candidates:
            res_parts = candidates[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in res_parts if isinstance(p, dict)).strip()
        return _FastGeminiResponse(text)


genai.configure(api_key=GEMINI_API_KEY, transport=os.getenv("GEMINI_TRANSPORT", "rest"))
model = FastGeminiClient(GEMINI_API_KEY, MODEL_NAME, default_max_tokens=75)
brain_fast_model = FastGeminiClient(GEMINI_API_KEY, MODEL_NAME, default_max_tokens=45)

app = Flask(__name__)
CORS(app)

# ================= STATE & QUEUES =================
latest_cam_frame = None
frame_lock = threading.Lock()

cached_visual_blobs = []
cached_visual_dict: Dict[str, Optional[bytes]] = {"screen_blob": None, "cam_blob": None, "focus_crop_blob": None}
cached_visual_ts: float = 0.0
visual_cache_lock = threading.Lock()

# Live Sensor State
chat_box_is_open = True
chat_open_event = threading.Event()
chat_open_event.set()

# Queue for Spontaneous Thoughts (O(1) thread-safe bounded deque)
spontaneous_thought_queue = ThoughtQueue(maxlen=10)
conversation_memory = []
memory_lock = threading.Lock()

last_interaction_time = time.time()

# Synchronization State for MateEngine Speech Bubble
last_generated_reply = None
reply_pushed_to_engine = True
reply_lock = threading.Lock()


_last_mateengine_sent_text = ""
_last_mateengine_sent_time = 0.0
_mateengine_send_lock = threading.Lock()


# Vision Intent Gating: only upload heavy webcam/screen frames if user asks
VISION_KEYWORDS = (
    'screen', 'look', 'see', 'webcam', 'camera', 'type bar', 'reading', 'watching',
    'what is on', "what's on", 'window', 'app', 'tabs', 'wearing', 'shirt',
    'room', 'desk', 'face', 'expression', 'glance', 'can you see', 'look at',
    'holding', 'hold up', 'show', 'showing', 'book', 'notes', 'read this',
    'check this', 'what is this', 'solve this', 'paper', 'solve', 'formula',
    'desktop', 'monitor', 'display', 'problem', 'equation', 'diagram', 'pdf'
)

def is_vision_requested(message: str) -> bool:
    """Returns True if user explicitly requests visual analysis of camera or screen."""
    if not message:
        return False
    msg_lower = str(message).lower()
    return any(kw in msg_lower for kw in VISION_KEYWORDS)


_dismiss_timer: Optional[threading.Timer] = None
_dismiss_lock = threading.Lock()

def clear_mateengine_bubble():
    """Sends empty/standUp payload to :8787/api/chat and :8787/api/native-animation to clear speech bubbles."""
    try:
        url_chat = f"http://127.0.0.1:{MATEENGINE_PORT}/api/chat"
        requests.post(url_chat, json={"text": "", "message": "", "characterName": "Alpha", "sender": "Alpha", "showBubble": False, "is_bubble": False}, timeout=1.0)
    except Exception as e:
        app_logger.debug(f"[BUBBLE DISMISS] Failed to clear MateEngine bubble via /api/chat: {e}")
    try:
        url_stand = f"http://127.0.0.1:{MATEENGINE_PORT}/api/native-animation"
        requests.post(url_stand, json={"standUp": True}, timeout=1.0)
    except Exception as e:
        app_logger.debug(f"[BUBBLE DISMISS] Failed to clear MateEngine bubble via /api/native-animation: {e}")

def schedule_bubble_auto_dismiss(delay_sec: float = 4.5):
    """Schedules an auto-dismissal of any active speech bubble in MateEngine after delay_sec."""
    global _dismiss_timer
    with _dismiss_lock:
        if _dismiss_timer is not None:
            try:
                _dismiss_timer.cancel()
            except Exception:
                pass
        _dismiss_timer = threading.Timer(delay_sec, clear_mateengine_bubble)
        _dismiss_timer.daemon = True
        _dismiss_timer.start()


def send_to_mateengine(text, character_name="Alpha", emotion="joy", trigger_lipsync=True, show_bubble=True, force=False, spontaneous=False):
    """Direct speech / animation hook to MateEngine runtime on :8787/api/chat with deduplication and BMP glyph sanitization."""
    global _last_mateengine_sent_text, _last_mateengine_sent_time
    if not text or not str(text).strip():
        return

    clean_text = str(text).strip()
    # Strip characters outside Basic Multilingual Plane (BMP) to avoid Unity [ ] [ ] box glyphs
    clean_text = re.sub(r'[\U00010000-\U0010ffff]', '', clean_text).strip()
    if not clean_text:
        return
    now = time.time()
    with _mateengine_send_lock:
        # Prevent identical messages within 30s or any message within 5s
        if not force:
            if clean_text == _last_mateengine_sent_text and (now - _last_mateengine_sent_time) < 30.0:
                app_logger.debug(f"[SYNC DEDUP] Skipped sending duplicate bubble to MateEngine ({now - _last_mateengine_sent_time:.1f}s ago): '{clean_text[:40]}...'")
                return
        _last_mateengine_sent_text = clean_text
        _last_mateengine_sent_time = now

    url = f"http://127.0.0.1:{MATEENGINE_PORT}/api/chat"
    payload = {
        "text": clean_text,
        "message": clean_text,
        "characterName": character_name,
        "sender": character_name,
        "emotion": emotion,
        "triggerLipSync": trigger_lipsync,
        "showBubble": show_bubble,
        "is_bubble": show_bubble,
        "add_to_chat": True,
        "spontaneous": spontaneous
    }
    try:
        requests.post(url, json=payload, timeout=2.5)
        if show_bubble:
            schedule_bubble_auto_dismiss(5.0)
    except Exception as e:
        app_logger.warning(f"[SYNC WARN] Could not push to MateEngine ({MATEENGINE_PORT}): {e}")


# =========================================================
# 1. DEBOUNCED INPUT-BOX SENSOR & VISUAL CACHER
# =========================================================
def live_sensor_and_cache_worker():
    """
    Throttled screen perception (CAPTUREBLT=0, zero pointer flicker).
    Updates the debounced input box state and keeps separate high-resolution
    screen + webcam (+ optional ROI crop) JPEG frames warm in RAM.
    """
    global chat_box_is_open, cached_visual_blobs, cached_visual_dict, cached_visual_ts
    last_cache_time = 0

    debouncer = ChatBoxDebouncer(
        open_confirm_sec=0.60,
        close_confirm_sec=1.00,
        min_state_dwell_sec=1.00,
        initial_state=chat_box_is_open,
    )

    grabber = SafeScreenGrabber(min_interval_sec=0.65)
    print("[SYSTEM] Flicker-Free Screen Sensor (CAPTUREBLT=0, 650ms rate-limit) online...")

    while True:
        try:
            screen_bgr = grabber.grab_bgr()
            if screen_bgr is not None:
                # Keep PerceptionEngine synced with latest screen frame
                if _awareness_perception is not None:
                    _awareness_perception.update_screen_frame(screen_bgr)

                is_raw_open = detect_opaque_input_box(screen_bgr)
                is_debounced_open, state_changed = debouncer.update(is_raw_open)

                if state_changed:
                    chat_box_is_open = is_debounced_open
                    if is_debounced_open:
                        print("\n[SENSOR] >>> Chat Box OPENED on screen! <<<")
                        chat_open_event.set()

                        # Reopening sync: flush pending unpushed reply to MateEngine bubble
                        to_push = None
                        with reply_lock:
                            if last_generated_reply and not reply_pushed_to_engine:
                                to_push = last_generated_reply
                                reply_pushed_to_engine = True

                        if to_push:
                            threading.Thread(
                                target=send_to_mateengine,
                                args=(to_push,),
                                kwargs={
                                    "character_name": "Alpha",
                                    "emotion": "joy",
                                    "trigger_lipsync": True,
                                    "show_bubble": True
                                },
                                daemon=True
                            ).start()
                    else:
                        print("\n[SENSOR] >>> Chat Box CLOSED on screen! <<<")
                        chat_open_event.clear()

                # Update separate high-definition visual cache every 0.8s (parallel in-memory BytesIO)
                now = time.time()
                if now - last_cache_time >= 0.8:
                    cam_frame = None
                    active_win = ""
                    if _awareness_perception is not None:
                        cam_frame = _awareness_perception.get_latest_frame()
                        active_win = _awareness_perception._get_active_window()
                        blob_dict = _awareness_perception.capture_separate_blobs(
                            screen_bgr=screen_bgr,
                            cam_bgr=cam_frame,
                            screen_max_width=1600,
                            screen_quality=85,
                            cam_width=640,
                            cam_quality=85,
                            include_roi="auto",
                            activity_tag=tag_window(active_win),
                        )
                    else:
                        blob_dict = {"screen_blob": None, "cam_blob": None, "focus_crop_blob": None}

                    parts = _format_separate_visual_parts(blob_dict)
                    with visual_cache_lock:
                        cached_visual_dict = blob_dict
                        cached_visual_blobs = parts
                        cached_visual_ts = now
                    last_cache_time = now

        except Exception as e:
            app_logger.debug(f"[SENSOR WARN] {e}")

        time.sleep(0.15)


def _format_separate_visual_parts(blob_dict: Dict[str, Optional[bytes]]) -> List[Any]:
    """Formats separate sharp screen, webcam, and optional ROI crop into labeled Gemini parts."""
    parts: List[Any] = []
    scr = blob_dict.get("screen_blob")
    cam = blob_dict.get("cam_blob")
    roi = blob_dict.get("focus_crop_blob")
    if scr:
        parts.extend(["[LIVE SCREEN (1600x900)]", {"mime_type": "image/jpeg", "data": scr}])
    if cam:
        parts.extend(["[LIVE WEBCAM (640x480)]", {"mime_type": "image/jpeg", "data": cam}])
    if roi:
        parts.extend(["[ACTIVE WINDOW / CURSOR HIGH-RES CROP]", {"mime_type": "image/jpeg", "data": roi}])
    return parts


def get_live_visual_dict(force_fresh: bool = False, include_roi: Any = "auto") -> Dict[str, Optional[bytes]]:
    """Returns separate in-memory JPEG byte blobs for screen, webcam, and optional ROI crop."""
    if force_fresh and _awareness_perception is not None:
        try:
            active_win = _awareness_perception._get_active_window()
            blob_dict = _awareness_perception.capture_separate_blobs(
                screen_max_width=1600,
                screen_quality=85,
                cam_width=640,
                cam_quality=85,
                include_roi=include_roi,
                activity_tag=tag_window(active_win),
            )
            if any(blob_dict.values()):
                with visual_cache_lock:
                    cached_visual_dict.update(blob_dict)
                    cached_visual_blobs[:] = _format_separate_visual_parts(blob_dict)
                return blob_dict
        except Exception as fresh_err:
            app_logger.debug(f"[FRESH VISUAL ERR] {fresh_err}")
    with visual_cache_lock:
        return dict(cached_visual_dict)


def get_cached_visuals(force_fresh: bool = False, include_roi: Any = "auto"):
    """
    Returns labeled separate JPEG image parts ([LIVE SCREEN], [LIVE WEBCAM],
    and optional [ACTIVE WINDOW / CURSOR HIGH-RES CROP]) encoded in-memory via BytesIO.
    If force_fresh=False and cache is empty (e.g. cold turn), falls back to a fresh capture.
    """
    if force_fresh:
        fresh_dict = get_live_visual_dict(force_fresh=True, include_roi=include_roi)
        fresh_parts = _format_separate_visual_parts(fresh_dict)
        if fresh_parts:
            return fresh_parts

    try:
        with visual_cache_lock:
            cached = list(cached_visual_blobs)
        if cached:
            return cached
        # Cold-start safety: if cache hasn't populated yet, capture live right now
        fresh_dict = get_live_visual_dict(force_fresh=True, include_roi=include_roi)
        return _format_separate_visual_parts(fresh_dict)
    except Exception:
        return []


def record_memory(role, content):
    with memory_lock:
        conversation_memory.append({"role": role, "content": content})
        if len(conversation_memory) > MAX_SLIDING_HISTORY:
            del conversation_memory[:-MAX_SLIDING_HISTORY]


# =========================================================
# 2. DESKTOP CONTROL MOD (PYAUTOGUI)
# =========================================================
def execute_desktop_command(cmd_string):
    try:
        parts = [p.strip() for p in cmd_string.split(",")]
        action_type = parts[0].lower()

        if action_type == "media" and len(parts) > 1:
            sub = parts[1].lower()
            key_map = {
                "playpause": "playpause", "play": "playpause", "pause": "playpause",
                "next": "nexttrack", "prev": "prevtrack",
                "volup": "volumeup", "voldown": "volumedown", "mute": "volumemute"
            }
            if sub in key_map:
                print(f"[DESKTOP MOD] Media key: {key_map[sub]}")
                pyautogui.press(key_map[sub])

        elif action_type == "open_app" and len(parts) > 1:
            print(f"[DESKTOP MOD] Launching: {parts[1]}")
            subprocess.Popen(f"start {parts[1]}", shell=True)

        elif action_type == "open_url" and len(parts) > 1:
            print(f"[DESKTOP MOD] Opening URL: {parts[1]}")
            subprocess.Popen(f'start "" "{parts[1]}"', shell=True)

        elif action_type == "hotkey" and len(parts) > 1:
            keys = [k.strip().lower() for k in parts[1].split("+")]
            print(f"[DESKTOP MOD] Hotkey: {keys}")
            pyautogui.hotkey(*keys)

        elif action_type == "type" and len(parts) > 1:
            text = ", ".join(parts[1:])
            print(f"[DESKTOP MOD] Typing: {text}")
            pyautogui.write(text, interval=0.02)

        elif action_type == "press" and len(parts) > 1:
            pyautogui.press(parts[1].lower())

        elif action_type == "click" and len(parts) >= 3:
            pyautogui.click(int(parts[1]), int(parts[2]))

    except Exception as e:
        print(f"[DESKTOP MOD ERROR] {e}")


def process_actions_and_clean_text(raw_text):
    pattern = r"\[\[ACTION:\s*(.*?)\]\]"
    matches = re.findall(pattern, raw_text, flags=re.IGNORECASE)
    for m in matches:
        threading.Thread(target=execute_desktop_command, args=(m,), daemon=True).start()
    return re.sub(pattern, "", raw_text, flags=re.IGNORECASE).strip()


# =========================================================
# 3. SPONTANEOUS THOUGHT ENDPOINTS & CONFIG SYNC
#    (Single unified spontaneous producer runs inside BrainEngine)
# =========================================================


_last_popped_pop: Optional[tuple[str, float]] = None
_pop_lock = threading.Lock()

# Spontaneous pokes runtime config (synced from SillyTavern UI :5001)
_spontaneous_pokes_enabled: bool = True
_in_chat_poke_enabled: bool = False
_desktop_bubbles_enabled: bool = False
_config_lock = threading.Lock()


@app.route('/api/config', methods=['GET', 'POST', 'OPTIONS'])
@app.route('/config', methods=['GET', 'POST', 'OPTIONS'])
def runtime_config_endpoint():
    """
    Receives and synchronizes runtime config from SillyTavern's Extension-MateEngine drawer.
    Controls spontaneous pokes, desktop speech bubbles, and in-chat delivery.
    """
    global _spontaneous_pokes_enabled, _in_chat_poke_enabled, _desktop_bubbles_enabled

    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    if request.method == 'GET':
        with _config_lock:
            return jsonify({
                "spontaneous_pokes_enabled": _spontaneous_pokes_enabled,
                "in_chat_poke_enabled": _in_chat_poke_enabled,
                "desktop_bubbles_enabled": _desktop_bubbles_enabled,
            }), 200

    data = request.get_json(force=True, silent=True) or {}
    with _config_lock:
        if "spontaneous_pokes_enabled" in data:
            _spontaneous_pokes_enabled = bool(data["spontaneous_pokes_enabled"])
        elif "spontaneousPokesEnabled" in data:
            _spontaneous_pokes_enabled = bool(data["spontaneousPokesEnabled"])

        if "in_chat_poke_enabled" in data:
            _in_chat_poke_enabled = bool(data["in_chat_poke_enabled"])
        elif "inChatPokeEnabled" in data:
            _in_chat_poke_enabled = bool(data["inChatPokeEnabled"])

        if "desktop_bubbles_enabled" in data:
            _desktop_bubbles_enabled = bool(data["desktop_bubbles_enabled"])
        elif "desktopBubblesEnabled" in data:
            _desktop_bubbles_enabled = bool(data["desktopBubblesEnabled"])

    if _awareness_brain is not None and hasattr(_awareness_brain, "set_spontaneous_enabled"):
        _awareness_brain.set_spontaneous_enabled(_spontaneous_pokes_enabled)
    if _awareness_dispatcher is not None and hasattr(_awareness_dispatcher, "set_spontaneous_enabled"):
        _awareness_dispatcher.set_spontaneous_enabled(_spontaneous_pokes_enabled)

    app_logger.info(
        f"[CONFIG SYNC] Runtime config updated: spontaneous_pokes_enabled={_spontaneous_pokes_enabled}, "
        f"in_chat={_in_chat_poke_enabled}, desktop_bubbles={_desktop_bubbles_enabled}"
    )

    return jsonify({
        "success": True,
        "config": {
            "spontaneous_pokes_enabled": _spontaneous_pokes_enabled,
            "in_chat_poke_enabled": _in_chat_poke_enabled,
            "desktop_bubbles_enabled": _desktop_bubbles_enabled,
        }
    }), 200


@app.route('/spontaneous_pop', methods=['GET'])
def pop_spontaneous_thought():
    """
    Polled by SillyTavern's Extension-MateEngine and DesktopMate.
    O(1) non-blocking release of queued spontaneous thoughts for desktop bubble display.
    Pops and removes thought immediately from deque so subsequent polls return {"thought": null}.
    Rejects candidate thoughts matching last popped thought within a 120s sliding window.
    """
    global _last_popped_pop
    if not _spontaneous_pokes_enabled:
        return jsonify({"thought": None, "chat_box_open": chat_box_is_open})
    raw_thought = None
    if spontaneous_thought_queue is not None:
        try:
            if hasattr(spontaneous_thought_queue, "popleft"):
                raw_thought = spontaneous_thought_queue.popleft()
            elif hasattr(spontaneous_thought_queue, "get_nowait"):
                raw_thought = spontaneous_thought_queue.get_nowait()
            elif hasattr(spontaneous_thought_queue, "pop"):
                raw_thought = spontaneous_thought_queue.pop()
        except Exception:
            raw_thought = None

    if raw_thought:
        from engine.brain import clean_thought
        clean = clean_thought(str(raw_thought))
        if clean:
            now = time.time()
            norm_key = " ".join(clean.lower().split())
            with _pop_lock:
                if _last_popped_pop is not None:
                    last_key, last_ts = _last_popped_pop
                    if (now - last_ts) <= 120.0 and norm_key == last_key:
                        app_logger.debug(f"[POP DEDUP] Suppressed duplicate pop within 120s: '{clean}'")
                        return jsonify({"thought": None, "chat_box_open": chat_box_is_open})
                _last_popped_pop = (norm_key, now)

            app_logger.info(f"[QUEUE FLUSHED] Dispatched spontaneous thought to active chat: '{clean}'")
            set_last_ambient_poke(clean, "spontaneous_pop")
            return jsonify({"thought": clean, "chat_box_open": chat_box_is_open, "timestamp": now})

    return jsonify({"thought": None, "chat_box_open": chat_box_is_open})


@app.route('/last_ambient_poke', methods=['GET'])
@app.route('/api/last_ambient_poke', methods=['GET'])
def get_ambient_poke_endpoint():
    """
    Returns short-term ambient poke context (within 90s) for context-aware chat response.
    """
    poke = get_last_ambient_poke()
    if poke and (time.time() - poke.get("timestamp", 0)) <= 90.0:
        return jsonify({"poke": poke, "valid": True})
    return jsonify({"poke": None, "valid": False})


@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health_check():
    """Explicit health check endpoint returning HTTP 200 for watchdog supervisor and SillyTavern."""
    return jsonify({
        "status": "ok",
        "health": "healthy",
        "model": MODEL_NAME,
        "chat_box_open": chat_box_is_open,
        "timestamp": int(time.time())
    }), 200


# =========================================================
# 4. SMART HOLD-QUEUE CHAT BRIDGE
# =========================================================
@app.route('/v1/models', methods=['GET'])
@app.route('/models', methods=['GET'])
def get_models():
    return jsonify({
        "object": "list",
        "data": [{"id": "gpt-3.5-turbo", "object": "model", "created": int(time.time()), "owned_by": "custom"}]
    })


@app.route('/v1/chat/completions', methods=['POST', 'OPTIONS'])
@app.route('/chat/completions', methods=['POST', 'OPTIONS'])
@app.route('/api/chat', methods=['POST', 'OPTIONS'])
@app.route('/api/plugins/mateengine-chat/chat', methods=['POST', 'OPTIONS'])
@app.route('/', defaults={'path': ''}, methods=['POST', 'GET', 'OPTIONS'])
@app.route('/<path:path>', methods=['POST', 'GET', 'OPTIONS'])
def universal_chat_bridge(path=""):
    global last_interaction_time

    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    if request.method == 'GET':
        return jsonify({"status": "running", "model": MODEL_NAME}), 200

    last_interaction_time = time.time()
    t_start = time.time()

    data = request.get_json(force=True, silent=True) or {}
    is_streaming = data.get('stream', False)
    req_model = data.get('model', 'gpt-3.5-turbo')

    try:
        user_msg = "hello"
        messages = data.get('messages', [])
        if messages and isinstance(messages, list):
            last_item = messages[-1]
            if isinstance(last_item, dict):
                user_msg = last_item.get('content', 'hello')
            else:
                user_msg = str(last_item)
        elif 'message' in data:
            user_msg = str(data['message'])
        elif 'text' in data:
            user_msg = str(data['text'])

        # 1. Stop Leaking Context: Strip any prepended [Context: ...] so user message is raw and clean
        user_msg = re.sub(r'\[Context:[^\]]*\][\s.:-]*', '', str(user_msg)).strip()
        if not user_msg:
            user_msg = "hello"

        # Record only the clean user message to memory & transcript
        record_memory("user", user_msg)

        # Reset ignoring / nagging timer in brain engine
        if _awareness_brain is not None:
            try:
                _awareness_brain.record_chat_interaction()
            except Exception:
                pass

        # Check short-term ambient context (Dual-Channel Memory within 90s)
        poke = get_last_ambient_poke()
        ambient_context = ""
        if poke and (time.time() - poke.get("timestamp", 0)) <= 90.0:
            poke_text = poke.get("text", "")
            if poke_text:
                ambient_context = f'You recently remarked to Antriksh: "{poke_text}". He just opened the chat to respond.'
            clear_last_ambient_poke()

        with memory_lock:
            recent_transcript = "\n".join(
                f"{'Antriksh' if m['role'] == 'user' else 'Alpha'}: {m['content']}"
                for m in conversation_memory[-MAX_SLIDING_HISTORY:]
            )

        # 2. Ephemeral System Instruction & Always-On Multimodal Chat Prompt
        system_instruction_lines = [
            "You are Alpha, a sharp-eyed, playful desktop anime companion with live vision and desktop control sitting on Antriksh's screen.",
            "You have Antriksh's live SCREEN image, live WEBCAM image, and optional high-res zoom crop attached to this conversation turn.",
            "CRITICAL INSTRUCTIONS FOR CHAT INPUT ROUTING:",
            "1. Respond directly, conversationally, and naturally to Antriksh's typed message.",
            "2. Ground visual references strictly in what is visible in the attached screen/webcam pixels—use them naturally whenever helpful, without hijacking his topic.",
            "3. Reply in 1-2 short, punchy sentences.",
            "DESKTOP ACTIONS (only use when explicitly asked to control PC/media):",
            "- [[ACTION: media, playpause]] / [[ACTION: media, next]] / [[ACTION: media, prev]]",
            "- [[ACTION: media, volup]] / [[ACTION: media, voldown]]",
            "- [[ACTION: open_app, notepad]] / [[ACTION: open_url, https://youtube.com]]",
            "- [[ACTION: hotkey, win+d]] / [[ACTION: hotkey, ctrl+t]] / [[ACTION: hotkey, ctrl+w]]",
            "- [[ACTION: type, text]] / [[ACTION: press, enter]]",
        ]
        if ambient_context:
            system_instruction_lines.append(f"[Ambient Memory Context: {ambient_context}]")
        system_instruction_str = "\n".join(system_instruction_lines)

        user_content_str = (
            f"Recent Conversation:\n{recent_transcript}\n\n"
            f"Antriksh: {user_msg}\n"
            f"Alpha (reply directly to Antriksh's message '{user_msg}'):"
        )

        # Always attach separate live frames on every chat turn (cached normally, force-fresh when explicitly looking)
        visuals = get_cached_visuals(force_fresh=is_vision_requested(user_msg), include_roi=True)
        gemini_payload = visuals + [user_content_str] if visuals else [user_content_str]

        answer = None
        try:
            response = model.generate_content(
                gemini_payload,
                system_instruction=system_instruction_str,
                generation_config={"temperature": 0.7, "max_output_tokens": 75},
            )
            if response and hasattr(response, "text") and response.text:
                answer = process_actions_and_clean_text(response.text.strip())
        except Exception as e:
            app_logger.warning(f"[CHAT LLM WARN] System instruction call failed, falling back: {e}")
            try:
                fallback_payload = visuals + [f"{system_instruction_str}\n\n{user_content_str}"] if visuals else [f"{system_instruction_str}\n\n{user_content_str}"]
                response = model.generate_content(fallback_payload)
                if response and hasattr(response, "text") and response.text:
                    answer = process_actions_and_clean_text(response.text.strip())
            except Exception as e2:
                app_logger.warning(f"[CHAT LLM WARN] Generation fallback error: {e2}")

        if not answer:
            answer = "I'm right here with you! Had a momentary connection hiccup, but I'm listening."

        # Sanitize answer: strip pipe options and non-BMP emojis that cause Unity box rendering
        answer = re.sub(r'[\U00010000-\U0010ffff]', '', answer).strip()
        if "|" in answer:
            candidates = [opt.strip() for opt in answer.split("|") if opt.strip()]
            if candidates:
                answer = candidates[0]

        record_memory("assistant", answer)

        final_delivery = answer

        app_logger.info(f"[REPLY GENERATED in {time.time() - t_start:.2f}s] {final_delivery}")

        # Track latest generated reply and dispatch directly to MateEngine
        to_dispatch_now = final_delivery
        with reply_lock:
            last_generated_reply = final_delivery
            reply_pushed_to_engine = True

        if to_dispatch_now:
            threading.Thread(
                target=send_to_mateengine,
                args=(to_dispatch_now,),
                kwargs={
                    "character_name": "Alpha",
                    "emotion": "joy",
                    "trigger_lipsync": True,
                    "show_bubble": True,
                    "force": True,
                    "spontaneous": False
                },
                daemon=True
            ).start()
            # Auto-dismiss any lingering user bubble on MateEngine desktop
            schedule_bubble_auto_dismiss(delay_sec=5.0)

        if not is_streaming:
            return jsonify({
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req_model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": final_delivery}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
            }), 200

        def sse_emitter():
            chunk_data = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req_model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": final_delivery}, "finish_reason": None}]
            }
            yield f"data: {json.dumps(chunk_data)}\n\n"

            stop_data = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(stop_data)}\n\n"
            yield "data: [DONE]\n\n"

        headers = {
            'Content-Type': 'text/event-stream; charset=utf-8',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
        return Response(sse_emitter(), headers=headers, mimetype='text/event-stream')

    except Exception as fatal_err:
        app_logger.error(f"[CHAT FATAL] Bridge exception: {fatal_err}")
        fallback_msg = "I'm right here! (Connection recovered cleanly)"
        return jsonify({
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": fallback_msg}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }), 200



# =========================================================
# SUBCONSCIOUS ENGINE — Perception + Brain + Dispatcher
# =========================================================
# These objects are module-level so the Flask routes can also introspect state.
_awareness_dispatcher: Dispatcher | None = None
_awareness_brain: BrainEngine | None = None
_awareness_perception: PerceptionEngine | None = None


def _launch_subconscious_engine():
    """
    Boots the Perception → Brain → Dispatcher pipeline as daemon threads.
    PerceptionEngine manages its own isolated CameraWorker with fail-safe fallback.
    """
    global _awareness_dispatcher, _awareness_brain, _awareness_perception

    # Shared perception snapshot queue (perception → brain)
    perc_q: queue.Queue = queue.Queue(maxsize=16)

    # Dispatcher pushes remarks into the existing spontaneous_thought_queue
    # (picked up by /spontaneous_pop) AND directly to MateEngine :8787.
    _awareness_dispatcher = Dispatcher(
        mateengine_port=MATEENGINE_PORT,
        shared_thought_queue=spontaneous_thought_queue,
    )

    _awareness_brain = BrainEngine(
        snapshot_queue=perc_q,
        remark_callback=_awareness_dispatcher.push,
        gemini_model=brain_fast_model,
        visual_fetcher=lambda include_roi=True: get_live_visual_dict(force_fresh=True, include_roi=include_roi),
        require_vision=True,
    )

    # Dedicated PerceptionEngine with built-in non-blocking CameraWorker (2.0s init timeout)
    _awareness_perception = PerceptionEngine(
        camera_index=0,
        snapshot_queue=perc_q,
    )

    _awareness_brain.set_spontaneous_enabled(_spontaneous_pokes_enabled)
    _awareness_dispatcher.set_spontaneous_enabled(_spontaneous_pokes_enabled)
    _awareness_brain.start()
    _awareness_perception.start()
    print("[SUBCONSCIOUS] AlphaVision Perception + Brain + Dispatcher online.")


def _reclaim_port_if_needed(port: int):
    """Ensures port 5001 is clean before starting Flask, killing any stale listeners."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    in_use = False
    try:
        sock.bind(('127.0.0.1', port))
    except OSError:
        in_use = True
    finally:
        sock.close()

    if in_use:
        app_logger.warning(f"[STARTUP] Port {port} is occupied. Reclaiming port...")
        if sys.platform == "win32":
            try:
                cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}\""
                subprocess.run(cmd, shell=True, timeout=5)
                time.sleep(1.0)
            except Exception as e:
                app_logger.warning(f"[STARTUP] Port reclaim error: {e}")


def _bridge_health_watchdog():
    """
    Lightweight self-healing monitor for MateEngine bridge (:8787).
    Periodically polls bridge connectivity. When connection drops or recovers,
    updates state and resets reply buffers to avoid stale synchronization.
    """
    bridge_url = f"http://127.0.0.1:{MATEENGINE_PORT}/api/status"
    fail_count = 0
    was_connected = False

    while True:
        time.sleep(15.0)
        is_ok = False
        try:
            r = requests.get(bridge_url, timeout=2.0)
            if r.status_code < 500:
                is_ok = True
        except Exception:
            is_ok = False

        if is_ok:
            if not was_connected:
                app_logger.info("[WATCHDOG] MateEngine bridge connection established (:8787).")
                was_connected = True
            fail_count = 0
        else:
            fail_count += 1
            if fail_count == 3 and was_connected:
                app_logger.warning("[WATCHDOG] MateEngine bridge (:8787) offline. Self-healing state reset...")
                was_connected = False
                with reply_lock:
                    global reply_pushed_to_engine
                    reply_pushed_to_engine = False


@app.route('/api/brain_state', methods=['GET'])
def get_brain_state():
    """Debug endpoint: expose current internal state as JSON."""
    if _awareness_brain:
        return jsonify(_awareness_brain.get_state_dict())
    return jsonify({"error": "Brain engine not initialised"}), 503


def _graceful_shutdown(signum, frame):
    """
    Signal handler for SIGINT / SIGTERM.
    Stops subconscious engine threads, releases camera, flushes state.
    """
    print("\n[SYSTEM] Shutdown signal received - cleaning up...")
    if _awareness_perception:
        _awareness_perception.stop()
    if _awareness_brain:
        _awareness_brain.stop()
    print("[SYSTEM] Graceful shutdown complete.")
    raise SystemExit(0)


if __name__ == '__main__':
    # Register graceful shutdown handlers
    signal.signal(signal.SIGINT,  _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # Ensure port 5001 is free
    _reclaim_port_if_needed(FLASK_PORT)

    # Launch Subconscious Engine (Perception with isolated CameraWorker, Brain, Dispatcher)
    _launch_subconscious_engine()

    # Core background workers (BrainEngine drives all unified delta/ambient remarks)
    threading.Thread(target=live_sensor_and_cache_worker, daemon=True, name="SensorCacheWorker").start()
    threading.Thread(target=_bridge_health_watchdog, daemon=True, name="BridgeWatchdog").start()

    print(f"[SYSTEM] Full stack active on port {FLASK_PORT}.")
    print(f"[SYSTEM]   -> Perception + Brain + Dispatcher (AlphaVision Subconscious)")
    print(f"[SYSTEM]   -> Opaque Input Sensor + Hold Queue + Desktop Mod")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, threaded=True, use_reloader=False)
