# Asset Setup

## Fonts (required before building)

Place the following font files in `assets/fonts/`:

| File | Source |
|---|---|
| `ShareTechMono-Regular.ttf` | https://fonts.google.com/specimen/Share+Tech+Mono |
| `Orbitron-Regular.ttf` | https://fonts.google.com/specimen/Orbitron |
| `Orbitron-Black.ttf` | https://fonts.google.com/specimen/Orbitron |

### Quick download (PowerShell):
```powershell
$fontsDir = "mobile/assets/fonts"
# Share Tech Mono
Invoke-WebRequest -Uri "https://github.com/google/fonts/raw/main/apache/sharetechmono/ShareTechMono-Regular.ttf" -OutFile "$fontsDir/ShareTechMono-Regular.ttf"
# Orbitron (download zip and extract)
Invoke-WebRequest -Uri "https://github.com/google/fonts/raw/main/ofl/orbitron/Orbitron%5Bwght%5D.ttf" -OutFile "$fontsDir/Orbitron-Regular.ttf"
Copy-Item "$fontsDir/Orbitron-Regular.ttf" "$fontsDir/Orbitron-Black.ttf"
```

Note: Orbitron is a variable font — the same file works for both Regular and Black weights.
The pubspec.yaml references weight 400 and weight 800 from the same variable font.

## Wake-word Model (required for Hey JARVIS)

Place the ONNX model in `assets/wake/`:

```
mobile/assets/wake/hey_jarvis_v0.1.onnx
```

Copy from JARVIS desktop installation:
```
Jarvis/data/models/wake/hey_jarvis_v0.1.onnx
```
(or whichever path the openWakeWord model is stored)

## Firebase (required for push notifications)

Place `google-services.json` in `android/app/`:
```
mobile/android/app/google-services.json
```
See `android/app/google-services.json.template` for instructions.
