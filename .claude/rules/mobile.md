---
paths:
  - "mobile/**"
---

# Flutter mobile app

The Android client talks to the FastAPI backend over the home network /
Tailscale. It is the least-covered surface in the project.

## CI expectation

The `mobile` job runs `flutter pub get` + `flutter analyze` and is configured
`continue-on-error: true` (owner decision, unchanged). That still means:

- **A green overall workflow does NOT mean `mobile` passed.** Always check the
  job itself: `gh run view <id> --json jobs`.
- **Report the `mobile` job separately** from the overall result rather than
  folding it into "CI green".

What changed (2026-08-06): `flutter analyze` is expected to be **clean** now.
`CI-MOBILE-01` — the 71 permanent findings (69 `deprecated_member_use`, 2
`asset_directory_does_not_exist`) — was cleared at the source, not suppressed.
No `analysis_options.yaml` exists anywhere in the repo, no `ignore_for_file` was
added, no `ignore:` comment was added, and `continue-on-error` was left as it
was. (`mobile/` does contain exactly one pre-existing `// ignore:` comment —
`unawaited_futures` in `lib/core/ws_client.dart:46`, from `acf64d1`, 2026-07-15 —
unrelated to any CI-MOBILE-01 finding.) **A `mobile` failure is therefore a real
regression from now on** — do not inherit it as "the known cosmetic one".

Two caveats that keep that expectation honest:

- `flutter analyze` here runs with the **default** analyzer rule set.
  `flutter_lints` is in `dev_dependencies` but is never included (no
  `analysis_options.yaml` exists), so "0 findings" means 0 against the defaults,
  not 0 against the `flutter_lints` ruleset.
- CI resolves `subosito/flutter-action@v2` with `channel: stable` and **no
  pinned version**, so a new stable release can introduce new deprecations and
  turn the job red with no code change of its own — the same drift that once
  broke the `python` job when ruff was unpinned (see `.github/workflows/ci.yml`).

## Assets — read before adding an `assets:` block back

`pubspec.yaml` deliberately has **no `assets:` block**. Nothing in `lib/` reads
the Flutter asset bundle (no `rootBundle` / `DefaultAssetBundle` /
`AssetManifest` use anywhere), so declared assets were bundled and never loaded.
Specifically:

- The `.ttf` files reach the app through the `fonts:` section, not `assets:`.
- The wake-word ONNX model is read by the **Android** side —
  `WakeWordService.kt` does `assets.open("wake/hey_jarvis_v0.1.onnx")`, which
  resolves against the Android AssetManager root
  (`android/app/src/main/assets/wake/`), *not* against `flutter_assets/`. A
  pubspec declaration can never satisfy that path.

## A clean clone cannot build the app

`assets/fonts/*.ttf` and `assets/wake/*.onnx` are gitignored (`mobile/.gitignore`,
deliberate — see `assets/ASSETS_SETUP.md`). `flutter analyze` does not check the
`fonts:` section, but anything that builds the asset bundle does. On a
tracked-files-only checkout:

```
Error: unable to locate asset entry in pubspec.yaml: "assets/fonts/ShareTechMono-Regular.ttf".
Error: Failed to build asset bundle
```

So `flutter test` / `flutter build` fail on a fresh clone until the fonts are
placed manually. CI only runs `analyze`, so this does not affect the `mobile`
job — but do not report "CI is green" as "the app builds anywhere".

## Tests

`test/widget_test.dart` (the single smoke test) **fails, and did so before the
CI-MOBILE-01 work** — verified by running it against an unmodified `git archive`
of the same HEAD:

```
A Timer is still pending even after the widget tree was disposed.
'package:flutter_test/src/binding.dart': Failed assertion: line 2542 pos 12: '!timersPending'
```

Source: `lib/app.dart` — `_SplashRouterState.initState` starts an uncancelled
`Future.delayed(Duration(seconds: 2))`. CI does not run `flutter test` for
mobile, so this is separate from the analyzer debt and still open.

## Functional gap to keep in mind

The mobile app renders **nothing** for a confirmation prompt. The safety gate
still holds server-side (the action does not execute), but a mobile user gets no
approve/deny UI, so any flow that can reach an L3 action is effectively
unusable from the phone. Do not describe mobile confirmation support as
existing.
