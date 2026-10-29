# 🌌 AlphaVision Bridge

An ultra-low latency, multimodal dual-vision cognitive bridge and ambient awareness engine connecting **SillyTavern** and **MateEngine** (DesktopMate) using Google Gemini Multimodal models (`gemini-2.5-flash-lite` / `gemini-1.5-flash`).

AlphaVision transforms your 3D desktop companion into a living, proactive ambient entity capable of seeing your workspace via computer vision and Windows OS telemetry, thinking independently, and conversing naturally with zero prompt engineering or manual triggers.

---

## 📑 Table of Contents

- [Core Architecture Overview](#-core-architecture-overview)
  - [1. Sensory Perception Layer (`engine/perception.py`)](#1-sensory-perception-layer-engineperceptionpy)
  - [2. Cognitive State Machine (`engine/brain.py`)](#2-cognitive-state-machine-enginebrainpy)
  - [3. Dual-Channel Dispatcher (`engine/dispatcher.py` & `AnimeVision.py`)](#3-dual-channel-dispatcher-enginedispatcherpy--animevisionpy)
- [Architecture Diagram](#-architecture-diagram)
- [Prerequisites & System Requirements](#-prerequisites--system-requirements)
- [Step-by-Step Installation & Configuration](#-step-by-step-installation--configuration)
  - [Step 1: Python Awareness Bridge Setup](#step-1-python-awareness-bridge-setup)
  - [Step 2: SillyTavern Extension & Plugin Deployment](#step-2-sillytavern-extension--plugin-deployment)
  - [Step 3: MateEngine Runtime Setup](#step-3-mateengine-runtime-setup)
  - [Step 4: SillyTavern UI API Configuration](#step-4-sillytavern-ui-api-configuration)
  - [Step 5: Automated One-Click Production Deployment](#step-5-automated-one-click-production-deployment)
- [Verification & Automated Test Suite](#-verification--automated-test-suite)
- [Network Ports & Endpoint Reference](#-network-ports--endpoint-reference)
- [Troubleshooting & Edge Cases](#-troubleshooting--edge-cases)
- [License](#-license)

---

## 🧠 Core Architecture Overview

AlphaVision operates as an asynchronous, event-driven subconscious system designed to eliminate high-frequency polling, token waste, and terminal clutter.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       AlphaVision Dual-Channel Bridge                       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────┐                             ┌───────────────────────┐
│ Channel A: Desktop    │                             │ Channel B: Direct     │
│ Transient Pokes       │                             │ Conversational Chat   │
│                       │                             │                       │
│ • GET /spontaneous_pop│                             │ • POST /v1/chat/...   │
│ • Pushes to :8787     │                             │ • Injects 90s context │
│ • Zero ST chat logs   │                             │ • Dialogue generation │
└───────────────────────┘                             └───────────────────────┘
```

### 1. Sensory Perception Layer (`engine/perception.py`)
- **Non-Blocking Hardware Threading:** `cv2.VideoCapture` is strictly decoupled from the main event loop and Flask request threads. It runs in an isolated `CameraWorker` daemon thread using the Windows DirectShow backend (`cv2.CAP_DSHOW`).
- **2.0-Second Fail-Safe Timeout:** If the webcam is in use, unplugged, or takes longer than 2.0s to initialize, the worker automatically logs a warning and transitions to **headless OS sensor mode** without stalling system boot.
- **DWM Flicker-Free Screen Grabber (`SafeScreenGrabber`):** Windows Desktop Window Manager normally hides and redraws the mouse cursor on every screen grab if `CAPTUREBLT` (0x40000000) is enabled. AlphaVision suppresses `CAPTUREBLT = 0` and enforces a minimum 650ms sampling floor, while instantly grabbing frames upon foreground window focus switches (`win32gui.GetForegroundWindow()`).
- **Context Tagging:** Classifies active foreground windows into domain tags:
  - `coding`: VS Code, Visual Studio, PyCharm, Sublime, Vim, Neovim.
  - `physics`: Physics homework, arXiv preprints, quantum mechanics simulations, Desmos, GeoGebra, Mathematica, MATLAB.
  - `gaming`: Steam, Valorant, fullscreen games.
  - `browsing`: Chrome, Firefox, Edge, Brave, Opera.
  - `media`: YouTube, Netflix, Spotify, Crunchyroll.

### 2. Cognitive State Machine (`engine/brain.py`)
- **Subconscious Cognitive Loop:** Ticks every 30s to evaluate whether an ambient, unprompted remark is warranted based on user activity transitions, idle time, and late-night habits.
- **Hysteresis-Debounced Chat Box Detection:** Utilizes a two-way confirmation filter (`ChatBoxDebouncer`) requiring $\ge 0.6$s of sustained positive detection before marking the chat box as OPEN, and $\ge 1.0$s of sustained absence before marking it CLOSED, completely preventing rapid flip-flop flickering.
- **Structured Annoying / Nagging Triggers:**
  - *Ignoring Timer:* If the user has been active on the desktop (`presence=True` and `idle_seconds < 180`) but hasn't chatted with the companion for $>10\text{--}15$ minutes, AlphaVision triggers an attention-seeking/nagging poke:
    - *Coding:* `"You've been staring at that code forever, take a break and talk to me."`
    - *Browsing:* `"Are you just going to leave me sitting here while you browse?"`
    - *Physics:* Teases the user about physics equations and demands a chat break.
    - *Gaming:* Teases the user about prioritizing games over their companion.
- **90-Second Short-Term Memory Buffer (`last_ambient_poke`):**
  - When a spontaneous remark is generated and popped, it is recorded in-memory:
    ```python
    last_ambient_poke = {
        "text": clean_text,
        "timestamp": time.time(),
        "context": window_context
    }
    ```
  - If the user opens the chat box and sends a message within **90 seconds**, the bridge enriches the prompt:
    ```text
    [Context: You just teased the user with: "<poke_text>". The user just opened the chat to respond.]
    ```
  - The context buffer expires immediately after the first user response so that subsequent dialogue returns to standard character flow.
- **Output Sanitization & Formatting:**
  - Strips candidate options separated by pipes (`|`), selecting only the first clean thought.
  - Enforces strict truncation to at most **15 words** cleanly capped at sentence boundaries to eliminate speech bubble overflow.

### 3. Dual-Channel Dispatcher (`engine/dispatcher.py` & `AnimeVision.py`)
- **Channel A (Transient Desktop Pokes):**
  - Delivered directly to MateEngine (:8787) via `ChatBot.DisplayAIResponse` and cached in `spontaneous_thought_queue` (retrieved via `GET /spontaneous_pop`).
  - Remarks are displayed strictly in the 3D desktop speech bubble and **never post into SillyTavern's message log as system chat messages**.
- **Channel B (Direct Conversational Chat):**
  - Handles user chat messages through `/v1/chat/completions`.
  - Resets the nagging/ignoring timer, enriches the prompt with the 90s poke context, flushes the spontaneous queue, and returns the character's direct dialogue reply.
  - **Never** returns a pre-generated ambient remark as the reply to an active user message.

---

## 🏗️ Architecture Diagram

```
                        ┌────────────────────────┐
                        │ DirectShow Camera / OS │
                        │ Desktop Window Hooks   │
                        └───────────┬────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   PERCEPTION LAYER (engine/perception.py)              │
│  • CameraWorker (CAP_DSHOW + 2s initialization timeout)                │
│  • SafeScreenGrabber (CAPTUREBLT=0 cursor flicker suppression)         │
│  • Window Classifier (Coding, Physics, Gaming, Media, Browsing)        │
│  • ChatBoxDebouncer (0.6s Open / 1.0s Close two-way hysteresis)        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ PerceptionSnapshot Queue
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   COGNITIVE BRAIN (engine/brain.py)                    │
│  • InternalState (focused, chilling, away, returned, sleep_deprived)   │
│  • Ignoring Timer (>10m active on PC without chatting)                 │
│  • In-Memory last_ambient_poke Buffer (90s auto-expiry)                │
│  • Single-Sentence <= 15 Words Pipe ('|') Filter                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ clean_thought
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  DISPATCHER PIPELINE (engine/dispatcher.py)            │
│  • 15s Throttle Gate + 60s Duplicate Message Suppressor                │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
      Channel A: Desktop Bubble         Channel B: Context-Aware Chat
                    │                                │
                    ▼                                ▼
         ┌─────────────────────┐          ┌─────────────────────┐
         │ MateEngineX.exe     │          │ SillyTavern (:8000) │
         │ Speech Bubble :8787 │          │ OpenAI Bridge :5001 │
         └─────────────────────┘          └─────────────────────┘
```

---

## 💻 Prerequisites & System Requirements

| Component | Minimum Requirement | Recommended | Notes |
| :--- | :--- | :--- | :--- |
| **Operating System** | Windows 10 (64-bit) | Windows 11 (64-bit) | Required for Win32 API window hooks, DWM screen capture, and DirectShow camera capture. |
| **Python** | 3.10.0 | 3.10.4 - 3.11.x | Managed via isolated virtual environment (`alpha_env`). |
| **Node.js** | 18.x LTS | 20.x LTS | Required to run SillyTavern background server. |
| **SillyTavern** | v1.12.0+ | v1.18.0+ | Frontend chat interface and character preset manager. |
| **MateEngine** | Modded Unity build | Modded Win32 build | Runs desktop avatar on port `8787` (`/api/chat`, `/api/lipsync`). |
| **AI Backend** | Gemini API Key | `gemini-2.5-flash-lite` | Free/Tier API key from Google AI Studio. |

---

## 🛠️ Step-by-Step Installation & Configuration

### Step 1: Python Awareness Bridge Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git
   cd AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-
   ```

2. **Create and activate the virtual environment:**
   ```cmd
   python -m venv alpha_env
   call alpha_env\Scripts\activate
   ```

3. **Install the dependencies:**
   ```cmd
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   Copy `.env.example` to `.env`:
   ```cmd
   copy .env.example .env
   ```
   Edit `.env` and fill in your Gemini API key:
   ```ini
   GEMINI_API_KEY=AIzaSyYourActualGeminiKeyHere
   MATE_ENGINE_PORT=8787
   SILLY_TAVERN_PORT=8000
   ALPHA_VISION_PORT=5001
   ```

---

### Step 2: SillyTavern Extension & Plugin Deployment

AlphaVision includes pre-configured, patched SillyTavern integrations inside the `extensions/` directory.

```
AlphaVision-Bridge/
└── extensions/
    ├── Extension-MateEngine/     ──> SillyTavern/public/scripts/extensions/third-party/Extension-MateEngine/
    └── mateengine-chat-plugin/   ──> SillyTavern/plugins/mateengine-chat-plugin/
```

1. **Deploy the Client Extension (UI & Desktop Bubble Poller):**
   Copy `extensions/Extension-MateEngine` to your SillyTavern third-party extensions directory:
   ```cmd
   xcopy /E /I /Y extensions\Extension-MateEngine path\to\SillyTavern\public\scripts\extensions\third-party\Extension-MateEngine
   ```
   *Features included:*
   - Polls `GET http://127.0.0.1:5001/spontaneous_pop` every 250ms.
   - Pushes spontaneous remarks exclusively to the MateEngine desktop bubble via port `8787`.
   - **Zero Chat Log Pollution:** Never creates `SillyTavern System` chat entries.
   - Checks `GET http://127.0.0.1:5001/last_ambient_poke` before generating user replies to inject the 90s teasing context.

2. **Deploy the Server Chat Plugin:**
   Copy `extensions/mateengine-chat-plugin` to your SillyTavern server plugins directory:
   ```cmd
   xcopy /E /I /Y extensions\mateengine-chat-plugin path\to\SillyTavern\plugins\mateengine-chat-plugin
   ```
   *Features included:*
   - Exposes `/api/plugins/mateengine-chat/chat` on port `8000`.
   - Bridges messages between MateEngine's in-game input UI and SillyTavern character presets.

---

### Step 3: MateEngine Runtime Setup

1. Use the modded MateEngine Unity client build configured with active local API support.
2. Verify that the runtime opens a local HTTP listener on port **`8787`**:
   - `POST /api/chat`: Receives `{ text, characterName, emotion, showBubble }` to display speech bubbles and trigger visemes.
   - `POST /api/lipsync`: Direct phoneme/viseme lip-sync streaming.
3. Ensure MateEngine's chat input box forwards messages to `http://127.0.0.1:5001/v1` or `http://127.0.0.1:8000/api/plugins/mateengine-chat/chat`.

---

### Step 4: SillyTavern UI API Configuration

Open the SillyTavern web interface (`http://127.0.0.1:8000`) and configure the API completion settings:

1. Click **AI Response Configuration** (Plug icon).
2. Set **Main API** to `Chat Completion`.
3. Set **Chat Completion Source** to `Custom (OpenAI-compatible)`.
4. Set **Custom Endpoint (Base URL)** to:
   ```text
   http://127.0.0.1:5001/v1
   ```
5. Set **Custom Model ID** to:
   ```text
   gpt-3.5-turbo
   ```
6. Set **Streaming** to `Disabled` (or `Enabled` for SSE streaming mode).
7. Under **Extensions**, ensure `MateEngine` is checked and enabled with Bridge URL set to `http://127.0.0.1:8787`.

---

### Step 5: Automated One-Click Production Deployment

Instead of manually keeping three visible command prompts open on your desktop, use the included production process supervisor to start the stack silently:

```
[ start_alphavision.bat ] ──> [ runner.vbs ] ──> [ supervisor.ps1 ]
                                                    │
                 ┌──────────────────────────────────┼──────────────────────────────────┐
                 ▼                                  ▼                                  ▼
      [ SillyTavern (Node.js) ]           [ AnimeVision (pythonw) ]         [ MateEngineX.exe ]
         Headless port :8000                 Headless port :5001              Foreground 3D Avatar
```

1. **Start all services silently (0 visible terminal windows):**
   Double-click `start_alphavision.bat` or run:
   ```cmd
   wscript.exe runner.vbs
   ```
   - Automatically cleans up orphaned processes and reclaims ports `5001`, `8000`, and `8787`.
   - Spawns SillyTavern as a background Node.js daemon.
   - Spawns AlphaVision via `pythonw.exe` with rotating file logging to `logs/alphavision.log`.
   - Launches `MateEngineX.exe` as the sole visible foreground application.

2. **Graceful Shutdown:**
   Double-click `Stop_AlphaVision.bat` to cleanly terminate all processes and release camera handles.

---

## 🧪 Verification & Automated Test Suite

AlphaVision includes a comprehensive unit and integration mock test suite that executes completely offline without requiring physical camera hardware, an active MateEngine instance, or live Gemini API calls.

To run the full suite:

```cmd
call alpha_env\Scripts\activate
python test_awareness.py
```

### Coverage (54 Passing Tests):
- **Perception Snapshot & Significance Gate:** Tests 10-second heartbeats, presence state changes, and fatigue cues.
- **Window Tagging:** Evaluates strict precedence rules (e.g., `"YouTube - Chrome"` correctly resolves to `youtube`, while `"arXiv:2401"` resolves to `physics`).
- **Camera Fail-Safe:** Verifies non-blocking `CameraWorker` thread safety, decoupled frame cloning, and seamless fallback to OS sensor mode when camera is offline.
- **DWM Anti-Flickering:** Asserts `CAPTUREBLT = 0` in `mss` and tests the 650ms minimum interval throttle.
- **Chat Box Debouncer:** Asserts that 150ms state flapping is rejected, while sustained 600ms transitions switch cleanly.
- **Dual-Channel Context Injection:** Tests 90s poke retention, author's note formatting, and immediate cache wiping on user reply.
- **Ignoring & Nagging Triggers:** Verifies tag-specific nagging triggers (`ignoring_coding`, `ignoring_browsing`, `ignoring_physics`) when user has been active for $>10$ minutes without chatting.
- **Dispatcher Deduplication:** Tests 15-second rate-limiting and 60-second duplicate message suppression.

```text
Ran 54 tests in 9.193s

OK
============================================================
  AlphaVision Awareness Engine — Mock Test Suite
============================================================
```

---

## 🌐 Network Ports & Endpoint Reference

| Port | Service | Protocol | Key Endpoints | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`5001`** | **AlphaVision Bridge** | HTTP / REST | `POST /v1/chat/completions` | OpenAI-compatible chat completion bridge with dual-vision snapshot injection. |
| | | | `GET /spontaneous_pop` | O(1) non-blocking release of queued ambient thoughts for desktop bubbles. |
| | | | `GET /last_ambient_poke` | Returns the active ambient poke within 90s for conversational context. |
| | | | `GET /health` | Bridge and subconscious health check. |
| **`8000`** | **SillyTavern** | HTTP / WebSocket | `/` | Web conversational frontend and prompt generator. |
| | | | `POST /api/plugins/mateengine-chat/chat` | Server-side relay for companion dialogue. |
| **`8787`** | **MateEngine** | HTTP / REST | `POST /api/chat` | Modded Unity endpoint for speech bubbles, visemes, and emotional expressions. |
| | | | `POST /api/lipsync` | Direct audio/phoneme lip-sync hook. |

---

## 🔍 Troubleshooting & Edge Cases

### 1. Windows Mouse Cursor Flickering
- **Cause:** Win32 GDI screen grabbing with `CAPTUREBLT` (0x40000000) forces Windows DWM to hide and redraw the hardware cursor on every grab.
- **Resolution:** In `engine/perception.py`, `CAPTUREBLT` is suppressed to 0, and screen sampling is capped to 650ms intervals or foreground focus change events.

### 2. Camera Access Hangs or "Webcam Unavailable"
- **Cause:** Windows Media Foundation can deadlock if another application (Discord, Zoom) has locked device 0.
- **Resolution:** `CameraWorker` uses `cv2.CAP_DSHOW` and enforces a 2.0-second initialization timeout. If inaccessible, the system logs:
  ```text
  [PERCEPTION] Webcam unavailable/busy. Running headless with OS sensors.
  ```
  AlphaVision continues operating seamlessly using window titles and idle telemetry.

### 3. Duplicate Speech Bubbles on Desktop
- **Cause:** Chat box sensor flickering OPEN/CLOSED on sub-pixel rendering.
- **Resolution:** `ChatBoxDebouncer` enforces two-way hysteresis (0.6s Open / 1.0s Close). In addition, `Dispatcher` enforces a 15-second rate limit and 60-second duplicate message filter.

### 4. Text Overflow in MateEngine Speech Bubbles
- **Cause:** LLM generates candidate thoughts joined with pipe characters (`"Option A | Option B"`), or generates multi-paragraph responses.
- **Resolution:** `engine/brain.py`'s `clean_thought()` function automatically discards all but the first candidate and cleanly caps length at sentence boundaries under 15 words.

### 5. `502 Bad Gateway` in SillyTavern
- **Cause:** Flask process terminated early due to missing `GEMINI_API_KEY` or port 5001 conflict.
- **Resolution:** Ensure `.env` exists in the repository root with a valid key. `AnimeVision.py` automatically checks and reclaims port 5001 on startup. Inspect `logs/alphavision.log` for runtime exceptions.

---

## 📜 License

Distributed under the **MIT License**. See [LICENSE](file:///C:/DriveD/mate_engine/MateEngineX_Data/alpha_env/LICENSE) for details.
