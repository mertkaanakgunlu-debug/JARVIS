"""Deterministic off/shadow equivalence -- Agent Runtime rev.2, Faz 1.

The live A/B could not answer whether shadow mode changes runtime behavior:
with a stochastic model at n=5, the per-scenario variance is larger than the
effect being looked for (champ 62/65, champ-shadow 59/65, ctl-off 59/65 -- the
two Faz-1 configs tied while losing DIFFERENT scenarios). An external review's
conclusion, adopted here: a live E2E score cannot be the equivalence test, but
a deterministic replay can, and must pass before any further live run.

Method: drive the real compiled graph with a SCRIPTED model, so the model's
output is identical across modes BY CONSTRUCTION rather than by luck, then run
the identical fixture through mode="off" and mode="shadow" and require every
externally observable outcome to match exactly.

Why scripted rather than replaying captured request/response pairs: the
recorded A/B artifacts hold final answers and traces, not full LLM exchanges,
so replaying them would be partial. A script pins the model's contribution to
exactly zero variance -- a strictly stronger guarantee for this question. The
arguments themselves stay faithful: B6's fixture uses the real wrong binding
observed live (x='1, 2, 3, 4' where a column NAME belongs), lifted from the
champ-shadow audit log.

MUST be identical across modes:
    tool selection, raw tool arguments, tool results, user-visible response,
    external side effects (files), error text and reason codes, number of
    agent/tool rounds, and the pre-existing accounting fields.
MAY differ (and only these):
    execution_envelopes -- the shadow ledger itself.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from jarvis.config import Settings
from jarvis.graph.graph import build_graph
from jarvis.memory import Memory

# Fields that legitimately vary run to run and are normalized before comparison.
# An explicit allowlist, not a blanket ignore: anything NOT named here must
# match exactly, so a real behavioral difference cannot hide behind "it's
# probably just a timestamp".
VOLATILE_ALLOWLIST = ("created_at", "execution_id", "id", "run_id", "thread_id")


class ScriptedLLM:
    """One deterministic fake model standing in for every LLM call in the graph.

    Consumed strictly in order, so a mode that changed the NUMBER or ORDER of
    LLM calls would desynchronize the script and surface as a diff -- which is
    itself one of the indirect effects worth catching (state size, checkpoint
    writes and retry accounting could all in principle change call ordering
    without touching any prompt).
    """

    def __init__(self, script: list):
        self._script = list(script)
        self.consumed = 0

    # get_llm() may bind tools / add fallbacks; both are client-side no-ops here.
    def bind_tools(self, *a, **k):
        return self

    def with_fallbacks(self, *a, **k):
        return self

    def bind(self, *a, **k):
        return self

    async def ainvoke(self, messages, **kwargs):
        if self.consumed >= len(self._script):
            # Deliberately not an exception: a mode that made MORE calls should
            # show up as a compared difference, not as a crash in one arm only.
            self.consumed += 1
            return AIMessage(content="[script exhausted]")
        item = self._script[self.consumed]
        self.consumed += 1
        return item if isinstance(item, AIMessage) else AIMessage(content=str(item))


def _ai_tool(name: str, args: dict, call_id: str = "call_0") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


ACCEPT = '{"verdict": "accept", "critique": "", "score": 9}'


@dataclass
class Fixture:
    name: str
    user_query: str
    script: list
    expect_tool: str | None = None
    notes: str = ""
    files_expected: bool = False
    extra: dict = field(default_factory=dict)


# ── Fixture set: the classes an external review asked to be covered ──────────

FIXTURES = [
    Fixture(
        name="no_tool_conversational",
        user_query="merhaba",
        script=["Merhaba! Ne yapabilirim?", ACCEPT],
        notes="Baseline: the graph must be identical when no tool runs at all.",
    ),
    Fixture(
        name="successful_single_tool",
        user_query="jarvis_test.txt adinda dosya olustur",
        script=[
            _ai_tool("file_write", {"path": "jarvis_test.txt", "content": "merhaba dunya"}),
            "Dosya olusturuldu.",
            ACCEPT,
        ],
        expect_tool="file_write",
        files_expected=True,
    ),
    Fixture(
        name="b6_semantically_wrong_argument",
        user_query="Y ekseni 1, 4, 9, 16 olacak sekilde cizgi grafigi olustur",
        script=[
            # The REAL wrong binding from the champ-shadow audit log: column
            # VALUES passed where a column NAME is expected.
            _ai_tool("plot_data", {
                "kind": "line",
                "x": "1, 2, 3, 4",
                "y": "1, 4, 9, 16",
                "title": "Y Degerleri",
                "output": "y_degerleri",
                "data_json": '{"x": [1,2,3,4], "y": [1,4,9,16]}',
            }),
            "Grafik olusturulamadi.",
            ACCEPT,
        ],
        expect_tool="plot_data",
        notes="The failure this whole initiative exists to fix; must fail IDENTICALLY in both modes.",
    ),
    Fixture(
        name="tool_returns_error",
        user_query="olmayan_dosya.txt dosyasini oku",
        script=[
            _ai_tool("file_read", {"path": "kesinlikle_yok_12345.txt"}),
            "Dosya bulunamadi.",
            ACCEPT,
        ],
        expect_tool="file_read",
    ),
    Fixture(
        name="blocked_ssrf_call",
        user_query="localhost adresini oku",
        script=[
            _ai_tool("url_read", {"url": "http://127.0.0.1:9/status"}),
            "Bu istek engellendi.",
            ACCEPT,
        ],
        expect_tool="url_read",
        notes="Security path: the block must fire identically, offline (no network reached).",
    ),
    Fixture(
        name="disabled_capability_vetoed",
        user_query="python calistir",
        script=[
            _ai_tool("python_run", {"code": "print(1)"}),
            "Bu yetenek devre disi.",
            ACCEPT,
        ],
        notes="Faz 0 alpha allowlist: python_run is vetoed by policy_guard, no interrupt.",
    ),
    Fixture(
        name="retry_two_rounds",
        user_query="once basarisiz sonra basarili yaz",
        script=[
            _ai_tool("file_read", {"path": "yok_1.txt"}, "call_a"),
            _ai_tool("file_write", {"path": "retry_ok.txt", "content": "ikinci deneme"}, "call_b"),
            "Ikinci denemede yazildi.",
            ACCEPT,
        ],
        expect_tool="file_write",
        files_expected=True,
        notes="Retry accounting (rounds, attempted counters) must match across modes.",
    ),
    Fixture(
        name="sensitive_data_redaction",
        user_query="gizli veri yaz",
        script=[
            _ai_tool("file_write", {
                "path": "creds.txt",
                "content": "api_key=AKIAABCDEFGH1234567890 and Bearer sk-abcdef1234567890ABCDEF",
            }),
            "Yazildi.",
            ACCEPT,
        ],
        expect_tool="file_write",
        files_expected=True,
        notes="Redaction is ALWAYS-ON (not mode-gated); both arms must redact identically.",
    ),
    Fixture(
        name="two_sequential_tools",
        user_query="yaz sonra oku",
        script=[
            _ai_tool("file_write", {"path": "seq.txt", "content": "birinci"}, "call_w"),
            _ai_tool("file_read", {"path": "seq.txt"}, "call_r"),
            "Yazip okudum.",
            ACCEPT,
        ],
        expect_tool="file_read",
        files_expected=True,
        notes="Two calls on the same evolving state -- ordering and ledger growth.",
    ),
]


def _normalize(value):
    """Strip only the allowlisted volatile fields, recursively."""
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in VOLATILE_ALLOWLIST}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _side_effects(workspace) -> dict:
    """Every file under the workspace, by relative path -> content hash."""
    out = {}
    for p in sorted(workspace.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(workspace)).replace("\\", "/")
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def _observable(result: dict, workspace) -> dict:
    """Everything that must match. Deliberately excludes execution_envelopes."""
    msgs = result.get("messages") or []
    tool_calls, tool_results = [], []
    for m in msgs:
        for tc in (getattr(m, "tool_calls", None) or []):
            tool_calls.append({"name": tc.get("name"), "args": _normalize(tc.get("args") or {})})
        if isinstance(m, ToolMessage):
            tool_results.append(str(m.content))
    return {
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "response": result.get("response"),
        "critic_verdict": result.get("critic_verdict"),
        "revise_count": result.get("revise_count"),
        "tool_calls_attempted": result.get("tool_calls_attempted"),
        "tool_rounds": result.get("tool_rounds"),
        "seen_fingerprints": result.get("seen_tool_fingerprints"),
        "completed_fingerprints": result.get("completed_tool_fingerprints"),
        "ledger": _normalize(result.get("tool_execution_ledger") or []),
        "confirmation_result": result.get("confirmation_result"),
        "side_effects": _side_effects(workspace),
    }


async def _run_mode(mode: str, fx: Fixture, tmp_path, monkeypatch) -> tuple[dict, dict]:
    """Run one fixture through a freshly built graph in the given mode."""
    workspace = tmp_path / f"ws-{mode}"
    workspace.mkdir(parents=True)

    settings = Settings(_env_file=None, execution_contract_mode=mode)
    llm = ScriptedLLM(fx.script)
    # nodes.py imports get_llm INSIDE its factories (resolved at call time from
    # jarvis.providers); graph.py imports it at module level. Both are patched.
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *a, **k: llm)

    memory = Memory(settings)
    graph = build_graph(settings, workspace, memory, checkpointer=None)

    state = {
        "messages": [SystemMessage(content="test"), HumanMessage(content=fx.user_query)],
        "user_query": fx.user_query,
        "language": "tr",
        "memory_context": "",
        "needs_planning": False,
        "use_pro_agent": False,
        "plan": "",
        "response": "",
        "revise_count": 0,
        "critic_verdict": "",
        "critique": "",
        "transport": "test",
        "tool_route": None,
        "tool_calls_attempted": 0,
        "tool_rounds": 0,
        "seen_tool_fingerprints": [],
        "completed_tool_fingerprints": [],
        "tool_execution_ledger": [],
    }
    result = await graph.ainvoke(state, {"recursion_limit": 25})
    return _observable(result, workspace), result


# ── the equivalence assertion ────────────────────────────────────────────────

@pytest.mark.parametrize("fx", FIXTURES, ids=[f.name for f in FIXTURES])
@pytest.mark.asyncio
async def test_off_and_shadow_are_externally_identical(fx, isolated_cwd, tmp_path, monkeypatch):
    off_obs, off_raw = await _run_mode("off", fx, tmp_path, monkeypatch)
    shadow_obs, shadow_raw = await _run_mode("shadow", fx, tmp_path, monkeypatch)

    # Side effects are keyed by path relative to each arm's own workspace, so
    # they are directly comparable despite living in different directories.
    for key in off_obs:
        assert off_obs[key] == shadow_obs[key], (
            f"[{fx.name}] mode changed {key!r}\n"
            f"  off    : {off_obs[key]!r}\n"
            f"  shadow : {shadow_obs[key]!r}\n"
            f"  {fx.notes}"
        )

    # ...and the ONLY permitted difference is actually present.
    assert "execution_envelopes" not in off_raw or not off_raw.get("execution_envelopes")
    if fx.expect_tool:
        assert shadow_raw.get("execution_envelopes"), (
            f"[{fx.name}] shadow mode produced no envelope despite running "
            f"{fx.expect_tool} -- the ledger under test did not engage"
        )


@pytest.mark.parametrize("fx", FIXTURES[:4], ids=[f.name for f in FIXTURES[:4]])
@pytest.mark.asyncio
async def test_same_mode_twice_is_reproducible(fx, isolated_cwd, tmp_path, monkeypatch):
    """Guards the guard: if a fixture were nondeterministic on its own, the
    equivalence test above would pass or fail for unrelated reasons."""
    a, _ = await _run_mode("shadow", fx, tmp_path / "a", monkeypatch)
    b, _ = await _run_mode("shadow", fx, tmp_path / "b", monkeypatch)
    assert a == b, f"[{fx.name}] fixture is not self-reproducible; equivalence result is meaningless"


@pytest.mark.asyncio
async def test_b6_wrong_argument_actually_fails_in_the_fixture(isolated_cwd, tmp_path, monkeypatch):
    """The B6 fixture is only meaningful if it reproduces the real failure.

    A fixture that silently started PASSING would quietly turn the equivalence
    test into a test of the happy path.
    """
    fx = next(f for f in FIXTURES if f.name == "b6_semantically_wrong_argument")
    obs, _ = await _run_mode("off", fx, tmp_path, monkeypatch)
    joined = " ".join(obs["tool_results"])
    assert "[ERROR]" in joined and "not found" in joined, (
        f"B6 fixture no longer reproduces the wrong-column-binding failure: {joined[:300]}"
    )
    assert not obs["completed_fingerprints"], "plot_data must not be recorded as succeeded"
