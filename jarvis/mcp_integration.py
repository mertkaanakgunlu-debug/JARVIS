"""Faz 5: MCP client layer.

Connects to configured external MCP servers, converts their tools into
LangChain tool objects, and synthesizes a ToolSpec per discovered tool with
a fail-closed default (L3 + requires_confirmation=True) via
tool_registry.register_dynamic_spec() -- so policy_guard, audit_log, and the
async scheduler cover MCP tools the exact same way they cover the ~34 native
@tool wrappers in jarvis/graph/tools.py, with zero changes to any of them
(they all only ever call get_spec()/read TOOL_SPECS). Dual-layer design per
ROADMAP.md Faz 5: this module is purely additive, the native tools are
untouched.

Event-loop note (the actual hard part of this phase): MCP's stdio transport
keeps ONE subprocess alive for the life of a *session* -- required for
stateful servers like browser automation, where a later browser_click must
see the page a prior browser_navigate opened. Confirmed live: the default
`MultiServerMCPClient.get_tools()` convenience method creates a *fresh*
session (and therefore a fresh, blank browser) per tool call, which silently
breaks any multi-step browse flow -- this module always uses the persistent
`client.session()` + `load_mcp_tools(session)` pattern instead, held open by
an AsyncExitStack for the process's lifetime.

That persistent session's stdio streams are bound to whichever asyncio loop
opened them and are not safely usable from a different loop -- the same
underlying constraint jarvis/graph/graph.py's checkpointer docstring already
explains for SqliteSaver vs. AsyncSqliteSaver. JarvisAgent therefore never
connects lazily from whichever caller happens to chat() first (which could
be a TaskExecutor background job's own short-lived asyncio.run()) -- it
connects exactly once, explicitly, from the real long-lived loop
(cli.py's asyncio.run(_run_loop/_run_voice_loop), or api.py's uvicorn loop
via lifespan()) before any user turn or background task can occur. See
JarvisAgent.connect_mcp_tools()/close_mcp_tools() in agent.py.

Windows gotcha (confirmed live on this machine, not assumed from docs): npx
is npx.cmd, a batch shim, not a real executable -- spawning it directly via
asyncio's subprocess APIs raises WinError 2 (file not found). Every npx-based
server config in this module goes through `cmd /c npx ...`, never bare `npx`.
"""

from __future__ import annotations

import logging
import sys
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from jarvis.tool_registry import ToolSpec, register_dynamic_spec

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from jarvis.config import Settings

logger = logging.getLogger("jarvis.mcp")

# Playwright MCP tool names confirmed (live, 2026-07-15 against @playwright/mcp
# @latest, 24-tool core set) to be pure inspection or same-tab navigation with
# no lasting external effect -- everything else that server exposes (click,
# type, fill_form, press_key, select_option, file_upload, drag, drop, hover,
# handle_dialog, evaluate, run_code_unsafe, ...) falls through to the
# fail-closed default below. This mirrors policy_guard._READ_ACTIONS' existing
# per-action override pattern for the four mixed-risk Google/ITU tools --
# same idea, just keyed by MCP tool name instead of an `action` argument.
_READ_ONLY_TOOLS = frozenset({
    "browser_snapshot", "browser_take_screenshot", "browser_console_messages",
    "browser_network_requests", "browser_network_request", "browser_find",
})
_LOCAL_NAV_TOOLS = frozenset({
    "browser_navigate", "browser_navigate_back", "browser_wait_for",
    "browser_resize", "browser_close", "browser_tabs",
})

# GPT-5.6 review remediation, Faz 4 (P1): browser_navigate previously had no
# SSRF guard at all -- the browser could be pointed at localhost/private/link
# -local/cloud-metadata addresses via any URL the model was told to visit
# (including one suggested by a page's own content -- the same prompt-
# injection vector jarvis/tools/webfetch.py's url_read guards against).
# Tools that take a navigable "url" argument get the same shared check.
_URL_ARG_TOOLS = frozenset({"browser_navigate"})


# GPT-5.6 review remediation, Faz 4: was "@latest" -- an unpinned dependency
# that npx re-resolves on every launch means a server-side release (bug,
# behavior change, or compromise) reaches this project with zero review and
# no diff to look at. Pinned to the current stable release as of 2026-07-15
# (confirmed via `npm view @playwright/mcp version`); bump deliberately.
_PLAYWRIGHT_MCP_VERSION = "0.0.78"


def _playwright_server_config(settings: "Settings") -> dict:
    args = ["-y", f"@playwright/mcp@{_PLAYWRIGHT_MCP_VERSION}"]
    if settings.mcp_playwright_headless:
        args.append("--headless")
    args.append("--isolated")  # in-memory profile, nothing persisted to disk between runs
    if sys.platform == "win32":
        # npx is npx.cmd (a batch shim) on Windows -- spawning it directly
        # raises WinError 2. Confirmed live; see module docstring.
        return {"command": "cmd", "args": ["/c", "npx", *args], "transport": "stdio"}
    return {"command": "npx", "args": args, "transport": "stdio"}


def _effective_server_config(settings: "Settings") -> dict[str, dict]:
    """Merge the dedicated Playwright convenience flags with the generic
    settings.mcp_servers escape hatch into one MultiServerMCPClient config."""
    servers = dict(settings.mcp_servers or {})
    if settings.mcp_playwright_enabled and "playwright" not in servers:
        servers["playwright"] = _playwright_server_config(settings)
    return servers


async def _ssrf_guard_interceptor(request, handler):
    """langchain-mcp-adapters ToolCallInterceptor: short-circuits a
    _URL_ARG_TOOLS call whose "url" argument resolves to a blocked address
    (jarvis.url_policy.is_blocked_url — same guard as url_read) instead of
    letting it reach the real MCP server. Returned as a CallToolResult with
    isError=True so it surfaces to the agent as a normal failed-tool-call
    message (self-correctable), not a crash."""
    if request.name in _URL_ARG_TOOLS:
        url = str((request.args or {}).get("url", "") or "")
        if url:
            from jarvis.url_policy import is_blocked_url

            blocked, why = is_blocked_url(url)
            if blocked:
                from mcp.types import CallToolResult, TextContent

                logger.warning("Blocked MCP %s to %r: %s", request.name, url, why)
                return CallToolResult(
                    isError=True,
                    content=[TextContent(
                        type="text",
                        text=f"[BLOCKED: {why}] Refusing to navigate to: {url}",
                    )],
                )
    return await handler(request)


def _classify(tool_name: str) -> tuple[int, bool, str]:
    """Fail-closed by design (ROADMAP.md Faz 5 risk note: no MCP tool may
    bypass the gate) -- only names explicitly known to be read-only or
    inconsequential navigation get anything less than L3+confirm."""
    if tool_name in _READ_ONLY_TOOLS:
        return 1, False, "external_read"
    if tool_name in _LOCAL_NAV_TOOLS:
        return 2, False, "external_read"
    return 3, True, "external_write"


class McpToolManager:
    """Owns the live MCP connections for one JarvisAgent's process lifetime.

    Connect once (idempotent — see connect()), fail soft on any error and
    continue with zero MCP tools (same philosophy as Memory's Ollama-EF
    reachability probe and providers.get_llm()'s _safe_construct — an
    optional integration being unreachable must never block startup or a
    turn), and close cleanly on shutdown so the launched npx/browser process
    tree doesn't linger after JARVIS exits.
    """

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._tools: list["BaseTool"] = []
        self._attempted = False

    @property
    def tools(self) -> list["BaseTool"]:
        return self._tools

    async def connect(self, settings: "Settings") -> None:
        if self._attempted:
            return
        self._attempted = True  # once per process -- a failed server is not retried every turn

        server_config = _effective_server_config(settings)
        if not server_config:
            return

        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            from langchain_mcp_adapters.tools import load_mcp_tools
        except ImportError:
            logger.warning("langchain-mcp-adapters not installed -- MCP tools unavailable")
            return

        stack = AsyncExitStack()
        tools: list["BaseTool"] = []
        try:
            client = MultiServerMCPClient(server_config)
            for name in server_config:
                try:
                    session = await stack.enter_async_context(client.session(name))
                    server_tools = await load_mcp_tools(
                        session, tool_interceptors=[_ssrf_guard_interceptor]
                    )
                except Exception as e:
                    logger.warning("MCP server %r unreachable, skipping: %s", name, e)
                    continue

                for t in server_tools:
                    risk, confirm, effect = _classify(t.name)
                    register_dynamic_spec(ToolSpec(
                        t.name, "mcp", risk, confirm, effect,
                        timeout_seconds=60, supports_background=False,
                        description=f"[MCP:{name}] {(t.description or '').strip()[:80]}",
                    ))
                tools.extend(server_tools)
                logger.info("MCP server %r: %d tools loaded", name, len(server_tools))
        except Exception as e:
            logger.warning("MCP connect failed, continuing without MCP tools: %s", e)
            await stack.aclose()
            return

        self._stack = stack
        self._tools = tools

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as e:
                logger.warning("MCP shutdown cleanup failed (non-fatal): %s", e)
            self._stack = None
        self._tools = []
        self._attempted = False
