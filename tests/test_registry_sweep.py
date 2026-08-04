"""Agent Runtime rev.2, Faz 8 -- the registry sweep contract test.

The plan's own gap statement: "bugun yok; bozuk spec'li tool sessizce
girebiliyor". tool_registry.py has import-time wiring assertions for its
own side tables (_TOOL_DOMAINS/_ALPHA_STATUS/_TIMEOUT_CLASSES/_IDEMPOTENCY
completeness), but nothing sweeps the assembled specs' FIELD VALUES, and --
the real hole -- nothing cross-checks TOOL_SPECS against what make_tools()
actually exposes to the model. A new @tool added without a ToolSpec would
flow through get_spec()->None and land in policy_guard's unclassified
fallback (allowed=True at L3-confirm) silently; a ToolSpec whose tool was
never wired would document a capability that doesn't exist. Both now fail
loudly here.

make_tools() usage follows test_workflow_tools.py's precedent exactly:
MagicMock memory (nothing here invokes a tool body), Settings(_env_file=None),
isolated_cwd because several tool closures resolve Path("data")/... at
construction time.
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from jarvis.config import Settings
from jarvis.execution.postcondition import PostconditionSpec
from jarvis.tool_registry import (
    ALPHA_STATUS_VALUES,
    TOOL_SPECS,
    ToolSpec,
    get_alpha_status,
    tools_producing,
)

# Closed vocabularies, straight from tool_registry.py's own module docstring
# and field annotations. A new legitimate value belongs THERE first; updating
# this test is then a conscious, reviewed act rather than a silent drift.
_CATEGORIES = {"filesystem", "compute", "network", "memory", "external_api",
               "ui", "sub_agent", "mcp"}
_SIDE_EFFECT_TYPES = {"none", "local_read", "local_write", "local_execute",
                      "external_read", "external_write"}
_TIMEOUT_CLASSES = {"cooperative_async", "soft_thread_timeout",
                    "hard_process_timeout", "external_request_timeout"}
_IDEMPOTENCY_VALUES = {"none", "natural", "keyed"}
_EFFECT_SCOPES = {"unclassified", "reversible", "irreversible"}


@pytest.mark.parametrize("name", sorted(TOOL_SPECS))
def test_spec_fields_are_within_their_declared_vocabularies(name):
    spec = TOOL_SPECS[name]
    assert spec.name == name, "registry key must equal spec.name"
    assert spec.category in _CATEGORIES, spec.category
    assert 0 <= spec.risk_level <= 4, spec.risk_level
    assert spec.side_effect_type in _SIDE_EFFECT_TYPES, spec.side_effect_type
    assert spec.timeout_seconds > 0
    assert spec.description.strip(), "empty description -- docs/TOOLS.md renders this"
    assert spec.domain.strip(), "static tools must carry a real domain (\"\" = mcp quarantine)"
    assert spec.contract_status in ALPHA_STATUS_VALUES, spec.contract_status
    assert spec.timeout_class in _TIMEOUT_CLASSES, spec.timeout_class
    assert spec.idempotency in _IDEMPOTENCY_VALUES, spec.idempotency
    assert spec.effect_scope in _EFFECT_SCOPES, spec.effect_scope


@pytest.mark.parametrize("name", sorted(TOOL_SPECS))
def test_l3_and_above_always_requires_confirmation(name):
    """The safety model's core invariant (docs/SAFETY.md): every external-
    effect (L3+) capability sits behind the confirmation gate. A new L3 tool
    registered with requires_confirmation=False would silently bypass the
    entire Faz 4 kernel -- this is the guard the plan means by 'bozuk spec'li
    tool sessizce girebiliyor'."""
    spec = TOOL_SPECS[name]
    if spec.risk_level >= 3:
        assert spec.requires_confirmation, (
            f"{name} is L{spec.risk_level} but requires_confirmation=False"
        )


@pytest.mark.parametrize("name", sorted(TOOL_SPECS))
def test_registered_args_schemas_are_strict_pydantic_models(name):
    spec = TOOL_SPECS[name]
    if spec.args_schema is None:
        return
    assert isinstance(spec.args_schema, type) and issubclass(spec.args_schema, BaseModel), name
    assert spec.args_schema.model_config.get("extra") == "forbid", (
        f"{name}.args_schema must inherit the shared extra='forbid' base"
    )


@pytest.mark.parametrize("name", sorted(TOOL_SPECS))
def test_declared_postconditions_are_real_specs(name):
    """Every declared postcondition must be a validated PostconditionSpec
    (pydantic enforces the closed `kind` Literal at construction) -- a plain
    dict smuggled into the tuple would defer the type error to the first
    live run of the postcondition runner."""
    for pc in TOOL_SPECS[name].postconditions:
        assert isinstance(pc, PostconditionSpec), (name, type(pc))


def test_dynamic_spec_defaults_are_fail_closed():
    """register_dynamic_spec() (Faz 5 MCP tools) relies on the DATACLASS
    defaults for every rev.2 field -- tool_registry's own comments promise
    those defaults are fail-closed. Pin the promise: an unknown external
    tool must never be presumed safe to re-run (idempotency), compensable
    (effect_scope), or schema-validated (args_schema)."""
    minimal = ToolSpec("x_dynamic", "mcp", 3, True, "external_write")
    assert minimal.idempotency == "none"
    assert minimal.effect_scope == "unclassified"
    assert minimal.args_schema is None
    assert minimal.postconditions == ()
    assert minimal.contract_status == "shadow_validated"
    # Paket E: no per-action downgrades for a tool nobody has classified. An
    # MCP server that happens to expose an action called "list" must not get a
    # read downgrade out of a name collision.
    assert minimal.actions == {}
    # Post-MVP Faz 6: an unclassified tool produces no Working Set object, so
    # it can never satisfy a completion contract. Fail-closed in the same
    # direction as the rest: the contract stays unmet rather than being
    # answered by an MCP tool nobody mapped.
    assert minimal.working_object_kind == ""
    assert minimal.working_object_operation == ""


def test_toolspec_has_no_unswept_fields():
    """Completeness backstop: if ToolSpec grows a new field, this sweep must
    grow with it -- fail loudly instead of silently not checking it."""
    known = {
        "name", "category", "risk_level", "requires_confirmation",
        "side_effect_type", "timeout_seconds", "supports_background",
        "description", "domain", "args_schema", "postconditions",
        "idempotency", "effect_scope", "contract_status", "timeout_class",
        "actions", "working_object_kind", "working_object_operation",
    }
    actual = {f.name for f in dataclass_fields(ToolSpec)}
    assert actual == known, (
        f"ToolSpec fields changed: +{actual - known} -{known - actual} -- "
        "extend test_registry_sweep.py's field checks for the new field(s)"
    )


# ── working-object metadata (Post-MVP Faz 6) ────────────────────────────────

def test_the_contracts_capability_set_is_derived_not_hand_listed():
    """The completion contract asks the registry which tools create a chart
    rather than keeping its own list. Two constants naming the same tools do
    not prevent drift -- they only make it quieter."""
    assert tools_producing("chart", "create") == {"plot_data"}
    assert tools_producing("chart", "revise") == {"chart_revise"}


def test_no_tool_declares_half_the_metadata():
    """A kind without an operation (or the reverse) would be invisible to
    tools_producing() while looking classified in the registry."""
    for name, spec in TOOL_SPECS.items():
        assert bool(spec.working_object_kind) == bool(spec.working_object_operation), (
            f"{name} declares only half of its working-object metadata"
        )


def test_a_tool_that_registers_no_working_object_declares_none():
    """finance('export') draws a chart and declares a kind='chart' artifact
    through the same generate_plot() path -- but it registers no Working Set
    object, so the chart it produces cannot be revised. Claiming it here
    would let a creation contract be satisfied by something the user then
    cannot edit, which is the opposite of what the contract is for."""
    assert TOOL_SPECS["finance"].working_object_kind == ""
    assert "finance" not in tools_producing("chart", "create")


# ── the TOOL_SPECS <-> make_tools() correspondence ──────────────────────────

def _exposed_tool_names(tmp_path) -> set[str]:
    from jarvis.graph.tools import make_tools

    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    return {t.name for t in make_tools(tmp_path, settings, MagicMock())}


def test_every_exposed_tool_has_a_registered_spec(isolated_cwd, tmp_path):
    """A @tool in make_tools() with no ToolSpec is the exact 'silently enters'
    hole: policy_guard would classify it via the unregistered-name fallback
    instead of a reviewed risk decision."""
    exposed = _exposed_tool_names(tmp_path)
    unregistered = exposed - set(TOOL_SPECS)
    assert not unregistered, (
        f"tools exposed to the model without a ToolSpec: {sorted(unregistered)}"
    )


def test_every_spec_is_exposed_unless_alpha_disabled(isolated_cwd, tmp_path):
    """The other direction: a ToolSpec whose tool is not actually wired is
    dead registry weight that docs/policy decisions silently rely on. The
    only legitimate absence is an alpha-'disabled' capability (python_run --
    structurally removed from the model surface by Faz 0)."""
    exposed = _exposed_tool_names(tmp_path)
    expected_absent = {n for n in TOOL_SPECS if get_alpha_status(n) == "disabled"}
    missing = set(TOOL_SPECS) - exposed - expected_absent
    assert not missing, f"registered but never exposed (and not disabled): {sorted(missing)}"
    assert expected_absent & exposed == set(), (
        f"alpha-disabled tools leaked onto the model surface: "
        f"{sorted(expected_absent & exposed)}"
    )


def test_file_write_postcondition_wiring_is_intact(isolated_cwd, tmp_path):
    """file_write is the one tool with live postconditions (Faz 3) AND the one
    registered compensator (Faz 7) -- the pair the plan's per-capability
    contract row 'postcondition ihlali -> failed' runs through. Pin the
    wiring, and prove a violated required postcondition really reports
    failed, end to end through the real runner."""
    from jarvis.execution.postcondition_runner import run_postconditions

    spec = TOOL_SPECS["file_write"]
    assert {pc.kind for pc in spec.postconditions} == {"file_exists", "path_within_workspace"}
    assert spec.effect_scope == "reversible"

    # The promised file deliberately does NOT exist -> file_exists must fail.
    results = run_postconditions(
        spec.postconditions,
        workspace=tmp_path,
        args={"path": "never_written.txt"},
        tool_result_content="ok",
    )
    by_kind = {r.spec.kind: r for r in results}
    assert by_kind["file_exists"].status == "failed"
    assert by_kind["file_exists"].spec.severity == "required"


# ── Post-MVP Faz 2.75, Paket E: the per-action table ────────────────────────

@pytest.mark.parametrize("name", sorted(TOOL_SPECS))
def test_action_specs_are_action_specs(name):
    """Same shape check the postconditions sweep does: a plain dict slipped in
    here would be read by policy_guard as attribute access and blow up at the
    worst moment -- inside a live gate decision."""
    from jarvis.tool_registry import ActionSpec

    for action, spec in (TOOL_SPECS[name].actions or {}).items():
        assert isinstance(action, str) and action == action.strip().lower(), (name, action)
        assert isinstance(spec, ActionSpec), (name, action, type(spec))


@pytest.mark.parametrize("name", sorted(TOOL_SPECS))
def test_a_declared_action_is_never_riskier_than_its_tool(name):
    """The table exists to DOWNGRADE documented reads. Using it to raise a
    single action's risk would hide that escalation from every reader of the
    tool's own risk_level -- if an action needs to be riskier than its tool,
    the tool is classified wrong."""
    spec = TOOL_SPECS[name]
    for action, aspec in (spec.actions or {}).items():
        assert aspec.risk_level <= spec.risk_level, (name, action)


def test_toolspec_stays_hashable():
    """ToolSpec is a frozen value object and was hashable before `actions`
    existed. A dict field silently removes that unless it is excluded from
    __hash__; nothing depends on it today, which is exactly why the loss would
    have gone unnoticed until something did."""
    assert isinstance(hash(TOOL_SPECS["todo"]), int)
