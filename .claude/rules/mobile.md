---
paths:
  - "mobile/**"
---

# Flutter mobile app

The Android client talks to the FastAPI backend over the home network /
Tailscale. It is the least-covered surface in the project.

## CI expectation — do not misread it

The `mobile` job runs `flutter analyze` and is configured
`continue-on-error: true` (owner decision). That means:

- **A green overall workflow does NOT mean `mobile` passed.** Always check the
  job itself: `gh run view <id> --json jobs`.
- `mobile` has been failing since well before any current work. The findings are
  `info`/`warning` class (`deprecated_member_use` for `withOpacity`/`scale`,
  plus missing asset directories declared in `pubspec.yaml`), not errors.
- **Report a `mobile` failure separately from the overall result** rather than
  folding it into "CI green". Do not silently inherit it as acceptable either —
  clearing it is a pending owner decision.

## Functional gap to keep in mind

The mobile app renders **nothing** for a confirmation prompt. The safety gate
still holds server-side (the action does not execute), but a mobile user gets no
approve/deny UI, so any flow that can reach an L3 action is effectively
unusable from the phone. Do not describe mobile confirmation support as
existing.
