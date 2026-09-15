Markdown
# AlphaVision Bridge

An ultra-low latency, multimodal dual-vision bridge connecting **SillyTavern** and **MateEngine** using Gemini Vision (`gemini-3.5-flash-lite`).
📦 Required Software & Prerequisites
Make sure you have both core tools installed before running the bridge:

1. SillyTavern
Acts as the conversational frontend and character interface.

Installer: SillyTavern-Launcher

Required Plugin: Extension-MateEngine (Install via SillyTavern extensions or place in /SillyTavern/public/scripts/extensions/third-party/Extension-MateEngine/)

MateEngine Chat Plugin: mateengine-chat-plugin (Place into /SillyTavern/plugins/)

2. Modded MateEngine (Desktop Companion)
Renders the 3D desktop anime avatar with real-time physics, speech bubbles, and visemes.

Modded Release: LazyCodingKing/MateEngine-Sillytavern

Port Configuration: Listens on port 8787 (/api/chat) for speech bubbles and motion commands.

🚀 Installation & Setup
Clone the Repository:

DOS
git clone [https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git](https://github.com/antrikshidk/AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-.git)
cd AlphaVision-Bridge-Bridge-between-Sillytarven-and-Mate-Engine-
Create Virtual Environment & Install Dependencies:

DOS
python -m venv alpha_env
call alpha_env\Scripts\activate
pip install -r requirements.txt
Configure API Key:
Set your Gemini API key in your environment:

DOS
set GEMINI_API_KEY=your_gemini_api_key_here
(Or edit AnimeVision.py directly).

Launch the Bridge:

DOS
python AnimeVision.py
Connect in SillyTavern:

API: Chat Completion

Chat Completion Source: Custom (OpenAI-compatible)

Custom Endpoint (Base URL): http://127.0.0.1:5001/v1

Model ID: gpt-3.5-turbo

⚙️ Features
Dual Vision Ingestion: Real-time webcam (DirectShow) + live active screen snapshots (mss).

Fast JPEG Buffer Compression: Sub-0.6s latency via micro-JPEG streams.

Hybrid SSE/JSON Endpoint: Supports active streaming and direct JSON queries.

Autonomous Observer Loop: Proactively observes activity and sends spontaneous speech bubble remarks to MateEngine.