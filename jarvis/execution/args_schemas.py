"""Tool argument schemas -- Agent Runtime rev.2, Faz 6 (typed schemas + bounded
repair), plan section: "her capability args_schema tasir (args_schema=None
yasak)". Priority order per the plan's own text: plot_data first (the B6-
adjacent discriminated-union example), then the action-dispatch tools (their
`action: str` is free-text today; policy_guard._READ_ACTIONS already keys
risk decisions off this same string).

Part 1 defined these schemas and left them completely unwired. Part 2 wires
validate_args() into prepare_execution_node (jarvis/graph/nodes.py) as a
REJECT-ONLY GATE -- it never substitutes canonical args back into the call.
This is a deliberate resolution of a real risk an external review caught:
if validation normalized " PLAY " to "play" and that canonical form were
signed/fingerprinted while the RAW form were what actually executed, the
signed digest and the executed args would diverge -- undermining exactly
the guarantee Faz 2's approval binding exists to provide. Avoided by
construction here: every dispatch function this module validates against
already does its OWN `action.strip().lower()` normalization internally
(confirmed by reading calendar.py/gmail.py/drive.py/itu_mail.py/
finance.py/spotify.py/schedule/todo/gcp_quota/geo_math's actual dispatch
bodies, not just docstrings -- same discipline as Part 1's alias
discoveries) -- so raw args always execute safely, and the `mode="before"`
normalizers below exist ONLY to make the accept/reject decision accurate,
never to change what gets signed or run.

Still not wired onto any live `@tool` function signature in
jarvis/graph/tools.py -- the model-facing schema stays free-text `action:
str` this phase too. Promoting a schema onto that live boundary changes
what the LLM API itself will accept, silently, the moment it's wrong;
these schemas get validated at the internal prepare_execution_node gate
first (observable via audit_log/tests) before that promotion is trusted.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _lower_strip(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


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

    @field_validator("kind", mode="before")
    @classmethod
    def _normalize_kind(cls, v: Any) -> Any:
        return _lower_strip(v)

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
    # jarvis/tools/spotify.py's spotify_control(). No required-field
    # validator: spotify_control's own dispatch shows `play` WITHOUT
    # `query` is a legitimate alias for `resume`
    # (`action in ("resume", "play") and not query: sp.start_playback()`),
    # not a missing-argument error -- confirmed by reading the real
    # dispatch, not inferred from the docstring.
    action: Literal["play", "pause", "resume", "next", "previous", "prev", "back", "current"]
    query: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)


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

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "GoogleCalendarArgs":
        # Verified against jarvis/tools/calendar.py's calendar_control() body.
        if self.action == "create" and not (self.title and self.date):
            raise ValueError("action='create' requires 'title' and 'date'")
        if self.action == "batch_create" and not self.events_json:
            raise ValueError("action='batch_create' requires 'events_json'")
        if self.action == "search" and not self.query:
            raise ValueError("action='search' requires 'query'")
        if self.action == "delete" and not (self.event_id or self.query):
            raise ValueError("action='delete' requires 'event_id' or 'query'")
        if self.action == "update" and not self.event_id:
            raise ValueError("action='update' requires 'event_id'")
        return self


class GmailArgs(_StrictArgs):
    action: Literal["list_unread", "search", "read", "send", "reply", "trash", "mark_read"]
    query: str = ""
    message_id: str = ""
    to: str = ""
    subject: str = ""
    body: str = ""
    max_results: int = 10

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "GmailArgs":
        # Verified against jarvis/tools/gmail.py's gmail_control() body.
        if self.action == "search" and not self.query:
            raise ValueError("action='search' requires 'query'")
        if self.action == "read" and not self.message_id:
            raise ValueError("action='read' requires 'message_id'")
        if self.action == "send" and not (self.to and self.subject and self.body):
            raise ValueError("action='send' requires 'to', 'subject', and 'body'")
        if self.action == "reply" and not (self.message_id and self.body):
            raise ValueError("action='reply' requires 'message_id' and 'body'")
        if self.action == "trash" and not self.message_id:
            raise ValueError("action='trash' requires 'message_id'")
        if self.action == "mark_read" and not self.message_id:
            raise ValueError("action='mark_read' requires 'message_id'")
        return self


class HudPanelsArgs(_StrictArgs):
    # No further server-side dispatch to cross-check -- panel_control()
    # (jarvis/ws.py) forwards `action` verbatim to the Electron HUD's own
    # frontend logic, outside this codebase. The docstring's 3 values are
    # the only authoritative source available here; no required-field
    # validator since nothing server-side enforces one.
    action: Literal["show", "hide", "toggle"]
    panels: str = "all"

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)


class ScheduleArgs(_StrictArgs):
    action: Literal["add", "list", "delete", "pause", "resume", "done"]
    title: str = ""
    description: str = ""
    schedule_type: str = "daily"
    run_at: str = ""
    days_of_week: str = ""
    day_of_month: int = 0
    task_id: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "ScheduleArgs":
        # Verified against the schedule tool's inline dispatch in
        # jarvis/graph/tools.py.
        if self.action == "add" and not (self.title and self.run_at):
            raise ValueError("action='add' requires 'title' and 'run_at'")
        if self.action in ("delete", "pause", "resume") and not self.task_id:
            raise ValueError(f"action='{self.action}' requires 'task_id'")
        return self


class TodoArgs(_StrictArgs):
    action: Literal["add", "list", "done", "delete", "analyze", "today", "edit"]
    title: str = ""
    description: str = ""
    due_date: str = ""
    category: str = "other"
    todo_id: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "TodoArgs":
        # Verified against the todo tool's inline dispatch in
        # jarvis/graph/tools.py.
        if self.action == "add" and not self.title:
            raise ValueError("action='add' requires 'title'")
        if self.action in ("done", "delete") and not self.todo_id:
            raise ValueError(f"action='{self.action}' requires 'todo_id'")
        if self.action == "edit":
            if not self.todo_id:
                raise ValueError("action='edit' requires 'todo_id'")
            # The real dispatch also excludes category=="other" (its own
            # default) from counting as a change -- mirrored here.
            has_update = bool(
                self.title or self.description or self.due_date
                or (self.category and self.category != "other")
            )
            if not has_update:
                raise ValueError(
                    "action='edit' requires at least one of "
                    "title/description/due_date/category to actually change"
                )
        return self


class GoogleDriveArgs(_StrictArgs):
    action: Literal["search", "list", "read", "download", "upload", "share", "delete"]
    query: str = ""
    folder_id: str = ""
    file_id: str = ""
    dest_path: str = ""
    local_path: str = ""
    name: str = ""
    email: str = ""
    # Not enforced by drive_control itself (role flows straight to the
    # Drive API) -- this Literal is a genuinely NEW safety net, not a
    # duplicate of an existing check, matching the tool's own documented
    # domain (see graph/tools.py's google_drive docstring).
    role: Literal["reader", "writer", "commenter"] = "reader"
    max_results: int = 20

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "GoogleDriveArgs":
        # Verified against jarvis/tools/drive.py's drive_control() body.
        if self.action == "search" and not self.query:
            raise ValueError("action='search' requires 'query'")
        if self.action == "read" and not self.file_id:
            raise ValueError("action='read' requires 'file_id'")
        if self.action == "download" and not self.file_id:
            raise ValueError("action='download' requires 'file_id'")
        if self.action == "upload" and not self.local_path:
            raise ValueError("action='upload' requires 'local_path'")
        if self.action == "share" and not (self.file_id and self.email):
            raise ValueError("action='share' requires 'file_id' and 'email'")
        if self.action == "delete" and not self.file_id:
            raise ValueError("action='delete' requires 'file_id'")
        return self


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

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "ItuMailArgs":
        # Verified against jarvis/tools/itu_mail.py's itu_mail_control() body.
        if self.action == "search" and not self.query:
            raise ValueError("action='search' requires 'query'")
        if self.action == "read" and not self.uid:
            raise ValueError("action='read' requires 'uid'")
        if self.action == "send" and not (self.to and self.subject and self.body):
            raise ValueError("action='send' requires 'to', 'subject', and 'body'")
        if self.action == "reply" and not (self.uid and self.body):
            raise ValueError("action='reply' requires 'uid' and 'body'")
        if self.action == "trash" and not self.uid:
            raise ValueError("action='trash' requires 'uid'")
        if self.action == "mark_read" and not self.uid:
            raise ValueError("action='mark_read' requires 'uid'")
        return self


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

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "FinanceArgs":
        # Verified against jarvis/tools/finance.py's finance_control() body.
        if self.action == "set_budget":
            if not self.category:
                raise ValueError("action='set_budget' requires 'category'")
            if self.monthly_limit <= 0:
                raise ValueError("action='set_budget' requires a positive 'monthly_limit'")
        return self


class GcpQuotaArgs(_StrictArgs):
    # "usage_today" is an undocumented alias for "usage" -- see gcp_quota's
    # own dispatch in jarvis/graph/tools.py. No required-field validator:
    # every action there takes no other arguments.
    action: Literal["status", "usage", "usage_today", "forecast"] = "status"

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)


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

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        return _lower_strip(v)

    @model_validator(mode="after")
    def _required_for_action(self) -> "GeoMathArgs":
        # Only the analyze/reason/derive/explain branch has a verified
        # required-input check (jarvis/graph/tools.py's geo_math wrapper:
        # "problem = expression or query or title; if not problem: ...").
        # geo_math_control() itself (jarvis/tools/geo_math_tool.py) has no
        # equivalent upfront checks for the other actions -- it defers to
        # the deeper compute functions, so inventing requirements for
        # solve_symbolic/wolfram/plot_* here would reject calls the real
        # tool would otherwise accept (over-validation is its own bug).
        if self.action in ("analyze", "reason", "derive", "explain"):
            if not (self.expression or self.query or self.title):
                raise ValueError(
                    f"action='{self.action}' requires 'expression', 'query', or 'title'"
                )
        return self


def validate_args(schema_cls: type[BaseModel], args: dict[str, Any] | None) -> tuple[bool, list[dict]]:
    """Validate raw tool-call args against a schema. Returns (ok, errors).

    A pure GATE, never a transform: on success the caller's raw args are
    unchanged and untouched by this function -- see this module's own
    docstring for why substituting canonical args back into the call would
    reintroduce the exact digest/execution divergence risk this design
    avoids. errors is pydantic's own ValidationError.errors() shape,
    trimmed to loc/type/msg (dropping `input`/`url`/`ctx` -- `input` in
    particular echoes the raw argument value, which this codebase's
    redaction discipline says must not be persisted unredacted; loc/type/
    msg are static, descriptive strings, never the caller's data).
    """
    try:
        schema_cls(**(args or {}))
        return True, []
    except ValidationError as exc:
        errors = [
            {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
            for e in exc.errors()
        ]
        return False, errors
