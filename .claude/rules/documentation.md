---
paths:
  - "*.md"
  - "docs/**"
---

# Writing documentation in this repo

## The repository is the source of truth

When a document and the code disagree, the code wins and the document is the
bug. Verify every concrete claim — a path, a SHA, a metric, a file name — before
repeating it. A task prompt is written by someone without the repo open and can
name things that moved or never existed; **report the mismatch rather than
silently substituting what you found.**

## Numbers that go stale must be derived, not quoted

Never write a fixed value for anything that changes on the next commit:

- how far `main` is behind — derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`
- test counts — bind them to the command and date of the run that produced them
- CI status — read it live (`gh run list`), never from a file

If a number must appear, say what produced it and when.

## HANDOFF.md

Single-state, not a history. Sections: current verified state; last completed
work; operational modes and rollout decisions; tests and CI; known open issues;
next engineering priority; human-required actions; session recovery notes.

**Self-reference rule (owner-set 2026-07-23, after the 5th recurrence):** HANDOFF
must never state its own closing commit's SHA, and never its own push or CI
outcome — both are unknowable at the moment it is written. Count the closing
commit relationally ("N work commits and this closing HANDOFF commit").

## Honest reporting

- Never present an unrun check as passed, or a skipped step as done.
- Distinguish deterministic evidence from live evidence; do not let a test-suite
  result stand in for a live run, or vice versa.
- Keep a corrected mistake visible rather than quietly rewriting it — several
  rules in this repo exist because a wrong claim was caught and recorded.
- Do not change a pre-registered threshold, corpus or metric after seeing a
  result. Note the problem for the next revision instead.

## Language

Reply to the owner in **Turkish**. Code, comments, commit messages and the
documents themselves stay **English**.
