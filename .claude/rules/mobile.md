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

Measured 2026-08-08 on `mobile/` with the font assets present locally:
`flutter test` → **37 passed**; `flutter analyze` → `No issues found`. The
suite is six files — `widget_test.dart`, `chat_sse_test.dart`,
`transcript_provider_test.dart`, plus `pending_confirmation_test.dart`,
`confirmation_provider_test.dart` and `ws_event_test.dart` from the L3
confirmation round-trip (below). Only `widget_test.dart` pumps a widget tree;
every other file is deliberately plain `test()` so it stays clear of
MOBILE-TEST-01's failure mode.

CI still runs `analyze` only. Adding `flutter test` to CI is now blocked by
`MOBILE-ASSETS-01` (the gitignored fonts, above) rather than by this bug — and
that is an owner decision, not a leftover.

## L3 confirmations — what exists, and what is still unverified

The approve/deny round-trip is **implemented** (2026-08-08): the phone shows a
card, `POST /chat/confirm/{id}` resolves the interrupt, and the continuation
streams back into the transcript through the same reader as a new turn
(`_consume()` in `lib/screens/chat_screen.dart`). Three pieces are load-bearing
and easy to undo by accident:

- **`confirmationProvider` is app-scoped on purpose.** The SSE stream that
  carries the prompt ends immediately (the graph is interrupted, TTL-bound), so
  a prompt held in `_ChatScreenState` would not survive a rebuild or a tab
  switch. Two legs feed it — the turn's own SSE frame and the WS
  `confirmation_required` broadcast — and `raise()` is idempotent on id so both
  firing is free. The WS leg is the only one that delivers a confirmation
  raised on **another transport**, or one whose SSE stream died first.
- **`beginSubmit()` is the double-submit gate, not the button's disabled
  state.** Approving twice is not idempotent: the first POST pops the
  confirmation server-side, so a second answers "expired or not found" over the
  real continuation. `resolved()` is id-checked so a finishing POST cannot clear
  a *second* interrupt raised mid-stream; `failed()` keeps the card up, because
  a dropped connection is not a verdict.
- **Never render `args` or the payload.** The card shows
  `policy_guard.describe_call`'s plain-language line; a call with no description
  degrades to its tool NAME. An args fallback would put raw JSON back on screen
  and leak message bodies onto a lock screen.

**Still true, do not oversell past it:** this is verified by `flutter analyze`
and `flutter test` only. **No live E2E against a real server + model has been
run from the phone** — the same limit `docs/SAFETY.md` records for the Electron
HUD's confirmation round-trip.
