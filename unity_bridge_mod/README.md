# Unity Bridge Mod: MateEngine Autonomous Chat Sync

This patch enhances `MateEngineBridge` in `Assembly-CSharp.dll` to support autonomous incoming chat lines from AlphaVision / SillyTavern directly into MateEngine's internal ChatBox / Dialogue Window.

## What It Fixes
1. **Schema Normalization**: Expands `ChatRequest` to accept both legacy (`text`, `characterName`, `showBubble`) and modern (`message`, `sender`, `is_bubble`, `add_to_chat`) schemas.
2. **Autonomous Dialogue Window Delivery**: When a spontaneous poke arrives, finds `ChatBot` even if inactive via `UnityEngine.Object.FindFirstObjectByType<ChatBot>(FindObjectsInactive.Include)`, activates the chat panel if closed (`chatBot.gameObject.SetActive(true)`), and calls `chatBot.DisplayAIResponse(cleanedText)` to push incoming character lines autonomously without requiring user input.
3. **Clean Speech Bubble Dismissal**: Auto-dismiss calls with empty text trigger `ForceStandUp()` to cleanly reset the avatar's floating bubble while preserving the internal dialogue history.

## How to Apply
Run PowerShell from this directory:
```powershell
powershell -ExecutionPolicy Bypass -File patch_mateengine_dll.ps1
```
