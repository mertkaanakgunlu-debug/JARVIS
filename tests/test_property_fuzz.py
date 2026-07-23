"""Agent Runtime rev.2, Faz 8 -- property-based fuzzing (hypothesis).

The plan's row: "Property-based fuzzing (`hypothesis` -- bugun hic property/
schema testi yok)". Two deterministic, pure layers get fuzzed -- the ones
whose whole value is "never trust, never crash":

  * jarvis.execution.args_schemas.validate_args -- the reject-only gate.
    Whatever junk a model emits, it must return (bool, trimmed-errors),
    never raise, never echo the raw input, and never accept an unknown
    field or an out-of-Literal action.
  * jarvis.execution.redaction -- whatever shape a secret arrives in, it
    must not survive to the persisted preview.

Determinism note (this matters after dd339b9's CI-flake fix): hypothesis is
randomized per run BY DEFAULT, which would reintroduce exactly the class of
nondeterministic CI red that fix just eliminated. The profile below sets
derandomize=True (examples derive from the test's own source, stable across
runs) and deadline=None (a GC pause or cold import on the CI runner must
not fail a property that is about VALUES, not speed).
"""
from __future__ import annotations

import string
from typing import get_args

from hypothesis import given, settings as hyp_settings, strategies as st
from hypothesis import HealthCheck

from jarvis.execution.args_schemas import validate_args
from jarvis.execution.redaction import digest_args, redact_preview, redact_value
from jarvis.tool_registry import TOOL_SPECS

hyp_settings.register_profile(
    "jarvis_deterministic",
    derandomize=True,
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
hyp_settings.load_profile("jarvis_deterministic")

# Every schema actually wired onto a ToolSpec -- derived, not hand-listed, so
# a 13th schema is fuzzed automatically the day it's registered.
SCHEMAS = sorted(
    {s.args_schema for s in TOOL_SPECS.values() if s.args_schema is not None},
    key=lambda c: c.__name__,
)
assert SCHEMAS, "no registered schemas -- the fuzz target vanished?"

_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10**9, max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=40),
)
_junk_values = st.one_of(_primitives, st.lists(_primitives, max_size=3))
_junk_dicts = st.dictionaries(st.text(min_size=1, max_size=25), _junk_values, max_size=6)


@given(args=_junk_dicts)
def test_validate_args_never_raises_and_never_echoes_input(args):
    """The gate's absolute contract, for every schema at once: any dict in ->
    (ok, errors) out. On rejection every error is exactly {loc, type, msg} --
    pydantic's `input` (which echoes the raw, unredacted argument value) and
    `url`/`ctx` must never leak through (redaction discipline, see
    validate_args' own docstring)."""
    for schema in SCHEMAS:
        ok, errors = validate_args(schema, args)
        assert isinstance(ok, bool)
        assert isinstance(errors, list)
        if ok:
            assert errors == []
        for e in errors:
            assert set(e) == {"loc", "type", "msg"}, e


@given(args=_junk_dicts, unknown_suffix=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10))
def test_an_unknown_field_is_always_rejected(args, unknown_suffix):
    """extra='forbid', as a universal property: no matter what else the dict
    contains, one guaranteed-unknown key must sink the call for every
    schema. (The 'zz_' prefix guarantees unknown-ness -- asserted, not
    assumed.)"""
    key = f"zz_{unknown_suffix}"
    for schema in SCHEMAS:
        assert key not in schema.model_fields  # the guarantee behind the property
        ok, errors = validate_args(schema, {**args, key: "x"})
        assert ok is False, f"{schema.__name__} accepted unknown field {key!r}"
        assert any(e["type"] == "extra_forbidden" for e in errors)


def _allowed_actions(schema) -> frozenset[str]:
    return frozenset(get_args(schema.model_fields["action"].annotation))


_ACTION_SCHEMAS = [s for s in SCHEMAS if "action" in s.model_fields]


@given(raw=st.text(min_size=1, max_size=30))
def test_an_action_outside_the_literal_is_always_rejected(raw):
    """The closed action vocabulary, fuzzed THROUGH the normalizer: any
    string whose strip().lower() form is not in the Literal must be
    rejected -- including case/whitespace variants of junk, which is
    exactly what the mode='before' normalizer exists to make accurate."""
    normalized = raw.strip().lower()
    for schema in _ACTION_SCHEMAS:
        if normalized in _allowed_actions(schema):
            continue  # a legitimate (possibly aliased) action for THIS schema
        ok, errors = validate_args(schema, {"action": raw})
        assert ok is False, (
            f"{schema.__name__} accepted action {raw!r} (normalized {normalized!r})"
        )


def test_every_action_schema_was_actually_fuzzed():
    """Meta-guard: the action property above quietly skips schemas without an
    `action` field -- make sure that set is exactly the one known exception
    (PlotDataArgs, whose closed field is `kind`), so a future action-bearing
    schema can't silently fall out of the fuzz net."""
    without_action = {s.__name__ for s in SCHEMAS} - {s.__name__ for s in _ACTION_SCHEMAS}
    assert without_action == {"PlotDataArgs"}, without_action


# ── redaction properties ────────────────────────────────────────────────────

_secret_body = st.text(alphabet=string.ascii_letters + string.digits, min_size=20, max_size=40)


@given(body=_secret_body, prefix=st.sampled_from(["", "use ", "cfg: "]))
def test_a_recognizable_secret_never_survives_redaction(body, prefix):
    """An sk-style secret embedded in free text -- as a plain string, nested
    in a list, or under an innocent dict key -- must be masked everywhere.
    This is the exact gap the shared layer closed (a secret in a PLAIN
    STRING argument, not behind a suspicious key)."""
    token = f"sk-{body}"
    carriers = [
        f"{prefix}{token} end",
        {"innocent_key": f"{prefix}{token}"},
        ["a", {"deep": [f"x {token}"]}],
    ]
    for carrier in carriers:
        assert token not in str(redact_value(carrier)), carrier


@given(value=_junk_values, key=st.sampled_from(["password", "api_key", "token", "secret", "authorization"]))
def test_a_sensitive_key_masks_any_value_shape(value, key):
    redacted = redact_value({key: value})
    assert redacted == {key: "<redacted>"}


@given(value=_junk_values, max_chars=st.integers(min_value=1, max_value=300))
def test_redact_preview_never_exceeds_its_budget(value, max_chars):
    assert len(redact_preview(value, max_chars=max_chars)) <= max_chars


@given(args=st.dictionaries(st.text(min_size=1, max_size=15), _primitives, min_size=1, max_size=6))
def test_digest_args_is_deterministic_and_key_order_insensitive(args):
    shuffled = dict(reversed(list(args.items())))
    assert digest_args("gmail", args) == digest_args("gmail", shuffled)
    # Domain separation: the same args under a different tool name must not
    # collide -- the digest identifies a CALL, not just an arg bag.
    assert digest_args("gmail", args) != digest_args("itu_mail", args)
