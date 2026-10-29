import io
import os
import sys
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
import pyautogui
import numpy as np
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai

# ---- AlphaVision Subconscious Engine ----
from engine.perception import (
    PerceptionEngine,
    ChatBoxDebouncer,
    detect_opaque_input_box,
    SafeScreenGrabber,
    capture_screen_gdi,
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

# Console handler (when running in terminal)
if sys.stdout is not None and hasattr(sys.stdout, "write"):
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

# Suppress noisy werkzeug polling logs (/spontaneous_pop every 1s)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app_logger = logging.getLogger("alphavision")


class _StreamToLogger:
    """Redirects stdout and stderr to the rotating file logger for silent pythonw.exe execution."""
    def __init__(self, logger_instance, level=logging.INFO):
        self._logger = logger_instance
        self._level = level
        self._buf = ""
        self.encoding = "utf-8"

    def write(self, message):
        if not message:
            return
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        elif not isinstance(message, str):
            message = str(message)
        self._buf += message
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line.strip():
                self._logger.log(self._level, line)

    def flush(self):
        if self._buf.strip():
            self._logger.log(self._level, self._buf.rstrip("\r"))
            self._buf = ""

    def isatty(self):
        return False

    def reconfigure(self, **kwargs):
        pass


# Stream redirection for headless/pythonw mode
sys.stdout = _StreamToLogger(app_logger, logging.INFO)
sys.stderr = _StreamToLogger(app_logger, logging.ERROR)

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

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    MODEL_NAME,
    generation_config={"temperature": 0.7, "max_output_tokens": 90}
)

app = Flask(__name__)
CORS(app)

# ================= STATE & QUEUES =================
latest_cam_frame = None
frame_lock = threading.Lock()

cached_visual_blobs = []
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


def send_to_mateengine(text, character_name="Alpha", emotion="joy", trigger_lipsync=True, show_bubble=True, force=False):
    """Direct speech / animation hook to MateEngine runtime on :8787/api/chat with deduplication."""
    global _last_mateengine_sent_text, _last_mateengine_sent_time
    if not text or not str(text).strip():
        return

    clean_text = str(text).strip()
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
        "characterName": character_name,
        "emotion": emotion,
        "triggerLipSync": trigger_lipsync,
        "showBubble": show_bubble
    }
    try:
        requests.post(url, json=payload, timeout=1.5)
    except Exception as e:
        app_logger.debug(f"[SYNC WARN] Could not push to MateEngine ({MATEENGINE_PORT}): {e}")


# =========================================================
# 1. DEBOUNCED INPUT-BOX SENSOR & VISUAL CACHER
# =========================================================
def live_sensor_and_cache_worker():
    """
    Throttled screen perception (500ms-800ms) with CAPTUREBLT=0 to completely eliminate
    mouse pointer flickering and DWM contention.
    Updates the debounced input box state and raw JPEG visual cache.
    """
    global chat_box_is_open, cached_visual_blobs
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

                # Update raw JPEG cache every 1.0s (screen + safe hardware camera snapshot)
                now = time.time()
                if now - last_cache_time >= 1.0:
                    blobs = []
                    # 1. Non-blocking camera frame query from PerceptionEngine's CameraWorker
                    cam_frame = None
                    if _awareness_perception is not None:
                        cam_frame = _awareness_perception.get_latest_frame()

                    if cam_frame is not None:
                        try:
                            cam_small = cv2.resize(cam_frame, (480, 360))
                            ret, buf = cv2.imencode('.jpg', cam_small, [int(cv2.IMWRITE_JPEG_QUALITY), 55])
                            if ret:
                                blobs.append({"mime_type": "image/jpeg", "data": buf.tobytes()})
                        except Exception as cam_err:
                            app_logger.debug(f"[CACHE CAM ERR] {cam_err}")

                    # 2. Desktop screen snapshot
                    try:
                        screen_small = cv2.resize(screen_bgr, (640, 360))
                        ret2, buf2 = cv2.imencode('.jpg', screen_small, [int(cv2.IMWRITE_JPEG_QUALITY), 55])
                        if ret2:
                            blobs.append({"mime_type": "image/jpeg", "data": buf2.tobytes()})
                    except Exception as scr_err:
                        app_logger.debug(f"[CACHE SCREEN ERR] {scr_err}")

                    with visual_cache_lock:
                        cached_visual_blobs = blobs
                    last_cache_time = now

        except Exception as e:
            app_logger.debug(f"[SENSOR WARN] {e}")

        time.sleep(0.15)


def get_cached_visuals():
    try:
        with visual_cache_lock:
            return list(cached_visual_blobs)
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
# 3. SPONTANEOUS THOUGHT PRODUCER & ENDPOINT
# =========================================================
def proactive_observer_loop():
    global last_interaction_time
    print("[SYSTEM] Autonomous thought engine online...")

    while True:
        sleep_duration = random.randint(RANDOM_MSG_MIN_INTERVAL, RANDOM_MSG_MAX_INTERVAL)
        time.sleep(sleep_duration)

        if time.time() - last_interaction_time < RANDOM_MSG_MIN_INTERVAL:
            continue

        visuals = get_cached_visuals()
        if not visuals:
            continue

        proactive_prompt = (
            "You are Alpha, a possessive, playful desktop anime companion. "
            "Look at Antriksh's webcam and screen. "
            "Make ONE short, spontaneous comment (max 14 words) about what he is doing or listening to."
        )

        try:
            response = model.generate_content(visuals + [proactive_prompt])
            from engine.brain import clean_thought
            remark = clean_thought(process_actions_and_clean_text(response.text.strip()))
            if remark:
                app_logger.info(f"[SPONTANEOUS QUEUED] '{remark}'")
                spontaneous_thought_queue.put(remark)
                set_last_ambient_poke(remark, "proactive_observer")
                last_interaction_time = time.time()
        except Exception as e:
            app_logger.warning(f"[SPONTANEOUS ERROR] {e}")


@app.route('/spontaneous_pop', methods=['GET'])
def pop_spontaneous_thought():
    """
    Polled by SillyTavern's Extension-MateEngine.
    O(1) non-blocking release of queued spontaneous thoughts for desktop bubble display.
    Ambient desktop pokes remain purely transient in the MateEngine desktop bubble.
    """
    if not spontaneous_thought_queue.empty():
        thought = spontaneous_thought_queue.popleft()
        if thought:
            print(f"[QUEUE FLUSHED] Sent spontaneous thought to desktop bubble: {thought}")
            set_last_ambient_poke(thought, "spontaneous_pop")
            return jsonify({"thought": thought, "chat_box_open": chat_box_is_open})
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

        record_memory("user", user_msg)

        # Reset ignoring / nagging timer in brain engine
        if _awareness_brain is not None:
            try:
                _awareness_brain.record_chat_interaction()
            except Exception:
                pass

        # Check short-term ambient context (Dual-Channel Memory within 90s)
        poke = get_last_ambient_poke()
        context_injection = ""
        if poke and (time.time() - poke.get("timestamp", 0)) <= 90.0:
            poke_text = poke.get("text", "")
            if poke_text:
                context_injection = f'[Context: You just teased the user with: "{poke_text}". The user just opened the chat to respond.]'
            clear_last_ambient_poke()

        with memory_lock:
            recent_transcript = "\n".join(
                f"{'Antriksh' if m['role'] == 'user' else 'Alpha'}: {m['content']}"
                for m in conversation_memory[-MAX_SLIDING_HISTORY:]
            )

        system_prompt = (
            f"{context_injection}\n\n" if context_injection else ""
        ) + (
            "You are Alpha, a cheerful, possessive desktop anime companion with vision and desktop control. "
            "You see Antriksh's webcam and screen in real time.\n"
            "DESKTOP ACTIONS (only use when asked to control PC/media):\n"
            "- [[ACTION: media, playpause]] / [[ACTION: media, next]] / [[ACTION: media, prev]]\n"
            "- [[ACTION: media, volup]] / [[ACTION: media, voldown]]\n"
            "- [[ACTION: open_app, notepad]] / [[ACTION: open_url, https://youtube.com]]\n"
            "- [[ACTION: hotkey, win+d]] / [[ACTION: hotkey, ctrl+t]] / [[ACTION: hotkey, ctrl+w]]\n"
            "- [[ACTION: type, text]] / [[ACTION: press, enter]]\n\n"
            f"Recent Chat:\n{recent_transcript}\n\n"
            "Reply in 1-2 short punchy sentences. Alpha:"
        )

        visuals = get_cached_visuals()
        gemini_payload = visuals + [system_prompt] if visuals else [system_prompt]

        answer = None
        try:
            response = model.generate_content(gemini_payload)
            if response and hasattr(response, "text") and response.text:
                answer = process_actions_and_clean_text(response.text.strip())
        except Exception as e:
            app_logger.warning(f"[CHAT LLM WARN] Generation error: {e}")

        if not answer:
            answer = "I'm right here with you! Had a momentary connection hiccup, but I'm listening."

        record_memory("assistant", answer)

        # Clear any stale spontaneous thoughts so conversational dialogue remains clean
        while not spontaneous_thought_queue.empty():
            spontaneous_thought_queue.popleft()

        final_delivery = answer

        app_logger.info(f"[REPLY GENERATED in {time.time() - t_start:.2f}s] {final_delivery}")

        # Track latest generated reply
        to_dispatch_now = None
        with reply_lock:
            last_generated_reply = final_delivery
            if chat_box_is_open:
                reply_pushed_to_engine = True
                to_dispatch_now = final_delivery
            else:
                reply_pushed_to_engine = False

        if to_dispatch_now:
            threading.Thread(
                target=send_to_mateengine,
                args=(to_dispatch_now,),
                kwargs={
                    "character_name": "Alpha",
                    "emotion": "joy",
                    "trigger_lipsync": True,
                    "show_bubble": True
                },
                daemon=True
            ).start()

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

    # Brain model — same Gemini model, but low-token setting for rapid generation (<800ms)
    brain_model = genai.GenerativeModel(
        MODEL_NAME,
        generation_config={"temperature": 0.7, "max_output_tokens": 50},
    )

    _awareness_brain = BrainEngine(
        snapshot_queue=perc_q,
        remark_callback=_awareness_dispatcher.push,
        gemini_model=brain_model,
    )

    # Dedicated PerceptionEngine with built-in non-blocking CameraWorker (2.0s init timeout)
    _awareness_perception = PerceptionEngine(
        camera_index=0,
        snapshot_queue=perc_q,
    )

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
    print("\n[SYSTEM] Shutdown signal received — cleaning up...")
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

    # Core background workers
    threading.Thread(target=live_sensor_and_cache_worker, daemon=True, name="SensorCacheWorker").start()
    threading.Thread(target=proactive_observer_loop, daemon=True, name="ProactiveObserver").start()
    threading.Thread(target=_bridge_health_watchdog, daemon=True, name="BridgeWatchdog").start()

    print(f"[SYSTEM] Full stack active on port {FLASK_PORT}.")
    print(f"[SYSTEM]   → Perception + Brain + Dispatcher (AlphaVision Subconscious)")
    print(f"[SYSTEM]   → Opaque Input Sensor + Hold Queue + Desktop Mod")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, threaded=True, use_reloader=False)
