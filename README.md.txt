(
echo # AlphaVision Bridge
echo Multimodal real-time vision and autonomous thought bridge for desktop avatars.
echo.
echo ## Features
echo - **Dual Vision Stream**: Synchronous webcam ^(DirectShow^) and active screen ^(mss^) ingestion.
echo - **Hybrid Bridge**: Supports both OpenAI SSE streaming and non-streaming JSON.
echo - **Autonomous Proactive Engine**: Spontaneously observes desktop activity and sends remarks to MateEngine.
echo.
echo ## Setup
echo 1. Clone this repository.
echo 2. Create a virtual environment:
echo    ```cmd
echo    python -m venv alpha_env
echo    call alpha_env\Scripts\activate
echo    pip install -r requirements.txt
echo    ```
echo 3. Set your Gemini API key:
echo    ```cmd
echo    set GEMINI_API_KEY=your_key_here
echo    ```
echo 4. Start the bridge:
echo    ```cmd
echo    python AnimeVision.py
echo    ```
echo 5. Connect SillyTavern Custom API to `http://127.0.0.1:5001/v1`.
) > "%USERPROFILE%\Desktop\AlphaVision-Bridge\README.md"