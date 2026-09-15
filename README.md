
```markdown
# 🌌 AlphaVision Bridge

An ultra-low latency, multimodal dual-vision neural bridge connecting **SillyTavern** and **MateEngine** using Gemini Vision (`gemini-3.5-flash-lite`).

AlphaVision gives your desktop anime companion real-time awareness of both your physical space (via webcam) and your active computer workspace (via screen capture), complete with autonomous spontaneous observations and sub-0.6s latency.

```

---

## 📦 Required Software & Prerequisites

This bridge **requires** the modded MateEngine client and the matching SillyTavern extensions to enable the desktop avatar, speech bubbles, and viseme lip-syncing.

### 1. SillyTavern Setup

Acts as the conversational frontend and character interface.

* **Installer:** [SillyTavern-Launcher](https://github.com/SillyTavern/SillyTavern-Launcher)
* **Required Extension:** [Extension-MateEngine](https://github.com/LazyCodingKing/Extension-MateEngine)
*Install via SillyTavern extensions or place inside `/SillyTavern/public/scripts/extensions/third-party/Extension-MateEngine/*`
* **Required Plugin:** [mateengine-chat-plugin](https://github.com/LazyCodingKing/mateengine-chat-plugin)
*Place inside `/SillyTavern/plugins/*`

### 2. Modded MateEngine (Desktop Companion)

Renders the 3D desktop anime avatar with real-time physics, speech bubbles, and visemes.

* **Modded Release:** [LazyCodingKing/MateEngine-Sillytavern](https://github.com/LazyCodingKing/MateEngine-Sillytavern)
* **Port Configuration:** Listens on port `8787` (`/api/chat`) for speech bubbles and motion commands.

---

## ⚡ Key Features

* 👁️ **Dual Visual Streams:** Synchronous webcam capture (via OpenCV DirectShow) and high-speed desktop screen capture (via `mss`).
* 🏎️ **Micro-JPEG Buffer Compression:** Resizes and compresses image payloads down to ~85 KB, cutting multimodal round-trip latency to under 0.6 seconds.
* 🔄 **Hybrid SSE & JSON Bridge:** Full compatibility with SillyTavern's Server-Sent Events (SSE) streaming as well as non-streaming JSON plugin calls.
* 💬 **Autonomous Spontaneous Thoughts:** A background observer thread that periodically checks your screen and webcam to trigger unprompted comments in MateEngine.

---

## 🚀 Installation & Setup

### 1. Clone the Repository

```cmd
git clone [https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git](https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git)
cd AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-

```

### 2. Create Virtual Environment & Install Dependencies

```cmd
python -m venv alpha_env
call alpha_env\Scripts\activate
pip install -r requirements.txt

```

### 3. Set Your Gemini API Key

Set the environment variable in your terminal:

```cmd
set GEMINI_API_KEY=your_gemini_api_key_here

```

*(Alternatively, edit the `GEMINI_API_KEY` placeholder directly in `AnimeVision.py`).*

### 4. Launch the Bridge

```cmd
python AnimeVision.py

```

---

## 🔧 SillyTavern Connection Settings

Configure your SillyTavern API settings as follows:

| Setting | Value |
| --- | --- |
| **API** | `Chat Completion` |
| **Chat Completion Source** | `Custom (OpenAI-compatible)` |
| **Custom Endpoint (Base URL)** | `http://127.0.0.1:5001/v1` |
| **Model ID** | `gpt-3.5-turbo` |
| **Streaming** | `Enabled` |

---

## 📜 License

Distributed under the **MIT License**. See `LICENSE` for more information.

