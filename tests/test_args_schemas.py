"""Agent Runtime rev.2, Faz 6 -- jarvis/execution/args_schemas.py.

Pure pydantic-model tests: these schemas are DEFINED and TESTED here but
deliberately NOT wired into anything live yet (no @tool signature, no
prepare_execution_node validation) -- see args_schemas.py's own docstring
for why. tests/test_tool_registry_schemas.py covers the wiring onto
TOOL_SPECS surviving the domain/contract_status/timeout_class replace()
passes; this file is only about the schemas' own validation behavior.
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


# ── Undocumented aliases, caught by reading the real dispatch code ─────────

def test_spotify_accepts_documented_and_undocumented_aliases():
    for action in ("play", "pause", "resume", "next", "previous", "prev", "back", "current"):
        SpotifyArgs(action=action)  # must not raise


def test_spotify_rejects_unknown_action():
    with pytest.raises(ValidationError):
        SpotifyArgs(action="shuffle")


def test_gcp_quota_accepts_usage_today_alias_and_defaults_to_status():
    assert GcpQuotaArgs().action == "status"
    GcpQuotaArgs(action="usage_today")  # must not raise


def test_geo_math_accepts_the_undocumented_sub_agent_actions():
    """analyze/reason/derive/explain are handled in a branch BEFORE
    geo_math's own documented "Actions:" docstring list -- must not be
    rejected just because they're not directly in that list."""
    for action in ("analyze", "reason", "derive", "explain"):
        GeoMathArgs(action=action)  # must not raise


def test_geo_math_accepts_documented_visualisation_actions():
    for action in ("solve_symbolic", "wolfram", "wave_simulate_2d",
                    "plot_2d", "plot_contour", "plot_3d_surface", "plot_volume"):
        GeoMathArgs(action=action)  # must not raise


def test_geo_math_rejects_unknown_action():
    with pytest.raises(ValidationError):
        GeoMathArgs(action="teleport")


# ── The rest: generic action-Literal + unknown-field-rejection contract ────

_ACTION_TOOLS = [
    (GoogleCalendarArgs, ["list", "create", "batch_create", "delete", "search", "update"]),
    (GmailArgs, ["list_unread", "search", "read", "send", "reply", "trash", "mark_read"]),
    (HudPanelsArgs, ["show", "hide", "toggle"]),
    (ScheduleArgs, ["add", "list", "delete", "pause", "resume", "done"]),
    (TodoArgs, ["add", "list", "done", "delete", "analyze", "today", "edit"]),
    (GoogleDriveArgs, ["search", "list", "read", "download", "upload", "share", "delete"]),
    (ItuMailArgs, ["list_unread", "search", "read", "send", "reply", "trash", "mark_read"]),
    (FinanceArgs, ["sync", "summary", "recent", "top_categories",
                    "set_budget", "budget_status", "chart"]),
]


@pytest.mark.parametrize("cls,actions", _ACTION_TOOLS, ids=[c.__name__ for c, _ in _ACTION_TOOLS])
def test_every_documented_action_is_accepted(cls, actions):
    for action in actions:
        cls(action=action)  # must not raise


@pytest.mark.parametrize("cls", [c for c, _ in _ACTION_TOOLS], ids=[c.__name__ for c, _ in _ACTION_TOOLS])
def test_unknown_action_is_rejected(cls):
    with pytest.raises(ValidationError):
        cls(action="definitely_not_a_real_action")


@pytest.mark.parametrize("cls,actions", _ACTION_TOOLS, ids=[c.__name__ for c, _ in _ACTION_TOOLS])
def test_unknown_field_is_rejected(cls, actions):
    with pytest.raises(ValidationError):
        cls(action=actions[0], this_field_does_not_exist="x")


def test_google_calendar_defaults_preserved():
    args = GoogleCalendarArgs(action="list")
    assert args.duration_minutes == 60 and args.days_ahead == 7


def test_itu_mail_reply_all_defaults_false():
    assert ItuMailArgs(action="read", uid="1").reply_all is False
