"""Who is on the other end of this turn — Post-MVP Faz 2.75, Paket C.

The transport string was answering two different questions at once:

  1. Where did this message come from?      (audit provenance)
  2. Can the user approve something now?    (policy)

Those are not the same question, and conflating them cost a real unattended
write. `_gate_inputs` derived "a human is present" as
``not transport.startswith("monitor-")`` — a denylist — so ``task-async``, the
background TaskExecutor, was treated as attended and a background job's
calendar create took Faz 2's confidence downgrade: risk_level 3,
requires_confirmation False, nobody watching. Found by external review
2026-08-01, verified against the running code, fixed first as an allowlist and
now as this.

An allowlist alone would have closed that one hole. This closes the class: the
answer is computed **once per turn, at the entry point that knows it**, carried
in graph state, and read by name. A node no longer re-derives policy from a
string, and a transport added later is unattended until someone says otherwise.

**On the four booleans.** Today ``human_present`` and ``can_confirm`` always
agree, and so do ``unattended`` and ``background``. They are separate anyway
because they answer separate questions and the codebase has already been bitten
once by a single flag standing in for two ideas — but saying "they are
independent" would be dressing up something unmeasured. The honest statement is
that they *may* diverge (a voice session whose user walked away is present-but-
unconfirmable; a scheduled briefing is unattended but not a background *task*),
and the type is shaped so that divergence is expressible rather than a rewrite.

The load-bearing field today is ``can_confirm``: it is the one policy_guard's
calendar downgrade reads.
"""

from __future__ import annotations

from dataclasses import dataclass

# Transports on which a human is at the surface right now and a confirmation
# prompt can actually be shown and answered within this turn.
#
# An allowlist. Everything else — a transport added next month, an empty
# string, an old checkpoint with no transport at all — is unattended. Being
# wrong here costs a confirmation prompt; being wrong the other way costs an
# unwatched external write.
_ATTENDED_TRANSPORTS = frozenset({
    "cli", "cli-text",                            # the Rich REPL
    "api", "api-stream", "api-upload",            # a client is holding the request
    "voice-cli", "voice-local", "voice-remote",   # the gate speaks the prompt
})

# Prefix → origin. Origin is provenance ("what kind of thing started this"),
# deliberately NOT policy: monitor-email and monitor-calendar are one origin
# with one set of permissions.
_MONITOR_PREFIX = "monitor-"


@dataclass(frozen=True)
class ExecutionContext:
    """One turn's answer to "may this proceed without asking anyone?"."""

    transport: str = ""
    origin: str = "unknown"       # "user" | "monitor" | "task" | "unknown"
    human_present: bool = False
    can_confirm: bool = False
    unattended: bool = True
    background: bool = True

    # ── construction ────────────────────────────────────────────────────────

    @classmethod
    def for_transport(cls, transport: str | None) -> "ExecutionContext":
        """Derive a context from a transport tag, failing safe on anything
        unrecognized."""
        tag = (transport or "").strip()

        if tag in _ATTENDED_TRANSPORTS:
            return cls(
                transport=tag, origin="user",
                human_present=True, can_confirm=True,
                unattended=False, background=False,
            )

        if tag.startswith(_MONITOR_PREFIX):
            # proactive_turn() discards an L3 interrupt into a notification --
            # there is no channel for a background thread to answer one.
            return cls(
                transport=tag, origin="monitor",
                human_present=False, can_confirm=False,
                unattended=True, background=True,
            )

        if tag == "task-async":
            # TaskExecutor catches ConfirmationRequired and fails the job with a
            # message, for the same reason: nothing to ask.
            return cls(
                transport=tag, origin="task",
                human_present=False, can_confirm=False,
                unattended=True, background=True,
            )

        # Unrecognized, empty, or absent. The safe corner of the lattice.
        return cls(
            transport=tag, origin="unknown",
            human_present=False, can_confirm=False,
            unattended=True, background=True,
        )

    # ── graph-state serialization ───────────────────────────────────────────
    #
    # Plain dict in state, same discipline as ToolRoute: checkpoints are
    # msgpack-serialized and a dataclass would not survive a resume.

    def to_dict(self) -> dict:
        return {
            "transport": self.transport,
            "origin": self.origin,
            "human_present": self.human_present,
            "can_confirm": self.can_confirm,
            "unattended": self.unattended,
            "background": self.background,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "ExecutionContext | None":
        """None for a state that has no context at all.

        Callers must treat None as "derive it from whatever else you have",
        not as "assume the default" -- the default here is the *safe* corner,
        and silently applying it to an old checkpoint mid-resume would turn a
        user's approved foreground turn into an unattended one.
        """
        if not data or not isinstance(data, dict):
            return None
        return cls(
            transport=str(data.get("transport") or ""),
            origin=str(data.get("origin") or "unknown"),
            human_present=bool(data.get("human_present")),
            can_confirm=bool(data.get("can_confirm")),
            unattended=bool(data.get("unattended", True)),
            background=bool(data.get("background", True)),
        )

    # ── the question policy actually asks ───────────────────────────────────

    @property
    def may_act_without_asking(self) -> bool:
        """Whether a confidence-based confirmation downgrade is permissible.

        All three conditions, not just one: a human is there, they can answer,
        and this is not a turn running behind their back. Today the three move
        together; the point is that a future transport can satisfy one without
        satisfying the others and this will still be right.
        """
        return self.human_present and self.can_confirm and not self.unattended
