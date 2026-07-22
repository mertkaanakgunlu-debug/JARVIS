"""Agent Runtime rev.2, Faz 6 -- jarvis/execution/args_schemas.py.

Pure pydantic-model tests. Part 1 defined these schemas with only an action
Literal + unknown-field rejection; Part 2 adds normalizers (mode="before"
strip/lower, matching what every real dispatch function already does
internally) and action-specific required-field validators, verified against
each tool's actual dispatch body (jarvis/tools/calendar.py, gmail.py,
drive.py, itu_mail.py, finance.py, spotify.py, plus the inline dispatches in
jarvis/graph/tools.py for schedule/todo/gcp_quota/geo_math/hud_panels), not
inferred from docstrings.

Still not wired into anything live -- see args_schemas.py's own docstring.
tests/test_tool_registry_schemas.py covers the wiring onto TOOL_SPECS;
tests/test_prepare_execution_validation.py covers validate_args() actually
gating prepare_execution_node; this file is only the schemas' own behavior.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.execution.args_schemas import (
    FinanceArgs,
    GcpQuotaArgs,
    GeoMathArgs,
    GmailArgs,
    GoogleCalendarArgs,
    GoogleDriveArgs,
    HudPanelsArgs,
    ItuMailArgs,
    PlotDataArgs,
    ScheduleArgs,
    SpotifyArgs,
    TodoArgs,
    validate_args,
)


# ── PlotDataArgs: the discriminated-union priority example ─────────────────

def test_plot_data_accepts_path_only():
    args = PlotDataArgs(path="data.csv", x="a", y="b")
    assert args.path == "data.csv" and args.data_json == ""


def test_plot_data_accepts_data_json_only():
    args = PlotDataArgs(data_json="[1,2,3]")
    assert args.data_json == "[1,2,3]"


def test_plot_data_rejects_both_path_and_data_json():
    with pytest.raises(ValidationError, match="not both"):
        PlotDataArgs(path="data.csv", data_json="[1,2,3]")


def test_plot_data_rejects_neither_path_nor_data_json():
    with pytest.raises(ValidationError, match="either"):
        PlotDataArgs()


def test_plot_data_whitespace_only_source_counts_as_absent():
    with pytest.raises(ValidationError):
        PlotDataArgs(path="   ", data_json="   ")


def test_plot_data_kind_defaults_to_line():
    assert PlotDataArgs(data_json="[1]").kind == "line"


def test_plot_data_rejects_unsupported_kind():
    with pytest.raises(ValidationError):
        PlotDataArgs(data_json="[1]", kind="pie")


def test_plot_data_rejects_unknown_field():
    with pytest.raises(ValidationError):
        PlotDataArgs(data_json="[1]", made_up_field="x")


def test_plot_data_kind_normalizes_whitespace_and_case():
    assert PlotDataArgs(data_json="[1]", kind=" Line ").kind == "line"


# ── Undocumented aliases, caught by reading the real dispatch code ─────────

def test_spotify_accepts_documented_and_undocumented_aliases():
    for action in ("play", "pause", "resume", "next", "previous", "prev", "back", "current"):
        SpotifyArgs(action=action)  # must not raise


def test_spotify_play_without_query_is_not_an_error():
    """Verified against spotify_control's real dispatch: `action in
    ("resume", "play") and not query` is a legitimate alias for resume, not
    a missing-argument case -- do not add a required-field validator here."""
    SpotifyArgs(action="play")  # must not raise


def test_spotify_rejects_unknown_action():
    with pytest.raises(ValidationError):
        SpotifyArgs(action="shuffle")


def test_gcp_quota_accepts_usage_today_alias_and_defaults_to_status():
    assert GcpQuotaArgs().action == "status"
    GcpQuotaArgs(action="usage_today")  # must not raise


def test_geo_math_accepts_the_undocumented_sub_agent_actions():
    """analyze/reason/derive/explain are handled in a branch BEFORE
    geo_math's own documented "Actions:" docstring list -- must not be
    rejected just because they're not directly in that list. Requires one
    of expression/query/title, per the wrapper's own verified check."""
    for action in ("analyze", "reason", "derive", "explain"):
        GeoMathArgs(action=action, expression="k**2 - 1")  # must not raise


def test_geo_math_analyze_family_requires_a_problem_statement():
    with pytest.raises(ValidationError, match="requires"):
        GeoMathArgs(action="analyze")


def test_geo_math_accepts_documented_visualisation_actions_with_no_extra_fields():
    """geo_math_control() itself has no upfront required-field checks for
    these -- confirmed by reading jarvis/tools/geo_math_tool.py directly,
    not assumed from the docstring. Inventing requirements here would
    reject calls the real tool accepts."""
    for action in ("solve_symbolic", "wolfram", "wave_simulate_2d",
                    "plot_2d", "plot_contour", "plot_3d_surface", "plot_volume"):
        GeoMathArgs(action=action)  # must not raise


def test_geo_math_rejects_unknown_action():
    with pytest.raises(ValidationError):
        GeoMathArgs(action="teleport")


# ── Action-Literal + unknown-field-rejection contract, WITH each action's
# real required fields (verified against actual dispatch code, not
# docstrings -- see the module docstring for the geo_math/spotify lesson
# that motivated reading every dispatch body in full) ───────────────────────

_ACTION_TOOLS = [
    (GoogleCalendarArgs, {
        "list": {}, "search": {"query": "q"}, "batch_create": {"events_json": "[]"},
        "create": {"title": "t", "date": "2026-01-01"}, "delete": {"event_id": "e1"},
        "update": {"event_id": "e1"},
    }),
    (GmailArgs, {
        "list_unread": {}, "search": {"query": "q"}, "read": {"message_id": "m1"},
        "send": {"to": "a@b.com", "subject": "s", "body": "b"},
        "reply": {"message_id": "m1", "body": "b"}, "trash": {"message_id": "m1"},
        "mark_read": {"message_id": "m1"},
    }),
    (HudPanelsArgs, {"show": {}, "hide": {}, "toggle": {}}),
    (ScheduleArgs, {
        "add": {"title": "t", "run_at": "09:00"}, "list": {}, "done": {},
        "delete": {"task_id": "t1"}, "pause": {"task_id": "t1"}, "resume": {"task_id": "t1"},
    }),
    (TodoArgs, {
        "add": {"title": "t"}, "list": {}, "today": {}, "analyze": {},
        "done": {"todo_id": "id1"}, "delete": {"todo_id": "id1"},
        "edit": {"todo_id": "id1", "title": "new title"},
    }),
    (GoogleDriveArgs, {
        "search": {"query": "q"}, "list": {}, "read": {"file_id": "f1"},
        "download": {"file_id": "f1"}, "upload": {"local_path": "/tmp/x"},
        "share": {"file_id": "f1", "email": "a@b.com"}, "delete": {"file_id": "f1"},
    }),
    (ItuMailArgs, {
        "list_unread": {}, "search": {"query": "q"}, "read": {"uid": "1"},
        "send": {"to": "a@b.com", "subject": "s", "body": "b"},
        "reply": {"uid": "1", "body": "b"}, "trash": {"uid": "1"}, "mark_read": {"uid": "1"},
    }),
    (FinanceArgs, {
        "sync": {}, "summary": {}, "recent": {}, "top_categories": {},
        "set_budget": {"category": "food", "monthly_limit": 100.0},
        "budget_status": {}, "chart": {},
    }),
]


@pytest.mark.parametrize("cls,action_kwargs", _ACTION_TOOLS, ids=[c.__name__ for c, _ in _ACTION_TOOLS])
def test_every_documented_action_is_accepted_with_its_required_fields(cls, action_kwargs):
    for action, extra in action_kwargs.items():
        cls(action=action, **extra)  # must not raise


@pytest.mark.parametrize("cls", [c for c, _ in _ACTION_TOOLS], ids=[c.__name__ for c, _ in _ACTION_TOOLS])
def test_unknown_action_is_rejected(cls):
    with pytest.raises(ValidationError):
        cls(action="definitely_not_a_real_action")


@pytest.mark.parametrize("cls,action_kwargs", _ACTION_TOOLS, ids=[c.__name__ for c, _ in _ACTION_TOOLS])
def test_unknown_field_is_rejected(cls, action_kwargs):
    action, extra = next(iter(action_kwargs.items()))
    with pytest.raises(ValidationError):
        cls(action=action, this_field_does_not_exist="x", **extra)


@pytest.mark.parametrize("cls,action_kwargs", _ACTION_TOOLS, ids=[c.__name__ for c, _ in _ACTION_TOOLS])
def test_action_normalizes_whitespace_and_case(cls, action_kwargs):
    """Matches what every real dispatch function already does internally
    (`action.strip().lower()`) -- confirmed for all 8 tools here by reading
    their actual bodies."""
    action, extra = next(iter(action_kwargs.items()))
    loud = f" {action.upper()} "
    assert cls(action=loud, **extra).action == action


def test_google_calendar_defaults_preserved():
    args = GoogleCalendarArgs(action="list")
    assert args.duration_minutes == 60 and args.days_ahead == 7


def test_itu_mail_reply_all_defaults_false():
    assert ItuMailArgs(action="read", uid="1").reply_all is False


# ── Action-specific required-field rejections (the actual regression guard
# for the geo_math/spotify-shaped mistake: docstring-only inference) ────────

@pytest.mark.parametrize("cls,action,missing_kwargs", [
    (GoogleCalendarArgs, "create", {}),
    (GoogleCalendarArgs, "create", {"title": "t"}),  # date still missing
    (GoogleCalendarArgs, "search", {}),
    (GoogleCalendarArgs, "delete", {}),
    (GoogleCalendarArgs, "update", {}),
    (GoogleCalendarArgs, "batch_create", {}),
    (GmailArgs, "search", {}),
    (GmailArgs, "read", {}),
    (GmailArgs, "send", {"to": "a@b.com"}),  # subject/body still missing
    (GmailArgs, "reply", {}),
    (GmailArgs, "trash", {}),
    (GoogleDriveArgs, "search", {}),
    (GoogleDriveArgs, "read", {}),
    (GoogleDriveArgs, "upload", {}),
    (GoogleDriveArgs, "share", {"file_id": "f1"}),  # email still missing
    (ItuMailArgs, "search", {}),
    (ItuMailArgs, "send", {"to": "a@b.com", "subject": "s"}),  # body missing
    (ScheduleArgs, "add", {"title": "t"}),  # run_at still missing
    (ScheduleArgs, "delete", {}),
    (TodoArgs, "add", {}),
    (TodoArgs, "done", {}),
    (TodoArgs, "edit", {}),
    (FinanceArgs, "set_budget", {}),
    (FinanceArgs, "set_budget", {"category": "food"}),  # monthly_limit missing
])
def test_missing_required_field_is_rejected(cls, action, missing_kwargs):
    with pytest.raises(ValidationError):
        cls(action=action, **missing_kwargs)


def test_finance_set_budget_rejects_non_positive_limit():
    with pytest.raises(ValidationError):
        FinanceArgs(action="set_budget", category="food", monthly_limit=0)
    with pytest.raises(ValidationError):
        FinanceArgs(action="set_budget", category="food", monthly_limit=-5)


def test_todo_edit_category_other_does_not_count_as_a_change():
    """Mirrors the real dispatch's own special case: category=="other" is
    its own default, so passing it explicitly isn't a genuine update."""
    with pytest.raises(ValidationError):
        TodoArgs(action="edit", todo_id="id1", category="other")


def test_todo_edit_with_only_description_is_accepted():
    TodoArgs(action="edit", todo_id="id1", description="new detail")  # must not raise


def test_drive_role_is_normalized_and_constrained():
    assert GoogleDriveArgs(action="list", role=" Writer ").role == "writer"
    with pytest.raises(ValidationError):
        GoogleDriveArgs(action="list", role="owner")


# ── validate_args(): the reject-only gate used by prepare_execution_node ───

def test_validate_args_ok_returns_no_errors():
    ok, errors = validate_args(GmailArgs, {"action": "list_unread"})
    assert ok is True
    assert errors == []


def test_validate_args_failure_returns_structured_errors():
    ok, errors = validate_args(GmailArgs, {"action": "send", "to": "a@b.com"})
    assert ok is False
    assert len(errors) == 1
    assert set(errors[0]) == {"loc", "type", "msg"}
    assert errors[0]["type"] == "value_error"


def test_validate_args_never_returns_the_raw_input_value():
    """redact discipline: `input` (pydantic's own raw-value echo) must not
    survive into the trimmed error shape."""
    ok, errors = validate_args(GmailArgs, {"action": "nonexistent_action"})
    assert ok is False
    for e in errors:
        assert "input" not in e and "url" not in e and "ctx" not in e


def test_validate_args_handles_none_args():
    ok, errors = validate_args(GcpQuotaArgs, None)
    assert ok is True
    assert errors == []


def test_validate_args_unknown_field_produces_extra_forbidden_error():
    ok, errors = validate_args(GcpQuotaArgs, {"action": "status", "bogus": "x"})
    assert ok is False
    assert any(e["type"] == "extra_forbidden" for e in errors)
