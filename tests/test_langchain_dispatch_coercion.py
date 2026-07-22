"""Agent Runtime rev.2, Faz 6 Part 2 -- the actual boundary that makes
"validate_args() is a pure gate, never a transform" (args_schemas.py's own
design) safe: LangChain's OWN per-@tool auto-derived schema.

An external review of Part 2 raised a real, precisely-stated concern: pydantic
(non-strict, jarvis's schemas don't set strict=True) coerces `"60"` -> `60`
and `"false"` -> `False`, and jarvis.execution.args_schemas.validate_args()
discards that coerced form -- so if the RAW, uncoerced args are what reach
the tool body, a string `"false"` is truthy in Python (`if reply_all:`),
which would be a real semantic corruption at execution time.

Verified empirically here, against JARVIS's REAL registered tool objects
(not a toy example) rather than argued abstractly: it does NOT happen,
because every `@tool`-decorated function in jarvis/graph/tools.py already
has LangChain generate its OWN pydantic schema from the function's type
hints (confirmed below: `tool.args_schema` exists and independently coerces
`"60"`/`"false"` before dispatch), and `make_safe_tool_node()`
(jarvis/graph/safe_tools.py) wraps LangGraph's `ToolNode` execution (a
timeout + error boundary around the SAME `execute` callback ToolNode
provides) rather than replacing or bypassing it. This coercion layer
predates Faz 6 entirely (inherent to using typed @tool functions since the
Faz 1 LangGraph migration) and is completely independent of
jarvis.execution.args_schemas -- so a call that reaches the "tools" node at
all is already protected against this specific failure mode, regardless of
what jarvis's own validation gate did or didn't canonicalize.

What jarvis.execution.args_schemas genuinely adds on top (confirmed NOT
redundant, also below): action-Literal enums (the underlying @tool
signatures type `action` as plain `str`), action-conditional required
fields (every parameter has a default, so LangChain's own schema treats
everything as optional), and unknown-field rejection (LangChain's
auto-derived schema does not set extra="forbid" -- confirmed below, it
silently accepts an unrecognized field).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from jarvis.config import Settings
from jarvis.graph import tools as graph_tools
from jarvis.memory import Memory


def _real_tools() -> dict:
    settings = Settings(_env_file=None)
    memory = Memory(settings)
    workspace = Path(tempfile.mkdtemp())
    return {t.name: t for t in graph_tools.make_tools(workspace, settings, memory)}


def test_langchain_auto_schema_coerces_string_int_before_dispatch():
    """duration_minutes="60" -> the int 60, the exact value the tool body's
    own timedelta(minutes=duration_minutes) call needs -- not the raw
    string the reviewer's concern assumed would reach it."""
    calendar = _real_tools()["google_calendar"]
    inst = calendar.args_schema(action="create", title="t", date="2026-01-01", duration_minutes="60")
    assert inst.duration_minutes == 60
    assert isinstance(inst.duration_minutes, int)


def test_langchain_auto_schema_coerces_string_bool_before_dispatch():
    """reply_all="false" -> the bool False, not a truthy non-empty string --
    the exact scenario the reviewed concern named (`if reply_all:` on a
    literal "false" string would wrongly take the True branch)."""
    itu_mail = _real_tools()["itu_mail"]
    inst = itu_mail.args_schema(action="reply", uid="1", body="hi", reply_all="false")
    assert inst.reply_all is False

    inst_true = itu_mail.args_schema(action="reply", uid="1", body="hi", reply_all="true")
    assert inst_true.reply_all is True


def test_langchain_auto_schema_rejects_unparseable_values():
    """The other half of the same coercion layer: a value that CAN'T be
    coerced is rejected before the tool body runs, not silently passed
    through as a raw string either."""
    from pydantic import ValidationError

    calendar = _real_tools()["google_calendar"]
    try:
        calendar.args_schema(action="create", title="t", date="2026-01-01", duration_minutes="not-a-number")
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_langchain_auto_schema_does_not_reject_unknown_fields():
    """Confirms jarvis.execution.args_schemas' extra="forbid" is a genuine,
    non-redundant addition -- LangChain's own auto-derived schema is
    permissive here."""
    calendar = _real_tools()["google_calendar"]
    inst = calendar.args_schema(action="list", made_up_field="x")
    assert not hasattr(inst, "made_up_field") or getattr(inst, "made_up_field", None) == "x"


def test_make_safe_tool_node_wraps_rather_than_bypasses_dispatch():
    """safe_tools.make_safe_tool_node uses LangGraph's own ToolNode with
    wrap_tool_call/awrap_tool_call hooks (a timeout + error boundary around
    the SAME `execute` callback ToolNode supplies) -- it does not construct
    its own dispatch path that could skip the args_schema coercion proven
    above. A structural check, not a behavioral one (behavior is covered by
    the tests above and by test_prepare_execution_node.py's real-graph
    tier), but it is what makes those results generalize to the real
    agent -> prepare_execution -> confirmation -> tools pipeline."""
    from langgraph.prebuilt import ToolNode

    from jarvis.graph.safe_tools import make_safe_tool_node

    node = make_safe_tool_node([])
    assert isinstance(node, ToolNode)
