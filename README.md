# 🌌 AlphaVision Bridge

An ultra-low latency, multimodal dual-vision awareness engine and cognitive bridge connecting **SillyTavern** and **MateEngine** using Gemini Multimodal models (`gemini-2.5-flash-lite` / `gemini-1.5-flash`).

AlphaVision transforms your desktop anime companion into a living, ambient desktop presence with real-time physical space awareness (via camera), active workspace telemetry (via Windows DWM screen and foreground window tracking), and a dual-channel conversational architecture.

---

## ⚡ Key Architecture & Features

### 1. Multi-Modal Desktop Perception & OS Telemetry (`engine/perception.py`)
* **Dual Sensory Loop:** Runs on an isolated worker thread combining computer vision and Windows OS telemetry.
* **Non-Blocking Hardware Camera Isolation:** DirectShow camera acquisition (`cv2.CAP_DSHOW`) runs in an isolated daemon `CameraWorker` with a 2.0-second initialization timeout.
* **Graceful Headless Degradation:** If a webcam is unavailable or locked by another application, AlphaVision degrades seamlessly to OS sensor mode without hanging startup or API endpoints.
* **Flicker-Free Screen Perception:** Screen capture strictly suppresses `CAPTUREBLT` (0x40000000) and throttles polling to $\ge 650$ ms, preventing Windows Desktop Window Manager cursor flickering while triggering immediately on foreground window switches.
* **Window Categorization:** Classifies active applications (coding, physics & science, gaming, browsing, media, writing, and terminal).

### 2. Cognitive State Machine & Volition Engine (`engine/brain.py`)
* **Autonomous Volition:** Evaluates whether ambient observations are warranted without waiting for user prompts.
* **Structured Annoying / Nagging Triggers:**
  * **Ignoring Timer:** If the user is active on the desktop but hasn't chatted with Alpha for $>10\text{--}15$ minutes, triggers an attention-seeking/nagging remark:
    * *Coding:* `"You've been staring at that code forever, take a break and talk to me."`
    * *Browsing:* `"Are you just going to leave me sitting here while you browse?"`
    * *Physics:* Teases user about physics equations and demands a chat break.
    * *Gaming:* Teases user about prioritizing games over their companion.
  * **Subject Focus:** Context-aware pokes for coding, physics/science, and gaming.
* **Strict Formatting & Sanitization:** All spontaneous remarks are stripped of candidate thought pipes (`|`), cleaned of prefixes/quotes, and strictly truncated to a single punchy sentence under 15 words.

### 3. Dual-Channel Architecture (`engine/dispatcher.py` & `AnimeVision.py`)
* **Transient Desktop Pokes (`/spontaneous_pop`):**
  * Spontaneous pokes route directly to MateEngine's speech bubble (:8787) and the transient `/spontaneous_pop` endpoint.
  * Remarks never pollute persistent SillyTavern chat history as system logs.
* **90-Second Ambient Context Injection (`last_ambient_poke`):**
  * When a spontaneous remark is popped, it is recorded in-memory for 90 seconds with context.
  * When the user opens the chat box and sends a message within 90 seconds, the bridge prepends:
    `[Context: You just teased the user with: "<poke>". The user just opened the chat to respond.]`
  * The context expires immediately after the first response, returning subsequent turns to standard dialogue.
* **Clean Normal Chat Routing:**
  * When the user sends a message in the input box, it generates a fresh character response from Gemini.
  * Pre-generated ambient remarks are never returned as the answer to active user messages.

---

## 🏗️ Architecture Flow

```
   [ Physical Camera ]         [ Desktop & Window Telemetry ]
           │                                 │
           ▼                                 ▼
   ┌─────────────────────────────────────────────────────────┐
   │         engine.perception (PerceptionEngine)            │
   │  • CameraWorker (CAP_DSHOW + 2s timeout fail-safe)       │
   │  • SafeScreenGrabber (CAPTUREBLT=0, 650ms debounce)     │
   │  • Context classifier (Coding, Physics, Gaming, Web)    │
   └──────────────────────────┬──────────────────────────────┘
                              │ PerceptionSnapshot (queue)
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │             engine.brain (BrainEngine)                  │
   │  • State Machine (focused, chilling, away, returned)    │
   │  • Ignoring Timer (>10m active without chatting)        │
   │  • Dedicated Low-Token LLM Worker (<800ms Flash Lite)   │
   │  • In-Memory last_ambient_poke (90s expiration)         │
   └──────────────────────────┬──────────────────────────────┘
                              │ clean_thought (<=15 words)
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │           engine.dispatcher (Dispatcher)                │
   │  • 15s rate-limit guard + 60s duplicate filter          │
   └─────────────┬─────────────────────────────┬─────────────┘
                 │                             │
    HTTP :8787   │                             │ Shared O(1) Queue
                 ▼                             ▼
   ┌──────────────────────────┐   ┌──────────────────────────┐
   │     MateEngineX.exe      │   │     GET /spontaneous_pop │
   │  Desktop Speech Bubble   │   │  (Extension-MateEngine)  │
   │  & Viseme Lip-Sync       │   │  Transient Bubble Push   │
   └──────────────────────────┘   └──────────────────────────┘
```

---

## 📦 Repository Structure

```
├── AnimeVision.py             # Main Flask server, dual-channel bridge & endpoints
├── engine/                    # Cognitive awareness engine package
│   ├── __init__.py
│   ├── perception.py          # Multimodal vision & Windows OS sensor
│   ├── brain.py               # State machine, ignoring timer & context injection
│   └── dispatcher.py          # Delivery pipeline, rate-limiting & deduplication
├── test_awareness.py          # Standalone 54-test unit and mock suite
├── extensions/                # Pre-configured SillyTavern integrations
│   ├── Extension-MateEngine/  # Browser UI extension (transient bubble poller)
│   └── mateengine-chat-plugin/# Server chat plugin (port 8000)
├── requirements.txt           # Python dependencies
├── .env.example               # Template environment configuration
└── README.md                  # System documentation
```

---

## ⚙️ Ports & Endpoints

| Port | Service | Role | Key Endpoints |
| --- | --- | --- | --- |
| `5001` | **AlphaVision Bridge** | OpenAI-compatible Vision API & Perception daemon | `/v1/chat/completions`<br>`/spontaneous_pop`<br>`/last_ambient_poke`<br>`/health` |
| `8000` | **SillyTavern** | Chat frontend, character cards & prompt presets | `/api/plugins/mateengine-chat/*` |
| `8787` | **MateEngine** | Modded Unity desktop avatar runtime | `/api/chat`<br>`/api/lipsync` |

---

## 🚀 Quick Start Guide

### 1. Installation

```bash
git clone https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git
cd AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-

python -m venv alpha_env
alpha_env\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy `.env.example` to `.env` and fill in your Gemini API key:

```bash
copy .env.example .env
```

```ini
GEMINI_API_KEY=your_actual_gemini_api_key_here
MATE_ENGINE_PORT=8787
SILLY_TAVERN_PORT=8000
ALPHA_VISION_PORT=5001
```

### 3. Deploy SillyTavern Extensions

Copy the bundled extensions to your SillyTavern directory:
* Copy `extensions/Extension-MateEngine` to `SillyTavern/public/scripts/extensions/third-party/Extension-MateEngine`
* Copy `extensions/mateengine-chat-plugin` to `SillyTavern/plugins/mateengine-chat-plugin`

### 4. Run the Test Suite

Verify all 54 tests pass without requiring hardware or API keys:

```bash
python test_awareness.py
```

### 5. Launch AlphaVision Bridge

```bash
python AnimeVision.py
```

### 6. SillyTavern Settings

In SillyTavern's **AI Response Configuration**:
* **API:** `Chat Completion`
* **Chat Completion Source:** `Custom (OpenAI-compatible)`
* **Custom Endpoint (Base URL):** `http://127.0.0.1:5001/v1`
* **Model ID:** `gpt-3.5-turbo`
* **Streaming:** `Disabled` (or `Enabled` via SSE bridge)

---

## 🧪 Testing & Verification

The suite in `test_awareness.py` covers:
* Snapshot dataclass field defaults & serialization
* Presence detection, head yaw/pitch, and significance gate transitions
* Internal state persistence & JSON round-tripping
* Ignoring timer triggers (`ignoring_coding`, `ignoring_browsing`, `ignoring_physics`)
* Short-term ambient poke storage, 90s expiration, and author's note formatting
* Hardware fail-safe fallbacks (isolated camera worker and headless degradation)
* GDI screen grabber safety & suppression of cursor-flickering `CAPTUREBLT`
* Pipe character candidate stripping and strict 15-word boundary truncation
* Rate limiting (15s floor) and deduplication (60s window)

---

## 📜 License

Distributed under the **MIT License**.
