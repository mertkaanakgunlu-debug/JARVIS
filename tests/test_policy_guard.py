"""policy_guard.py — the safety kernel gating every tool call (Faz 4).

Single choke point: risk classification, the per-action read/write downgrade
for the four mixed-risk external_api tools (BUG-6), and the kill-switch veto.
Everything else in the safety chain (audit_log, the confirmation node, MCP
tools) trusts this module's PolicyDecision, so its correctness matters more
than most of this codebase.
"""
from __future__ import annotations

from jarvis import kill_switch, policy_guard


def test_read_only_tool_is_low_risk_no_confirm():
    d = policy_guard.evaluate("file_read", {}, settings=None)
    assert d.risk_level == 1
    assert d.requires_confirmation is False
    assert d.allowed is True


def test_shell_run_is_high_risk_requires_confirm():
    d = policy_guard.evaluate("shell_run", {"command": "dir"}, settings=None)
    assert d.risk_level == 3
    assert d.requires_confirmation is True
    assert d.allowed is True  # allowed to *ask*; kill switch is a separate axis


def test_python_run_matches_shell_run_risk():
    # BUG-1: python_run must not sit at a lower gate than shell_run -- arbitrary
    # unsandboxed Python execution is at least as powerful as a shell command.
    d = policy_guard.evaluate("python_run", {"script_path": "x.py"}, settings=None)
    assert d.risk_level == 3
    assert d.requires_confirmation is True


def test_unregistered_tool_fails_safe():
    """An unregistered tool name must default to confirm-required, not silently allowed."""
    d = policy_guard.evaluate("totally_made_up_tool_xyz", {}, settings=None)
    assert d.risk_level == 3
    assert d.requires_confirmation is True
    assert d.allowed is True  # still "allowed to ask", not a hard veto
    assert "no ToolSpec registered" in d.reason


class TestPerActionDowngrade:
    """BUG-6: the four mixed-risk external_api tools must downgrade their
    documented read actions to L1/no-confirm, while every other action on the
    same tool stays at the tool's full risk level."""

    def test_calendar_list_is_read_only(self):
        d = policy_guard.evaluate("google_calendar", {"action": "list"}, settings=None)
        assert d.risk_level == 1
        assert d.requires_confirmation is False

    def test_calendar_search_is_read_only(self):
        d = policy_guard.evaluate("google_calendar", {"action": "search"}, settings=None)
        assert d.requires_confirmation is False

    def test_calendar_create_is_not_downgraded(self):
        d = policy_guard.evaluate("google_calendar", {"action": "create"}, settings=None)
        assert d.risk_level == 3
        assert d.requires_confirmation is True

    def test_calendar_delete_is_not_downgraded(self):
        d = policy_guard.evaluate("google_calendar", {"action": "delete"}, settings=None)
        assert d.requires_confirmation is True

    def test_gmail_read_actions_downgraded(self):
        for action in ("list_unread", "search", "read"):
            d = policy_guard.evaluate("gmail", {"action": action}, settings=None)
            assert d.requires_confirmation is False, action

    def test_gmail_send_not_downgraded(self):
        d = policy_guard.evaluate("gmail", {"action": "send"}, settings=None)
        assert d.requires_confirmation is True

    def test_drive_download_downgraded_but_upload_is_not(self):
        d_read = policy_guard.evaluate("google_drive", {"action": "download"}, settings=None)
        assert d_read.requires_confirmation is False
        d_write = policy_guard.evaluate("google_drive", {"action": "upload"}, settings=None)
        assert d_write.requires_confirmation is True

    def test_itu_mail_read_vs_send(self):
        d_read = policy_guard.evaluate("itu_mail", {"action": "list_unread"}, settings=None)
        assert d_read.requires_confirmation is False
        d_write = policy_guard.evaluate("itu_mail", {"action": "send"}, settings=None)
        assert d_write.requires_confirmation is True

    def test_downgrade_is_scoped_to_its_own_tool(self):
        # "search" is a read action for gmail, but google_calendar's action
        # namespace is independent -- a tool with no _READ_ACTIONS entry at
        # all must never accidentally inherit another tool's downgrade list.
        d = policy_guard.evaluate("spotify", {"action": "search"}, settings=None)
        assert d.risk_level == 2  # spotify's own ToolSpec risk level, untouched
        assert d.requires_confirmation is False  # spotify itself is no-confirm by spec


class TestKillSwitch:
    """Kill switch vetoes L3+requires_confirmation calls only -- an emergency
    stop for JARVIS acting on the outside world, not a full halt on local
    writes (see policy_guard.py's _KILL_SWITCH_RISK_THRESHOLD docstring)."""

    def test_tripped_switch_vetoes_l3_action(self, isolated_cwd):
        kill_switch.disable("test: pausing external actions")
        d = policy_guard.evaluate("shell_run", {"command": "dir"}, settings=None)
        assert d.allowed is False
        assert "kill switch" in d.reason.lower()

    def test_tripped_switch_does_not_veto_l2_local_write(self, isolated_cwd):
        kill_switch.disable("test: pausing external actions")
        d = policy_guard.evaluate("file_write", {}, settings=None)
        assert d.allowed is True  # L2, requires_confirmation=False -- below the veto threshold

    def test_tripped_switch_does_not_veto_read_action_on_gated_tool(self, isolated_cwd):
        # google_calendar is L3 by default, but "list" is downgraded to L1
        # *before* the kill-switch check runs -- a tripped switch must not
        # block reading your own calendar.
        kill_switch.disable("test: pausing external actions")
        d = policy_guard.evaluate("google_calendar", {"action": "list"}, settings=None)
        assert d.allowed is True

    def test_re_enabled_switch_stops_vetoing(self, isolated_cwd):
        kill_switch.disable("test")
        assert policy_guard.evaluate("shell_run", {}, settings=None).allowed is False
        kill_switch.enable()
        assert policy_guard.evaluate("shell_run", {}, settings=None).allowed is True


class TestDescribeCall:
    """describe_call() feeds confirmation prompts (CLI text + TTS) and the
    audit log -- must never be empty/None for a call a user might approve."""

    def test_shell_run_names_the_command(self):
        assert "ls -la" in policy_guard.describe_call("shell_run", {"command": "ls -la"})

    def test_known_action_uses_plain_verb(self):
        desc = policy_guard.describe_call("gmail", {"action": "send", "to": "x@y.com"})
        assert "send an email" in desc
        assert "to=x@y.com" in desc

    def test_unknown_tool_falls_back_to_bare_name(self):
        desc = policy_guard.describe_call("some_mcp_tool", {})
        assert "some_mcp_tool" in desc
