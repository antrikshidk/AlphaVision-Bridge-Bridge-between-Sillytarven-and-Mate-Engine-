<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/banner.svg">
  <img alt="AlphaVision Bridge" src="docs/banner.svg" width="100%">
</picture>

<br>

[![CI Tests](https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/actions/workflows/tests.yml/badge.svg)](https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-81%20Passing-brightgreen?logo=pytest)](test_awareness.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Gemini](https://img.shields.io/badge/Powered%20by-Gemini%20Flash-4285F4?logo=google&logoColor=white)](https://ai.google.dev/)
[![SillyTavern](https://img.shields.io/badge/SillyTavern-Extension-8B5CF6)](extensions/Extension-MateEngine/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows&logoColor=white)](https://www.microsoft.com/windows)

**A multimodal, dual-channel cognitive bridge connecting SillyTavern + MateEngine powered by Google Gemini**

[Getting Started](#-installation) · [How It Works](#-how-it-works) · [Configuration](#-configuration-reference) · [Contributing](CONTRIBUTING.md)

</div>

---

## ✨ What Is This?

AlphaVision transforms a 3D desktop companion ([MateEngine](https://store.steampowered.com/app/3410490/Desktop_Mate/) / DesktopMate) into a **living, proactive ambient entity** that:

| | Feature | Description |
|---|---------|-------------|
| 👁️ | **Sees** | Your workspace through webcam + screen capture with PiP composite frames (OpenCV / MediaPipe) |
| 🧠 | **Thinks** | Independently via a hysteresis-debounced cognitive state machine with 90-second ambient memory |
| 💬 | **Pokes** | You spontaneously with punchy one-liner remarks in MateEngine's speech bubble |
| 🔄 | **Chats** | Conversationally through SillyTavern with full context of what she just said |

No manual triggers. No prompt engineering. Zero-latency dual-channel delivery.

---

## 📸 Screenshots

<div align="center">

| SillyTavern Chat | AlphaVision Terminal + MateEngine |
|:---:|:---:|
| ![SillyTavern Chat](docs/screenshots/sillytavern-chat.png) | ![AlphaVision Terminal](docs/screenshots/alphavision-terminal.png) |

<br>

<details>
<summary><strong>🖥️ Full Desktop View — MateEngine + Terminal Logs</strong></summary>
<br>

![MateEngine Desktop](docs/screenshots/mateengine-desktop.png)

*Alpha watches your screen, detects what you're doing, and comments spontaneously through MateEngine's speech bubble and SillyTavern's chat simultaneously.*

</details>

</div>

---

## 🔄 How It Works

AlphaVision runs as a **subconscious background engine** — a silent Python service (`:5001`) that sits between SillyTavern and MateEngine and autonomously orchestrates two distinct communication channels:

| Channel | Trigger | Delivery Path | Purpose |
|---------|---------|---------------|---------| 
| **A — Ambient Pokes** | Brain engine fires spontaneous remark | `Dispatcher → POST :8787/api/chat` + `/spontaneous_pop` queue | Persistent desktop presence — teasing, nagging, one-liners |
| **B — Direct Chat** | User types in MateEngine input box | `MateEngine → SillyTavern plugin → Gemini → POST :8787/api/chat` | Full conversational back-and-forth with Gemini character |

The two channels share a **90-second ambient memory buffer** (`last_ambient_poke`): if she just poked you and you open the chat to reply, she remembers exactly what she said.

---

## 🏗️ Architecture

```mermaid
graph TD
    subgraph Perception["👁️ Perception Engine"]
        CAM["📷 Webcam<br/><small>OpenCV + MediaPipe</small>"]
        SCR["🖥️ Screen Capture<br/><small>Win32 DWM / MSS</small>"]
        IDLE["⏱️ Idle Detection<br/><small>win32api</small>"]
        WIN["📋 Window Titles<br/><small>win32gui</small>"]
    end

    subgraph Brain["🧠 Cognitive State Machine"]
        STATE["State Tagger<br/><small>coding · physics · gaming<br/>browsing · studying</small>"]
        HYS["Hysteresis Engine<br/><small>Debounce + Rate Limit</small>"]
        GEN["Gemini Flash<br/><small>Remark Generator</small>"]
    end

    subgraph Dispatch["📡 Dispatcher"]
        RATE["Rate Limiter<br/><small>15s cooldown</small>"]
        DEDUP["Dedup Filter<br/><small>60s / 120s window</small>"]
        QUEUE["Thought Queue"]
    end

    subgraph Delivery["🎯 Dual-Channel Delivery"]
        CHA["<strong>Channel A</strong><br/>Ambient Pokes<br/><small>GET /spontaneous_pop</small>"]
        CHB["<strong>Channel B</strong><br/>Direct Chat<br/><small>POST /v1/chat/completions</small>"]
    end

    subgraph Targets[""]
        ME["🎭 MateEngine :8787<br/><small>Speech Bubbles · Lip Sync<br/>Expressions · Dialogue UI</small>"]
        ST["💬 SillyTavern :8000<br/><small>Chat History · Extensions<br/>Character Context</small>"]
    end

    CAM --> STATE
    SCR --> STATE
    IDLE --> STATE
    WIN --> STATE
    STATE --> HYS
    HYS --> GEN
    GEN --> RATE
    RATE --> DEDUP
    DEDUP --> QUEUE
    QUEUE --> CHA
    QUEUE --> CHB
    CHA --> ST
    CHA --> ME
    CHB --> ME
    CHB --> ST

    style Perception fill:#0d1117,stroke:#58a6ff,stroke-width:2px,color:#c9d1d9
    style Brain fill:#0d1117,stroke:#bc8cff,stroke-width:2px,color:#c9d1d9
    style Dispatch fill:#0d1117,stroke:#f778ba,stroke-width:2px,color:#c9d1d9
    style Delivery fill:#0d1117,stroke:#3fb950,stroke-width:2px,color:#c9d1d9
    style Targets fill:#0d1117,stroke:#d29922,stroke-width:2px,color:#c9d1d9
```

<details>
<summary><strong>📊 Composite Vision Pipeline (v1.7)</strong></summary>

```
┌─────────────────────────────────────────────────┐
│          Composite Frame (1024 × 576)            │
│                                                  │
│   Full Desktop Screen Capture                    │
│   (scaled to 1024px width)                       │
│                                                  │
│                            ┌──────────────┐      │
│                            │  📷 WEBCAM   │      │
│                            │   280 × 210  │      │
│                            │  PiP Inset   │      │
│                            │  CLAHE + HDR │      │
│                            └──────────────┘      │
└─────────────────────────────────────────────────┘
  ↓ JPEG ~28KB  ↓ ~258 tokens  ↓ ~2.5s Gemini RTT
```

The PiP composite sends a **single image** to Gemini containing both what's on your screen and your webcam feed — cutting latency from ~15s (two separate uploads) to ~2.5s.

</details>

---

## 💻 System Requirements

| Requirement | Version |
|:--|:--|
| Windows | 10 / 11 (Win32 DWM API required) |
| Python | 3.10+ |
| MateEngine / DesktopMate | Modded build with `Assembly-CSharp.dll` patch |
| SillyTavern | Latest (`main` branch) |
| Google Gemini API Key | [Get one free](https://aistudio.google.com/apikey) |
| Webcam | Optional — falls back gracefully to OS-only telemetry |

---

## 🚀 Installation

### 1 · Python Bridge Setup

```bash
git clone https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git
cd AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-

python -m venv alpha_env
call alpha_env\Scripts\activate       # Windows CMD
pip install -r requirements.txt
```

Copy `.env.example` → `.env` and fill in your Gemini API key:

```env
GEMINI_API_KEY=your_key_here
MATE_ENGINE_PORT=8787
SILLYTAVERN_PORT=8000
ALPHA_VISION_PORT=5001
```

### 2 · SillyTavern Extension + Plugin

**Extension** (browser-side — runs AI generation and polls for spontaneous thoughts):
```
Copy  extensions/Extension-MateEngine/
  →   SillyTavern/public/scripts/extensions/third-party/Extension-MateEngine/
```

**Server Plugin** (Node.js relay — exposes `/api/plugins/mateengine-chat/chat`):
```
Copy  extensions/mateengine-chat-plugin/
  →   SillyTavern/plugins/mateengine-chat-plugin/
```

Restart SillyTavern. The **MateEngine Bridge** panel will appear in the Extensions drawer.

### 3 · MateEngine Runtime + Unity DLL Patch

AlphaVision requires a patched `Assembly-CSharp.dll` that exposes an HTTP server on port 8787 and routes incoming chat messages to the internal dialogue UI.

```powershell
cd unity_bridge_mod
powershell -ExecutionPolicy Bypass -File patch_mateengine_dll.ps1
```

The patcher uses `dnlib` to inject the `MateEngineBridge` HTTP listener class into the Unity runtime. After patching, place the patched DLL at:
```
MateEngineX_Data/Managed/Assembly-CSharp.dll
```

> **Verification:** When MateEngine launches, you should see in `%USERPROFILE%\AppData\LocalLow\Shinymoon\MateEngineX\Player.log`:
> ```
> [MateEngineBridge] ✔ Server started successfully on http://127.0.0.1:8787/
> ```

### 4 · SillyTavern API Configuration

In SillyTavern's **API settings**, set the connection type to **OpenAI-compatible** and point it at AlphaVision:

```
API URL:  http://127.0.0.1:5001/v1
Model:    gemini-2.5-flash-lite   (or any string — AlphaVision ignores it)
```

This routes SillyTavern's LLM calls through AlphaVision's bridge, which injects webcam snapshots and OS context before forwarding to Gemini.

### 5 · One-Click Launch

| Action | Command |
|--------|---------|
| **Silent background launch** (recommended) | Double-click `launch_alphavision.bat` |
| **Foreground terminal** (debugging) | `call alpha_env\Scripts\activate && python AnimeVision.py` |
| **Stop everything** | Double-click `Stop_AlphaVision.bat` |

---

## ⚙️ Configuration Reference

AlphaVision exposes a live config sync endpoint. The SillyTavern extension's **MateEngine Bridge** settings drawer writes directly to it:

<details>
<summary><strong>Extension Settings (synced to <code>/api/config</code>)</strong></summary>

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Master on/off for the entire bridge |
| `spontaneousPokesEnabled` | `true` | Allow autonomous ambient remarks from brain engine |
| `inChatPokeEnabled` | `true` | Pipe spontaneous remarks into SillyTavern active chat history |
| `desktopBubblesEnabled` | `true` | Show speech bubbles on MateEngine desktop avatar |
| `autoDismissBubbles` | `true` | Auto-clear speech bubble after timeout |
| `bubbleDismissTimeoutSec` | `5` | Seconds before auto-clear |
| `autoLipSync` | `true` | Trigger viseme/lip-sync on every message |
| `visionUrl` | `http://127.0.0.1:5001` | AlphaVision bridge address |
| `bridgeUrl` | `http://127.0.0.1:8787` | MateEngine bridge address |

</details>

<details>
<summary><strong>Environment Variables (<code>.env</code>)</strong></summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Google AI Studio API key |
| `MODEL_NAME` | `gemini-2.5-flash-lite` | Gemini model for both chat and brain |
| `MATE_ENGINE_PORT` | `8787` | MateEngine HTTP bridge port |
| `SILLYTAVERN_PORT` | `8000` | SillyTavern web UI port |
| `FLASK_PORT` | `5001` | AlphaVision Flask server port |

</details>

---

## 🔌 Endpoint Reference

<details>
<summary><strong>All API Endpoints</strong></summary>

| Port | Service | Endpoint | Method | Description |
|------|---------|----------|--------|-------------|
| **5001** | AlphaVision | `/v1/chat/completions` | POST | OpenAI-compatible chat bridge; injects vision context |
| | | `/spontaneous_pop` | GET | O(1) pop of next queued ambient thought |
| | | `/last_ambient_poke` | GET | Returns the active ambient poke (valid 90s window) |
| | | `/api/config` | POST | Live config sync from SillyTavern extension |
| | | `/health` | GET | Bridge and subconscious engine health check |
| **8000** | SillyTavern | `/` | GET | Web conversational frontend |
| | | `/api/plugins/mateengine-chat/chat` | POST | Server relay — MateEngine chat request |
| | | `/api/plugins/mateengine-chat/pending` | GET | Poll for pending user messages |
| | | `/api/plugins/mateengine-chat/response` | POST | Submit AI response for delivery |
| **8787** | MateEngine | `/api/chat` | POST | Speech bubble + dialogue UI delivery |
| | | `/api/status` | GET | Bridge health check |
| | | `/api/expression` | POST | Set facial expression directly |
| | | `/api/lipsync` | POST | Trigger viseme lip-sync |

</details>

---

## 🧪 Test Suite

AlphaVision ships with a comprehensive offline test suite — no camera, no MateEngine, no live API calls required.

```bash
call alpha_env\Scripts\activate
python test_awareness.py
```

```
--------------------------------------------------------------
Ran 81 tests in 10.3s

OK
```

<details>
<summary><strong>All 18 Test Classes (81 tests)</strong></summary>

| Test Class | What It Covers |
|------------|---------------|
| `TestPerceptionSnapshot` | Snapshot dataclass defaults and custom fields |
| `TestWindowTagging` | Tag precedence: `physics`, `youtube`, `coding`, `gaming`, fallback |
| `TestPerceptionEngineSignificanceGate` | 10s heartbeats, presence/window change significance |
| `TestInternalStatePersistence` | JSON state round-trip (save + restore) |
| `TestBrainStateMachine` | State transitions, rate-limiting, away/return/fatigue |
| `TestDispatcher` | Rate-limit guard, 60s/120s dedup, queue enqueue, unreachable engine |
| `TestChatBoxDebouncer` | 150ms flap rejection, 600ms open / 1000ms close transitions |
| `TestFullPipeline` | End-to-end: perception → brain → dispatcher → callback |
| `TestCameraWorkerFailSafe` | Non-blocking camera init timeout and fallback |
| `TestCleanThoughtAndFormatting` | Pipe-stripping, 15-word cap, BMP glyph sanitization |
| `TestSafeScreenGrabber` | CAPTUREBLT=0, 650ms throttle, foreground-window-triggered grab |
| `TestAmbientPokeContext` | 90s poke retention, expiry, immediate clear on reply |
| `TestIgnoringAndNaggingTriggers` | Tag-specific nagging triggers after 10min idle |
| `TestThoughtQueueAndInputRouting` | Duplicate rejection, context-leak stripping |
| `TestMediaPipeImportAndInChatSpontaneousPoking` | MediaPipe availability, in-chat poke formatting |
| `TestVisionGatingAndBubbleDismissal` | Vision intent gating, bubble scheduling, JS routing checks |
| `TestLowLightAndHighResVision` | 1024px composite screen + PiP webcam, CLAHE boost, degradation fallbacks |
| `TestAntiSleepNagAndPrecedence` | Sleep-nag cooldown (35min), study tag priority over `late_night` |
| `TestConfigSyncAndSpontaneousToggle` | Live config endpoint, brain/dispatcher enable/disable |

</details>

---

## 🔧 Troubleshooting

<details>
<summary><strong>Mouse cursor flickering</strong></summary>

**Cause:** Win32 GDI `CAPTUREBLT` flag forces DWM to redraw the hardware cursor on every screen grab.  
**Fix:** Already applied — `mss.windows.CAPTUREBLT = 0` in `engine/perception.py` and screen capture is capped to ≥650ms intervals.

</details>

<details>
<summary><strong>Webcam shows <code>[PERCEPTION] Webcam unavailable/busy</code></strong></summary>

**Cause:** Another app (Discord, Zoom, OBS) holds an exclusive lock on camera device 0.  
**Fix:** `CameraWorker` uses `cv2.CAP_DSHOW` with a 2.0s init timeout. AlphaVision continues with OS-only telemetry (window titles + idle duration) — vision is optional.

</details>

<details>
<summary><strong>MateEngine speech bubble stuck on <code>...</code></strong></summary>

**Cause:** The SillyTavern extension's `processPendingChats()` was sending `showBubble: false` because `desktopBubblesEnabled` was `false` by default.  
**Fix (shipped in v1.6):** Direct conversational replies now explicitly pass `showBubble=true` and `spontaneous=false`. The `...` loading state is always cleared.

</details>

<details>
<summary><strong>Spontaneous remarks not appearing in SillyTavern chat</strong></summary>

**Cause:** `inChatPokeEnabled` defaulted to `false`; and the chat bridge was clearing the thought queue on every user message.  
**Fix (shipped in v1.6):** `inChatPokeEnabled` now defaults to `true`. The queue-clearing loop is removed — thoughts are preserved until SillyTavern polls them.

</details>

<details>
<summary><strong><code>502 Bad Gateway</code> from SillyTavern</strong></summary>

**Cause:** Flask process crashed — missing `GEMINI_API_KEY` or port 5001 conflict.  
**Fix:** Check `.env` exists and has a valid key. `AnimeVision.py` auto-reclaims port 5001 on startup. Inspect `logs/alphavision.log`.

</details>

<details>
<summary><strong>Duplicate speech bubbles</strong></summary>

**Cause:** Chat box sensor flickering OPEN/CLOSED on sub-pixel rendering.  
**Fix:** `ChatBoxDebouncer` enforces two-way hysteresis (0.6s open / 1.0s close). `Dispatcher` enforces a 15s rate-limit and 60s duplicate filter.

</details>

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development setup guide.

**Quick start:**
1. Fork & clone the repo
2. Create a venv with `requirements.txt`
3. Run `python test_awareness.py` — all 81 tests must pass before you open a PR
4. Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

<div align="center">

**[⬆ Back to Top](#)**

<sub>Built by <a href="https://github.com/antrikshidk">Antriksh</a> · Powered by <a href="https://ai.google.dev/">Google Gemini</a> · For MateEngine / DesktopMate on Windows</sub>

</div>
