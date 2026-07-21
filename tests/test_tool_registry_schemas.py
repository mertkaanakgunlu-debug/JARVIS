"""Agent Runtime rev.2, Faz 6 -- ToolSpec.args_schema wiring in tool_registry.py.

TOOL_SPECS is rebuilt three times after the base ToolSpec(...) list (domain,
then contract_status, then timeout_class, each via dataclasses.replace()) --
this locks in that args_schema, set on the ORIGINAL constructor calls,
survives all three passes intact rather than being silently dropped by one
of them replacing the whole spec.
"""
from __future__ import annotations

from jarvis.execution import args_schemas
from jarvis.tool_registry import TOOL_SPECS, get_spec

_EXPECTED = {
    "plot_data": args_schemas.PlotDataArgs,
    "geo_math": args_schemas.GeoMathArgs,
    "spotify": args_schemas.SpotifyArgs,
    "hud_panels": args_schemas.HudPanelsArgs,
    "schedule": args_schemas.ScheduleArgs,
    "todo": args_schemas.TodoArgs,
    "finance": args_schemas.FinanceArgs,
    "gcp_quota": args_schemas.GcpQuotaArgs,
    "google_calendar": args_schemas.GoogleCalendarArgs,
    "gmail": args_schemas.GmailArgs,
    "google_drive": args_schemas.GoogleDriveArgs,
    "itu_mail": args_schemas.ItuMailArgs,
}


def test_every_priority_tool_has_its_schema_registered():
    for name, schema_cls in _EXPECTED.items():
        spec = get_spec(name)
        assert spec is not None, f"{name} missing from TOOL_SPECS"
        assert spec.args_schema is schema_cls, f"{name}.args_schema mismatch"


def test_args_schema_survives_the_domain_replace_pass():
    """domain is assigned by a LATER replace() pass than the base ToolSpec(...)
    constructor call args_schema is set in -- if replace() ever regressed to
    replacing the whole spec instead of one field, domain would be right and
    args_schema would go back to None (or vice versa). Checking both on the
    same object proves neither pass clobbered the other's field."""
    spec = get_spec("plot_data")
    assert spec.args_schema is args_schemas.PlotDataArgs
    assert spec.domain == "data"


def test_args_schema_survives_the_contract_status_and_timeout_class_passes():
    spec = get_spec("gmail")
    assert spec.args_schema is args_schemas.GmailArgs
    assert spec.contract_status  # non-empty -- the alpha-status pass ran
    assert spec.timeout_class == "external_request_timeout"


def test_tools_without_a_schema_yet_stay_none():
    """Honest scope boundary: only the plan's named priorities got a schema
    this phase. web_search (a simple, non-action-dispatch tool) must not
    have silently acquired one."""
    spec = get_spec("web_search")
    assert spec is not None
    assert spec.args_schema is None


def test_all_registered_schemas_forbid_unknown_fields():
    """Cheap belt-and-suspenders check that every schema wired onto
    TOOL_SPECS actually inherits the shared extra='forbid' base -- a schema
    that forgot to subclass _StrictArgs would silently reopen the
    unknown-field gap this phase closes."""
    for spec in TOOL_SPECS.values():
        if spec.args_schema is None:
            continue
        assert spec.args_schema.model_config.get("extra") == "forbid", spec.name
