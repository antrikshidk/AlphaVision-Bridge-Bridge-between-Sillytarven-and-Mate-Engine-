# Contributing to AlphaVision Bridge

Thank you for your interest in contributing! AlphaVision Bridge is a living project — if you're fixing bugs, adding features, or improving documentation, your help is genuinely appreciated.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Development Setup](#development-setup)
- [Running the Test Suite](#running-the-test-suite)
- [Project Structure](#project-structure)
- [Commit Style](#commit-style)
- [Pull Request Process](#pull-request-process)
- [Code Style Notes](#code-style-notes)
- [Important Constraints](#important-constraints)

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| **Windows** | 10 or 11 (Win32 APIs required) |
| **Python** | 3.10 or 3.11 (3.12 not tested) |
| **MateEngine** | Modded build with port 8787 bridge active |
| **SillyTavern** | Any recent version, with `Extension-MateEngine` installed |
| **Gemini API Key** | From [Google AI Studio](https://aistudio.google.com/) |

---

## Development Setup

```bash
# 1. Clone the repo
git clone https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-
cd AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-

# 2. Create and activate a virtual environment (Windows)
python -m venv alpha_env
alpha_env\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
copy .env.example .env
# Then edit .env and fill in your GEMINI_API_KEY and port values
```

Your `.env` should look like:

```env
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
MATE_ENGINE_PORT=8787
SILLY_TAVERN_PORT=8000
ALPHA_VISION_PORT=5001
```

---

## Running the Test Suite

Always run the full test suite **before** submitting a PR. Use the **venv Python**, not system Python (MediaPipe and other packages are only installed in the venv):

```bash
# From the repo root, with venv activated:
alpha_env\Scripts\python.exe test_awareness.py
```

Expected output:

```
Ran 78 tests in ~10s

OK
```

All 78 tests must pass. If you add new functionality, add corresponding tests to `test_awareness.py`. Do not submit a PR that reduces the test count or leaves any test failing.

---

## Project Structure

```
AlphaVision-Bridge/
├── AnimeVision.py              # Main entry point — dual-channel orchestrator
├── test_awareness.py           # Full test suite (78 tests)
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── launch_alphavision.bat      # Quick-launch script
├── supervisor.ps1              # Watchdog / auto-restart process monitor
│
├── engine/                     # Core awareness engine package
│   ├── __init__.py
│   ├── perception.py           # Webcam + Win32 OS telemetry (read-only sensors)
│   ├── brain.py                # Cognitive state machine, context tagging, hysteresis
│   └── dispatcher.py          # Dual-channel delivery (MateEngine :8787 + SillyTavern :8000)
│
├── extensions/
│   └── Extension-MateEngine/   # SillyTavern extension (copy to ST's third-party folder)
│       ├── index.js
│       ├── manifest.json
│       └── style.css
│
├── unity_bridge_mod/           # C# DLL patch for MateEngine port 8787 bridge
│   ├── README.md
│   └── patch_mateengine_dll.ps1
│
└── .github/
    ├── PULL_REQUEST_TEMPLATE.md
    └── ISSUE_TEMPLATE/
        ├── bug_report.md
        └── feature_request.md
```

### Key file ownership rules

| File | Rule |
|---|---|
| `AnimeVision.py` | Authoritative entry point. All changes go here. |
| `engine/perception.py` | Sensor layer only. No business logic. |
| `engine/brain.py` | State machine only. No HTTP calls. |
| `engine/dispatcher.py` | Delivery only. No state decisions. |
| `AnimeVision - Copy.py` | **DO NOT EDIT** — legacy backup, ignored by git tracking |
| `AnimeVision_2.py` | **DO NOT EDIT** — legacy backup, ignored by git tracking |
| `Assembly-CSharp.dll` | **DO NOT EDIT** directly — use `patch_mateengine_dll.ps1` |

---

## Commit Style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>
```

| Type | When to use |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation changes only |
| `test` | Adding or updating tests |
| `chore` | Maintenance, dependency bumps, refactoring |
| `perf` | Performance improvements |

**Examples:**

```
feat(brain): add physics-context tag for PDF viewer detection
fix(dispatcher): ensure spontaneous thoughts are queued before MateEngine POST
docs(readme): update endpoint reference table with /api/config
test(awareness): add 3 tests for ambient poke memory window
```

---

## Pull Request Process

1. **Fork** the repository and create a branch from `main`:
   ```bash
   git checkout -b fix/your-descriptive-branch-name
   ```

2. **Make your changes.** Never write placeholders or truncate code — every function must be complete.

3. **Run all 78 tests** and confirm they pass.

4. **If you modified `extensions/Extension-MateEngine/index.js`**, ensure the copy in SillyTavern's third-party folder is also updated and identical:
   ```
   SillyTavern\public\scripts\extensions\third-party\Extension-MateEngine\index.js
   ```

5. **Commit** using Conventional Commits format.

6. **Open a Pull Request** against `main`. Fill in the PR template completely, including pasting your test results.

7. A maintainer will review and merge. Expect feedback within a few days.

---

## Code Style Notes

- **No placeholders.** Every function, endpoint handler, and callback must be fully implemented — no `# TODO: implement this` stubs in submitted code.
- **No truncation.** Never submit partial implementations.
- **Logging:** Use the `logging` module with the `[alphavision.<module>]` prefix pattern already established.
- **Thread safety:** All Unity/MateEngine API calls must be dispatched to the main thread via the queue pattern. Never call Unity engine APIs from background HTTP handler threads.
- **Error handling:** Wrap all external HTTP calls (to MateEngine `:8787` and SillyTavern `:8000`) in `try/except requests.RequestException` with graceful degradation — a failed delivery must never crash the awareness loop.
- **Environment variables:** Load all secrets from `.env` via `python-dotenv`. Never hardcode API keys.

---

## Important Constraints

- ❌ **Never commit `.env`** or any file containing real API keys.
- ❌ **Never edit** `AnimeVision - Copy.py` or `AnimeVision_2.py`.
- ❌ **Never directly patch** `Assembly-CSharp.dll` by hand — always use `patch_mateengine_dll.ps1`.
- ✅ **Always run the full test suite** before committing.
- ✅ **Keep both `index.js` copies in sync** if the SillyTavern extension is modified.

---

*If you have questions, open a [GitHub Discussion](https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-/discussions) or file an issue using the bug report template.*
