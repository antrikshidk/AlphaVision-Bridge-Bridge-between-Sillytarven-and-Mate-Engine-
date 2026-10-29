WORLK in Progress, there is some problem with expression control as i didnt add character expression animation, si ut will not work

MADE USING GOOGLE ANTIGRAVITY FOR PERSONAL USE
# MateEngine Bridge Extension for SillyTavern

Connects SillyTavern to MateEngine desktop app for real-time avatar control including expressions, animations, and lip sync.

## Features

- **Expression Control**: Set avatar emotions (Joy, Angry, Sorrow, Fun, Neutral)
- **Lip Sync**: Automatic text-to-viseme lip sync on AI messages
- **Animation Control**: Trigger animator parameters
- **Auto Mode**: Automatically send expressions on new messages
- **Slash Commands**: Full control via `/mex` commands

## Installation

### SillyTavern Extension

1. Copy this folder to `SillyTavern/public/scripts/extensions/third-party/Extension-MateEngine`
2. Restart SillyTavern
3. Enable in Extensions panel

### MateEngine Setup

1. Open MateEngine Unity project
2. Add `SillyTavernBridge` component to your avatar GameObject
3. Assign references to `UniversalBlendshapes` and `Animator`
4. Enable "Auto Start" or toggle manually in MateEngine settings
5. Build and run

## Slash Commands

| Command | Description |
|---------|-------------|
| `/mex status` | Check bridge connection |
| `/mex joy` | Set Joy expression |
| `/mex angry` | Set Angry expression |
| `/mex sad` | Set Sorrow expression |
| `/mex neutral` | Set Neutral expression |
| `/mex speak Hello world` | Speak text with lip sync |
| `/mex stop` | Stop lip sync |
| `/mex expression happy 0.8` | Set expression with intensity |
| `/mex animation isDancing true` | Set animator bool |

## Settings

- **Enable Bridge**: Turn connection on/off
- **Bridge URL**: Default `http://localhost:8787`
- **Auto-send expressions**: Automatically sync expressions on messages
- **Auto lip-sync**: Animate mouth on AI messages

## API Endpoints

MateEngine exposes these HTTP endpoints:

| Endpoint | Method | Body |
|----------|--------|------|
| `/api/status` | GET | - |
| `/api/expression` | POST | `{"emotion":"joy","value":1.0}` |
| `/api/viseme` | POST | `{"viseme":"A","value":0.8}` |
| `/api/animation` | POST | `{"param":"isDancing","value":true}` |
| `/api/speak` | POST | `{"text":"Hello","emotion":"joy"}` |
| `/api/lipsync` | POST | `{"stop":true}` |

## Emotion Mapping

| SillyTavern | MateEngine |
|-------------|------------|
| joy, amusement, excitement | Joy |
| anger, annoyance | Angry |
| sadness, grief | Sorrow |
| love, desire, caring | Fun |
| neutral, surprise | Neutral |

## Troubleshooting

- **Not connected**: Ensure MateEngine is running and bridge is started
- **Port conflict**: Change port in both MateEngine settings and extension
- **No expressions**: Check that `UniversalBlendshapes` reference is assigned
