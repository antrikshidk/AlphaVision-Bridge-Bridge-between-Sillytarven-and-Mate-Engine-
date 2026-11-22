# Changelog

All notable changes to AlphaVision Bridge are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [v1.7.0] — 2026-11-22

### Added
- **Unified Composite Screen + Webcam Vision Pipeline** (`compose_screen_and_camera_bgr` & `compose_screen_and_camera`):
  - Captures full desktop screen (scaled to 1024px width for crystal-clear readability of code, PDF math formulas, and video) with a real-time Picture-in-Picture (PiP) webcam inset (280px) in the bottom-right corner.
  - PiP includes CLAHE low-light enhancement, high-contrast borders, and a `WEBCAM` indicator tag so Gemini instantly understands screen context vs user camera context.
  - Single composite image upload (<100KB JPEG) cuts API roundtrip latency from ~15s to ~1.5s and consumes only 1 image token allocation (~258 tokens).
- **Subconscious Screen Frame Syncing**: `AnimeVision.py` automatically keeps `PerceptionEngine` updated with live desktop frames via `update_screen_frame()`, populating `snap.frame_blob` on significant state changes so spontaneous remarks can comment directly on active screen content.
- **Extended Vision Intent Gating**: User questions mentioning `screen`, `desktop`, `monitor`, `display`, `problem`, `equation`, `diagram`, or `pdf` automatically trigger fresh high-resolution composite vision analysis.

### Tests
- Expanded test suite from 78 to **81 tests passing** (`Ran 81 tests in ~10.3s — OK`), adding coverage for composite layout dimensions, degradation fallbacks, screen frame cache, and expanded keyword triggers.

---

## [v1.6.0] — 2026-10-29

### Fixed
- **Direct chat replies** now correctly clear the `...` loading bubble in MateEngine's dialogue UI — `sendChatMessage()` now passes `showBubble=true`, `spontaneous=false`, and `force=true` explicitly so the reply is never de-duplicated or silently dropped.
- **Spontaneous thought queue** is no longer destructively emptied during a chat session — removed the erroneous `while not spontaneous_thought_queue.empty(): popleft()` drain loop from `universal_chat_bridge()` in `AnimeVision.py`. Queued pokes now survive until properly flushed.

### Added
- **SillyTavern settings toggle UI** for spontaneous pokes — a new checkbox in the Extension-MateEngine settings drawer lets users enable or disable ambient desktop pokes without restarting. Config is synced to AlphaVision's `/api/config` endpoint on `:5001`.
- **`inChatPokeEnabled` and `desktopBubblesEnabled`** now default to `true` in the extension's `defaultSettings`.

### Tests
- Test suite expanded from 76 to **78 tests passing** (`Ran 78 tests in ~10s — OK`).

---

## [v1.5.0] — 2026-10-28

### Fixed
- **Low-light vision degradation** — `perception.py` now applies CLAHE + gamma boost when average frame brightness falls below the threshold (nighttime/dim environment). Handheld objects, book titles, and screen content are now legible instead of a dark blur.
- **Repetitive sleep-deprived nagging** — `brain.py` enforces a 35-minute cooldown after any sleep-related remark is fired, preventing Alpha from looping on "you look like a zombie" for the entire session. Time-of-day weight is reduced so other context signals (active window, posture, held objects) can override.
- **Ambient pokes decoupled from SillyTavern system chat** — spontaneous desktop remarks from `/spontaneous_pop` are no longer injected into the SillyTavern message log as `SillyTavern System` messages. They remain transient in the MateEngine desktop bubble only; `last_ambient_poke` stores the 90-second short-term memory for context injection when the user opens chat.

### Changed
- Webcam capture resolution bumped to 640×480 (JPEG quality 75) for vision-required interactions.

---

## [v1.4.0] — 2026-10-27

### Fixed
- **Assembly-CSharp bridge crash (`TypeLoadException`)** — the MateEngine HTTP bridge no longer crashes when `ChatBot` is not yet active. All `Object.FindFirstObjectByType` and UI calls are now queued to the Unity main thread's `Update()` loop instead of being called directly from background HTTP handler threads. The HTTP listener continues accepting connections on `:8787` regardless of `ChatBot` state.
- Added `patch_mateengine_dll.ps1` to `unity_bridge_mod/` for reproducible patching of `Assembly-CSharp.dll`.

---

## [v1.3.0] — 2026-10-26

### Fixed
- **Spontaneous pokes now appear inside MateEngine's internal ChatBox / dialogue UI** — `dispatcher.py` now POSTs directly to MateEngine's chat endpoint (`:8787/api/chat`) with `"add_to_chat": true` and `"is_bubble": true` so remarks show in both the floating speech bubble and the character's dialogue history simultaneously.

---

## [v1.2.0] — 2026-10-24

### Fixed
- **Extreme generation latency** — vision capture is now gated; frames are only sent to the Gemini multimodal model when the user is actively holding an object or when a low-confidence context tag requires visual disambiguation. Idle frames no longer trigger full model inference.
- **MateEngine speech bubbles auto-dismiss** — bubbles now carry a TTL and are dismissed automatically after the configured display duration without requiring user interaction.
- **Username collision in SillyTavern** — the extension now reads the active character's display name at runtime instead of using a hardcoded fallback, preventing message attribution errors in multi-character setups.
- **Mouse cursor flickering** — `mss.windows.CAPTUREBLT` set to `0`; screen capture rate throttled to once per 500ms minimum; all `GetDC`/`ReleaseDC`/`DeleteDC`/`DeleteObject` calls wrapped in `finally` blocks to prevent GDI resource leaks.

---

## [v1.1.0] — 2026-10-22

### Added
- **Spontaneous thoughts piped into SillyTavern active chat** — when Alpha generates a desktop poke while SillyTavern is open, the remark is also delivered into the active chat log so the user can reply naturally.

### Fixed
- **MediaPipe `solutions` import deprecation** — updated all imports from the deprecated `mediapipe.solutions.*` namespace to the current stable API, resolving startup `ImportError` on MediaPipe ≥ 0.10.

---

## [v1.0.0] — 2026-10-20

### Added
- **Initial multimodal awareness engine** — `engine/perception.py` with asynchronous webcam face/presence tracking (OpenCV + MediaPipe) and Windows OS telemetry (`win32gui`, `win32api`) for active window titles and user idle duration. Graceful fallback when camera is offline or busy.
- **Cognitive state machine** (`engine/brain.py`) — hysteresis-debounced chat box detection, context tagging (physics homework, coding, browsing, gaming), rate-limited attention-seeking / nagging triggers, and 90-second short-term ambient memory buffer (`last_ambient_poke`).
- **Dual-channel dispatcher** (`engine/dispatcher.py` + `AnimeVision.py`):
  - *Channel A (Ambient Pokes)*: Transient popups delivered exclusively to MateEngine desktop speech bubbles via `GET /spontaneous_pop` on `:5001` → `:8787`. Never pollutes SillyTavern chat history.
  - *Channel B (Direct Conversation)*: Standard dialogue relay to SillyTavern via the Extension-MateEngine bridge on `:8000`.
- **SillyTavern extension** (`extensions/Extension-MateEngine/`) — full settings drawer, spontaneous thought ingestion, and MateEngine chat reply callback.
- **Auto-restart watchdog** (`supervisor.ps1`) — monitors the AlphaVision process and restarts it automatically on crash.
- **Full test suite** — `test_awareness.py` with initial coverage of perception, brain state machine, dispatcher routing, and integration flows.

---

[v1.6.0]: https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/compare/v1.5.0...v1.6.0
[v1.5.0]: https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/compare/v1.4.0...v1.5.0
[v1.4.0]: https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/compare/v1.3.0...v1.4.0
[v1.3.0]: https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/compare/v1.2.0...v1.3.0
[v1.2.0]: https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/compare/v1.1.0...v1.2.0
[v1.1.0]: https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/compare/v1.0.0...v1.1.0
[v1.0.0]: https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/releases/tag/v1.0.0
