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

**`MOBILE-TEST-01` is closed (2026-08-07)** — fixed in the product code, not
worked around in the test. `test/widget_test.dart` used to fail with:

```
A Timer is still pending even after the widget tree was disposed.
'package:flutter_test/src/binding.dart': Failed assertion: line 2542 pos 12: '!timersPending'
```

`_SplashRouterState` (`lib/app.dart`) armed an uncancellable
`Future.delayed(Duration(seconds: 2))`; it now holds a cancellable `Timer` and
cancels it in `dispose()`. **The splash behaviour is unchanged** — same 2s
delay, same `pushReplacementNamed('/home')`. The other two timers in `lib/`
(`lock_screen.dart`, `home_screen.dart`'s `_TopBarState`) already cancelled
theirs; this was the only leak.

Two properties of the regression guard, so a later edit does not silently
neuter it:

- The guard disposes the tree **inside** the 2s window and then ends the test.
  It must NOT elapse fake time past the deadline afterwards — elapsing lets a
  leaked timer fire and retire itself, so `_verifyInvariants` would find nothing
  pending and the check would pass **vacuously**. Confirmed by reverting the fix
  and re-running: the smoke test and the guard both fail, the navigation test
  still passes.
- `pumpAndSettle()` can never be used in this tree — `JarvisOrb`'s
  `AnimationController` calls `repeat()`, so it never settles. Pump explicit
  durations instead.

Measured 2026-08-07 on `mobile/` with the font assets present locally:
`flutter test` → **17 passed** (`widget_test.dart`, `chat_sse_test.dart`,
`transcript_provider_test.dart`); `flutter analyze` → `No issues found`.

CI still runs `analyze` only. Adding `flutter test` to CI is now blocked by
`MOBILE-ASSETS-01` (the gitignored fonts, above) rather than by this bug — and
that is an owner decision, not a leftover.

## Functional gap to keep in mind

The mobile app renders **nothing** for a confirmation prompt. The safety gate
still holds server-side (the action does not execute), but a mobile user gets no
approve/deny UI, so any flow that can reach an L3 action is effectively
unusable from the phone. Do not describe mobile confirmation support as
existing.
