"""Tool argument schemas -- Agent Runtime rev.2, Faz 6 (typed schemas + bounded
repair), plan section: "her capability args_schema tasir (args_schema=None
yasak)". Priority order per the plan's own text: plot_data first (the B6-
adjacent discriminated-union example), then the action-dispatch tools (their
`action: str` is free-text today; policy_guard._READ_ACTIONS already keys
risk decisions off this same string).

DEFINED AND TESTED, DELIBERATELY NOT YET WIRED anywhere -- an honest scope
cut, not an oversight. ToolSpec.args_schema has been a declared "Faz 6
destination" since Faz 1 ("None = not yet typed (every tool today)"); this
module starts filling it in for the plan's named priorities rather than
attempting all ~34 model-visible tools in one pass, and stops short of two
further steps on purpose:

1. No `@tool` function signature in jarvis/graph/tools.py is touched. Two
   real, non-obvious discoveries while building this file: geo_math accepts
   "analyze"/"reason"/"derive"/"explain" as an ENTIRELY SEPARATE dispatch
   branch handled BEFORE its own documented "Actions:" list (nowhere in its
   own docstring), and spotify's control function accepts "prev"/"back" as
   undocumented aliases for "previous". Both are captured correctly below
   because each tool's FULL dispatch chain was read, not just its docstring
   -- but that same experience is the reason live, model-facing schemas
   (which change what the LLM API itself will accept, silently, the moment
   they're wrong) are not touched this session. A mistake in a schema used
   only for future internal validation is cheap to fix later; a mistake in
   a live tool-calling schema is a quiet regression. Promoting a verified
   Literal onto the real @tool signature is a deliberate next step, not
   this one.
2. Nothing in jarvis/graph/nodes.py or jarvis/policy_guard.py reads
   ToolSpec.args_schema yet -- prepare_execution_node's own docstring has
   said "normalize (pass-through today -- no ToolSpec carries an
   args_schema yet, that's Faz 6)" since Faz 2; wiring real validation (and
   the bounded-repair pipeline built on top of it: normalize -> validate ->
   one repair -> alternative capability -> explicit error, re-triggering
   Faz 2's approval binding) into that node is the next increment.

Every schema forbids unknown fields (extra="forbid") -- the plan's separate
"unknown-field rejection tum semalarda" bullet, which needs no code beyond
this shared base once each tool has a schema at all.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlotDataArgs(_StrictArgs):
    """The plan's own priority #1 example: EITHER `path` OR `data_json`, not
    both, not neither -- today enforced only at runtime inside plot_data's
    own body (an [ERROR] string), this is the schema-level version. `kind`
    is closed to jarvis.tools.plotting.SUPPORTED_KINDS, the actual live
    check plot_data's body already performs."""
    path: str = ""
    kind: Literal["line", "scatter", "bar", "hist", "box", "violin", "heatmap"] = "line"
    x: str = ""
    y: str = ""
    title: str = ""
    hue: str = ""
    output: str = ""
    data_json: str = ""

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "PlotDataArgs":
        has_path = bool(self.path.strip())
        has_inline = bool(self.data_json.strip())
        if has_path and has_inline:
            raise ValueError("provide either `path` or `data_json`, not both")
        if not has_path and not has_inline:
            raise ValueError("provide either `path` (a data file) or `data_json` (inline values)")
        return self


class SpotifyArgs(_StrictArgs):
    # "prev"/"back" are undocumented aliases for "previous" -- see
    # jarvis/tools/spotify.py's spotify_control().
    action: Literal["play", "pause", "resume", "next", "previous", "prev", "back", "current"]
    query: str = ""


class GoogleCalendarArgs(_StrictArgs):
    action: Literal["list", "create", "batch_create", "delete", "search", "update"]
    title: str = ""
    date: str = ""
    time: str = ""
    duration_minutes: int = 60
    description: str = ""
    location: str = ""
    days_ahead: int = 7
    query: str = ""
    event_id: str = ""
    events_json: str = ""


class GmailArgs(_StrictArgs):
    action: Literal["list_unread", "search", "read", "send", "reply", "trash", "mark_read"]
    query: str = ""
    message_id: str = ""
    to: str = ""
    subject: str = ""
    body: str = ""
    max_results: int = 10


class HudPanelsArgs(_StrictArgs):
    # No further server-side dispatch to cross-check -- panel_control()
    # (jarvis/ws.py) forwards `action` verbatim to the Electron HUD's own
    # frontend logic, outside this codebase. The docstring's 3 values are
    # the only authoritative source available here.
    action: Literal["show", "hide", "toggle"]
    panels: str = "all"


class ScheduleArgs(_StrictArgs):
    action: Literal["add", "list", "delete", "pause", "resume", "done"]
    title: str = ""
    description: str = ""
    schedule_type: str = "daily"
    run_at: str = ""
    days_of_week: str = ""
    day_of_month: int = 0
    task_id: str = ""


class TodoArgs(_StrictArgs):
    action: Literal["add", "list", "done", "delete", "analyze", "today", "edit"]
    title: str = ""
    description: str = ""
    due_date: str = ""
    category: str = "other"
    todo_id: str = ""


class GoogleDriveArgs(_StrictArgs):
    action: Literal["search", "list", "read", "download", "upload", "share", "delete"]
    query: str = ""
    folder_id: str = ""
    file_id: str = ""
    dest_path: str = ""
    local_path: str = ""
    name: str = ""
    email: str = ""
    role: str = "reader"
    max_results: int = 20


class ItuMailArgs(_StrictArgs):
    action: Literal["list_unread", "search", "read", "send", "reply", "trash", "mark_read"]
    query: str = ""
    uid: str = ""
    to: str = ""
    subject: str = ""
    body: str = ""
    cc: str = ""
    reply_all: bool = False
    max_results: int = 20


class FinanceArgs(_StrictArgs):
    action: Literal[
        "sync", "summary", "recent", "top_categories",
        "set_budget", "budget_status", "chart",
    ]
    year: int = 0
    month: int = 0
    category: str = ""
    monthly_limit: float = 0.0
    alert_threshold_pct: float = 0.8
    months_back: int = 1
    n: int = 5


class GcpQuotaArgs(_StrictArgs):
    # "usage_today" is an undocumented alias for "usage" -- see gcp_quota's
    # own dispatch in jarvis/graph/tools.py.
    action: Literal["status", "usage", "usage_today", "forecast"] = "status"


class GeoMathArgs(_StrictArgs):
    # analyze/reason/derive/explain are handled in a branch BEFORE geo_math's
    # documented dispatch and are absent from its own docstring's "Actions:"
    # list -- see this module's own docstring for how that was caught.
    action: Literal[
        "analyze", "reason", "derive", "explain",
        "solve_symbolic", "wolfram", "wave_simulate_2d",
        "plot_2d", "plot_contour", "plot_3d_surface", "plot_volume",
    ]
    expression: str = ""
    variable: str = ""
    query: str = ""
    x_data: str = "[]"
    y_data: str = "[]"
    grid_data: str = "[]"
    volume_data: str = "[]"
    source_pos: str = "[5, 5]"
    velocity_grid: str = "[]"
    duration: float = 0.5
    nz: int = 100
    nx: int = 100
    levels: int = 20
    cmap: str = "RdBu_r"
    title: str = ""
    plot_type: str = "line"
