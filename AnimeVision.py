import io
import cv2
from PIL import Image
import mss
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import threading
import time
import random
import json
import requests
import google.generativeai as genai

# ================= CONFIGURATION =================
GEMINI_API_KEY = "KEY"  # Paste your active key here
MODEL_NAME = "gemini-3.5-flash-lite"
MATEENGINE_PORT = 8787

RANDOM_MSG_MIN_INTERVAL = 240
RANDOM_MSG_MAX_INTERVAL = 480
# =================================================

genai.configure(api_key=GEMINI_API_KEY)
# Fast generation config for instant punchy desktop replies
model = genai.GenerativeModel(
    MODEL_NAME,
    generation_config={"temperature": 0.7, "max_output_tokens": 120}
)

latest_frame = None
frame_lock = threading.Lock()
last_interaction_time = time.time()

app = Flask(__name__)
CORS(app)

# ==========================================
# 1. HIGH-SPEED WEBCAM & SCREEN COMPRESSION
# ==========================================
def camera_stream_loop():
    global latest_frame
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    # Lower hardware grab resolution for raw speed
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    print("[SYSTEM] Camera active (DirectShow 640x480)...")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            time.sleep(0.02)
            continue

        with frame_lock:
            latest_frame = frame.copy()

        cv2.imshow('Anime Vision Camera Feed', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

def grab_dual_visuals_fast():
    """Captures and compresses webcam + screen into lightweight micro-JPEGs."""
    visual_payload = []

    # 1. Fast Webcam Compress (~25 KB)
    with frame_lock:
        if latest_frame is not None:
            ret, buf = cv2.imencode('.jpg', latest_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
            if ret:
                visual_payload.append(Image.open(io.BytesIO(buf)))

    # 2. Fast Screen Grab & Downscale (~60 KB)
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            sct_img = sct.grab(monitor)
            screen_pil = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            screen_pil.thumbnail((800, 450))
            
            buf = io.BytesIO()
            screen_pil.save(buf, format="JPEG", quality=65)
            buf.seek(0)
            visual_payload.append(Image.open(buf))
    except Exception as e:
        print(f"[SCREEN GRAB ERROR] {e}")

    return visual_payload

def push_to_mateengine(text, emotion="joy"):
    """Guarantees the desktop avatar bubble and visemes update with complete text."""
    try:
        requests.post(
            f"http://127.0.0.1:{MATEENGINE_PORT}/api/chat",
            json={"text": text, "message": text, "emotion": emotion, "triggerLipSync": True, "showBubble": True},
            timeout=1.0
        )
    except Exception:
        pass

# ==========================================
# 2. AUTONOMOUS PROACTIVE LOOP
# ==========================================
def proactive_observer_loop():
    global last_interaction_time
    print("[SYSTEM] Autonomous thought engine online...")

    while True:
        sleep_duration = random.randint(RANDOM_MSG_MIN_INTERVAL, RANDOM_MSG_MAX_INTERVAL)
        time.sleep(sleep_duration)

        if time.time() - last_interaction_time < RANDOM_MSG_MIN_INTERVAL:
            continue

        visuals = grab_dual_visuals_fast()
        if not visuals:
            continue

        proactive_prompt = (
            "You are Alpha, a desktop anime companion. "
            "Visual inputs show the user's webcam and screen. "
            "Notice what they are working on, reading, playing, or listening to, and make a short, playful spontaneous comment (1 short sentence max). "
            "Do not sound robotic or mention cameras. Talk directly to them."
        )

        try:
            response = model.generate_content(visuals + [proactive_prompt])
            remark = response.text.strip()
            if remark:
                print(f"\n[SPONTANEOUS THOUGHT] {remark}")
                push_to_mateengine(remark, "joy")
                last_interaction_time = time.time()
        except Exception as e:
            print(f"[SPONTANEOUS ERROR] {e}")

# ==========================================
# 3. HIGH-SPEED SSE/JSON BRIDGE ROUTE
# ==========================================
@app.route('/v1/models', methods=['GET'])
@app.route('/models', methods=['GET'])
def get_models():
    return jsonify({
        "object": "list",
        "data": [{"id": "gpt-3.5-turbo", "object": "model", "created": int(time.time()), "owned_by": "custom"}]
    })

@app.route('/v1/chat/completions', methods=['POST', 'OPTIONS'])
@app.route('/chat/completions', methods=['POST', 'OPTIONS'])
@app.route('/api/plugins/mateengine-chat/chat', methods=['POST', 'OPTIONS'])
@app.route('/', defaults={'path': ''}, methods=['POST', 'GET', 'OPTIONS'])
@app.route('/', methods=['POST', 'GET', 'OPTIONS'])
def universal_chat_bridge(path=""):
    global last_interaction_time
    
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"})
    if request.method == 'GET':
        return jsonify({"status": "running", "model": MODEL_NAME})

    last_interaction_time = time.time()
    data = request.get_json(force=True, silent=True) or {}
    is_streaming = data.get('stream', True)

    user_msg = "hello"
    messages = data.get('messages', [])
    if messages:
        user_msg = messages[-1].get('content', 'hello')
    elif 'message' in data:
        user_msg = data['message']
    elif 'text' in data:
        user_msg = data['text']

    print(f"\n[STREAM HIT] User: '{user_msg}' | Grabbing visuals...")

    visuals = grab_dual_visuals_fast()

    system_prompt = (
        "You are Alpha, a cheerful desktop anime companion with vision. "
        "You have snapshots of the user's webcam AND their active desktop screen (apps, music, browser). "
        "Respond naturally in 1-2 short sentences for a desktop speech bubble. "
        "Use screen and camera details accurately.\n"
        f"User said: {user_msg}"
    )

    gemini_payload = visuals + [system_prompt] if visuals else [system_prompt]

    try:
        response = model.generate_content(gemini_payload)
        answer = response.text.strip()
    except Exception as e:
        answer = f"Visual glitch: {e}"

    print(f"[REPLY GENERATED] {answer}")

    # Immediately push complete text to MateEngine to prevent "..." placeholder overwrite
    threading.Thread(target=push_to_mateengine, args=(answer, "joy"), daemon=True).start()

    # If non-streaming
    if not is_streaming:
        return jsonify({
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "gpt-3.5-turbo",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25}
        })

    # SSE streaming chunks for SillyTavern UI
    def sse_emitter():
        chunk_data = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "gpt-3.5-turbo",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}]
        }
        yield f"data: {json.dumps(chunk_data)}\n\n"

        stop_data = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "gpt-3.5-turbo",
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

if __name__ == '__main__':
    threading.Thread(target=camera_stream_loop, daemon=True).start()
    threading.Thread(target=proactive_observer_loop, daemon=True).start()
    print("[SYSTEM] High-Speed Vision Bridge active on port 5001...")
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True, use_reloader=False)
