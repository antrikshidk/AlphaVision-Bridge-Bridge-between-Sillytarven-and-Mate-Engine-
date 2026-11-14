# AlphaVision Bridge

> **A multimodal, dual-channel cognitive bridge connecting SillyTavern + MateEngine powered by Google Gemini**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-78%20Passing-brightgreen?logo=pytest)](test_awareness.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Gemini](https://img.shields.io/badge/Powered%20by-Gemini%20Flash-orange?logo=google)](https://ai.google.dev/)
[![SillyTavern](https://img.shields.io/badge/SillyTavern-Extension-purple)](extensions/Extension-MateEngine/)
[![Platform](https://img.shields.io/badge/Platform-Windows-blue?logo=windows)](https://www.microsoft.com/windows)

AlphaVision transforms a 3D desktop companion (MateEngine / DesktopMate) into a **living, proactive ambient entity** that:

- 👁️ **Sees** your workspace through webcam + screen capture (OpenCV / MediaPipe)
- 🧠 **Thinks** independently via a hysteresis-debounced cognitive state machine
- 💬 **Pokes you spontaneously** with punchy one-liner remarks in MateEngine's speech bubble
- 🔄 **Responds conversationally** when you open the SillyTavern chat box, with full context of what she just said

No manual triggers. No prompt engineering. Zero-latency dual-channel delivery.

---

## Table of Contents

- [How It Works](#-how-it-works)
- [Architecture Diagram](#-architecture-diagram)
- [System Requirements](#-system-requirements)
- [Installation](#-installation)
  - [1 · Python Bridge Setup](#1--python-bridge-setup)
  - [2 · SillyTavern Extension + Plugin](#2--sillytavern-extension--plugin)
  - [3 · MateEngine Runtime + Unity DLL Patch](#3--mateengine-runtime--unity-dll-patch)
  - [4 · SillyTavern API Configuration](#4--sillytavern-api-configuration)
  - [5 · One-Click Launch](#5--one-click-launch)
- [Configuration Reference](#-configuration-reference)
- [Endpoint Reference](#-endpoint-reference)
- [Test Suite](#-test-suite)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)

---

## How It Works

AlphaVision runs as a **subconscious background engine** — a silent Python service (`:5001`) that sits between SillyTavern and MateEngine and autonomously orchestrates two distinct communication channels:

| Channel | Trigger | Delivery Path | Purpose |
|---------|---------|---------------|---------|
| **A — Ambient Pokes** | Brain engine fires spontaneous remark | `Dispatcher → POST :8787/api/chat` + `/spontaneous_pop` queue | Persistent desktop presence — teasing, nagging, one-liners |
| **B — Direct Chat** | User types in MateEngine input box | `MateEngine → SillyTavern plugin → Gemini → POST :8787/api/chat` | Full conversational back-and-forth with Gemini character |

The two channels share a **90-second ambient memory buffer** (`last_ambient_poke`): if she just poked you and you open the chat to reply, she remembers exactly what she said.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        AlphaVision Dual-Channel Bridge                       │
│                              AnimeVision.py (:5001)                          │
└─────────────────────────────────────┬────────────────────────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
   ┌──────────────────┐   ┌──────────────────────┐   ┌────────────────────┐
   │ engine/           │   │  engine/brain.py      │   │  engine/           │
   │ perception.py     │   │                       │   │  dispatcher.py     │
   │                   │   │  Cognitive State      │   │                    │
   │  ·  Webcam        │──▶│  Machine              │──▶│  Delivery Pipeline │
   │  ·  Screen grab   │   │  ·  Hysteresis        │   │  ·  Rate-limited   │
   │  ·  Win32 idle    │   │  ·  Rate limiting     │   │  ·  Dedup 60s/120s │
   │  ·  Window titles │   │  ·  State tagging     │   │  ·  Retry on fail  │
   └──────────────────┘   │  ·  Gemini Flash Lite  │   └────────────────────┘
                           └──────────────────────┘
                                                               │
                     ┌─────────────────────────────────────────┤
                     │                                         │
                     ▼                                         ▼
       ┌─────────────────────────┐             ┌──────────────────────────┐
       │ Channel A: Ambient Poke │             │ Channel B: Direct Chat   │
       │                         │             │                          │
       │  GET /spontaneous_pop   │             │  POST /api/chat          │
       │  → Extension polls      │             │  → MateEngine input box  │
       │  → deliverInChatPoke()  │             │  → SillyTavern plugin    │
       │  → SillyTavern chat     │             │  → Gemini generation     │
       │                         │             │  → POST :8787/api/chat   │
       └─────────────────────────┘             └──────────────────────────┘
                     │                                         │
                     └──────────────────┬──────────────────────┘
                                        ▼
                          ┌─────────────────────────┐
                          │  MateEngine  (:8787)     │
                          │  Unity Runtime           │
                          │  · Speech bubbles        │
                          │  · Lip sync / visemes    │
                          │  · Facial expressions    │
                          │  · Chat dialogue window  │
                          └─────────────────────────┘
```

---

## System Requirements

| Requirement | Version |
|-------------|---------|
| Windows | 10 / 11 (Win32 DWM API required) |
| Python | 3.10+ |
| MateEngine / DesktopMate | Modded build with `Assembly-CSharp.dll` patch |
| SillyTavern | Latest (`main` branch) |
| Google Gemini API Key | `gemini-2.5-flash-lite` or `gemini-1.5-flash` |
| Webcam | Optional — falls back gracefully to OS-only telemetry |

---

## Installation

### 1 · Python Bridge Setup

```cmd
git clone https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git
cd AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-

python -m venv alpha_env
call alpha_env\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your Gemini API key:

```env
GEMINI_API_KEY=your_key_here
MATE_ENGINE_PORT=8787
SILLYTAVERN_PORT=8000
ALPHA_VISION_PORT=5001
```

### 2 · SillyTavern Extension + Plugin

**Extension (browser-side — runs the AI generation and polls for spontaneous thoughts):**
```
Copy extensions/Extension-MateEngine/
  → SillyTavern/public/scripts/extensions/third-party/Extension-MateEngine/
```

**Server Plugin (Node.js relay — exposes `/api/plugins/mateengine-chat/chat`):**
```
Copy extensions/mateengine-chat-plugin/
  → SillyTavern/plugins/mateengine-chat-plugin/
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

**Silent background launch (recommended for daily use):**
```cmd
double-click launch_alphavision.bat
```

**Foreground terminal launch (debugging):**
```cmd
call alpha_env\Scripts\activate
python AnimeVision.py
```

**Stop everything cleanly:**
```cmd
double-click Stop_AlphaVision.bat
```

---

## Configuration Reference

AlphaVision exposes a live config sync endpoint. The SillyTavern extension's **MateEngine Bridge** settings drawer writes directly to it:

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

**Environment Variables (`.env`):**

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Google AI Studio API key |
| `MODEL_NAME` | `gemini-2.5-flash-lite` | Gemini model for both chat and brain |
| `MATE_ENGINE_PORT` | `8787` | MateEngine HTTP bridge port |
| `SILLYTAVERN_PORT` | `8000` | SillyTavern web UI port |
| `FLASK_PORT` | `5001` | AlphaVision Flask server port |

---

## Endpoint Reference

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

---

## Test Suite

AlphaVision ships with a comprehensive offline test suite — no camera, no MateEngine, no live API calls required.

```cmd
call alpha_env\Scripts\activate
python test_awareness.py
```

**78 tests across 18 test classes:**

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
| `TestLowLightAndHighResVision` | CLAHE + gamma boost, 512px capture, study window tagging |
| `TestAntiSleepNagAndPrecedence` | Sleep-nag cooldown (35min), study tag priority over `late_night` |
| `TestConfigSyncAndSpontaneousToggle` | Live config endpoint, brain/dispatcher enable/disable |

```
----------------------------------------------------------------------
Ran 78 tests in 10.2s

OK
```

---

## Troubleshooting

### Mouse cursor flickering
**Cause:** Win32 GDI `CAPTUREBLT` flag forces DWM to redraw the hardware cursor on every screen grab.  
**Fix:** Already applied — `mss.windows.CAPTUREBLT = 0` in `engine/perception.py` and screen capture is capped to ≥650ms intervals.

### Webcam shows `[PERCEPTION] Webcam unavailable/busy`
**Cause:** Another app (Discord, Zoom, OBS) holds an exclusive lock on camera device 0.  
**Fix:** `CameraWorker` uses `cv2.CAP_DSHOW` with a 2.0s init timeout. AlphaVision continues with OS-only telemetry (window titles + idle duration) — vision is optional.

### MateEngine speech bubble stuck on `...`
**Cause:** The SillyTavern extension's `processPendingChats()` was sending `showBubble: false` because `desktopBubblesEnabled` was `false` by default.  
**Fix (shipped in v1.6):** Direct conversational replies now explicitly pass `showBubble=true` and `spontaneous=false`. The `...` loading state is always cleared.

### Spontaneous remarks not appearing in SillyTavern chat
**Cause:** `inChatPokeEnabled` defaulted to `false`; and the chat bridge was clearing the thought queue on every user message.  
**Fix (shipped in v1.6):** `inChatPokeEnabled` now defaults to `true`. The queue-clearing loop is removed — thoughts are preserved until SillyTavern polls them.

### `502 Bad Gateway` from SillyTavern
**Cause:** Flask process crashed — missing `GEMINI_API_KEY` or port 5001 conflict.  
**Fix:** Check `.env` exists and has a valid key. `AnimeVision.py` auto-reclaims port 5001 on startup. Inspect `logs/alphavision.log`.

### `[SillyTavernAdapter] No ChatBot found in scene!`
This is a **non-fatal** log line emitted by the unmodded MateEngine adapter. AlphaVision uses the patched `MateEngineBridge` class instead — this warning can be safely ignored.

### Duplicate speech bubbles
**Cause:** Chat box sensor flickering OPEN/CLOSED on sub-pixel rendering.  
**Fix:** `ChatBoxDebouncer` enforces two-way hysteresis (0.6s open / 1.0s close). `Dispatcher` enforces a 15s rate-limit and 60s duplicate filter.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development setup guide.

Quick summary:
1. Fork & clone the repo
2. Create a venv with `requirements.txt`
3. Run `python test_awareness.py` — all 78 tests must pass before you open a PR
4. Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages

---

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

<div align="center">
<sub>Built by Antriksh · Powered by Google Gemini · For MateEngine / DesktopMate on Windows</sub>
</div>
