# Asset Setup

Everything listed here is **gitignored on purpose** (`mobile/.gitignore`), so a
fresh clone does not have it. That is not free:

- `flutter analyze` and `flutter pub get` pass without any of it.
- Anything that builds the asset bundle — `flutter test`, `flutter build` — does
  **not**, until the fonts below exist:

  ```
  Error: unable to locate asset entry in pubspec.yaml: "assets/fonts/ShareTechMono-Regular.ttf".
  Error: Failed to build asset bundle
  ```

## Fonts (required before building or testing)

Place these in `mobile/assets/fonts/`. `pubspec.yaml` declares them under
`fonts:`, and `lib/theme/typography.dart` + several widgets reference the
families by name.

| File | Family recorded in the file | Weight in pubspec | Source |
|---|---|---|---|
| `ShareTechMono-Regular.ttf` | `Share Tech Mono` | — | https://fonts.google.com/specimen/Share+Tech+Mono |
| `Orbitron-Regular.ttf` | `Orbitron` | 400 | https://fonts.google.com/specimen/Orbitron |
| `Orbitron-Black.ttf` | `Orbitron ExtraBold` | 800 | https://fonts.google.com/specimen/Orbitron |

Both families are under the **SIL Open Font License**
(`licenseURL: http://scripts.sil.org/OFL`, read from the files' own name tables):
Share Tech Mono 1.003 © 2012 Carrois Type Design; Orbitron 2.001 © 2018 The
Orbitron Project Authors.

**Do not copy one Orbitron file over the other.** An earlier version of this
document said Orbitron is a variable font and that "the same file works for both
weights", with a `Copy-Item Regular -> Black` step. That does not match the files
actually in use: they are two distinct static instances (`Orbitron` vs
`Orbitron ExtraBold`, 17752 vs 17768 bytes), and the pubspec's weight-800 entry
maps to the ExtraBold one. Copying would silently collapse both weights to
Regular.

## Wake-word model (required for "Hey JARVIS")

The model is loaded by the **Android** side, not by Flutter —
`WakeWordService.kt` calls `assets.open("wake/hey_jarvis_v0.1.onnx")`, which
resolves against the Android AssetManager root. So it goes here:

```
mobile/android/app/src/main/assets/wake/hey_jarvis_v0.1.onnx
```

**not** `mobile/assets/wake/`. A `pubspec.yaml` `assets:` declaration cannot
satisfy that path — Flutter bundles its own assets under
`assets/flutter_assets/…` in the APK, so `assets.open("wake/…")` would never see
them. `pubspec.yaml` used to declare `assets/wake/` for this and it never worked;
the declaration was removed on 2026-08-06 (it was also one of the two
`asset_directory_does_not_exist` findings in `CI-MOBILE-01`).

Copy from the JARVIS desktop installation:

```
Jarvis/data/models/wake/hey_jarvis_v0.1.onnx
```

(or wherever the openWakeWord model is stored). `mobile/.gitignore` already
ignores `assets/wake/*.onnx`; add the new location if the model is placed under
`android/app/src/main/assets/`.

## Firebase (required for push notifications)

Place `google-services.json` in `android/app/`:

```
mobile/android/app/google-services.json
```

See `android/app/google-services.json.template` for instructions.
