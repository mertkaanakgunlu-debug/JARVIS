"""Confidence-based calendar confirmation — Post-MVP Faz 2, plan item 5.

The owner's decision was "takvim/todo serbest -- tarih hatasi kapandiktan
sonra": stop interrupting for routine calendar entries once dates can be
trusted. The plan deliberately does not cash that in as a blanket relaxation,
because "JARVIS may create calendar events without asking" and "JARVIS may
create calendar events it is not sure about without asking" are different
promises.

Half of this file is the feature. The other half — TestNoSafetyMechanismIsBypassed
— is the part that matters, because this is the first change in the codebase
that can produce risk_level=3 with requires_confirmation=False. Every existing
safety mechanism was written when those two were equivalent, so each one is
re-asserted here against a call that WOULD auto-approve:

  * the kill switch          (a REAL bypass this change opened; the veto was
                              keyed on requires_confirmation and had to be
                              re-keyed on risk_level alone)
  * --profile test           (keys on side_effect_type, unaffected — proven,
                              not assumed)
  * background/proactive     (never reaches the downgrade at all, because
    turns                     `interactive` is derived from the transport —
                              see the test for why the read-only gate is NOT
                              what protects this, contrary to first guess)
  * the audit log            (risk_level is untouched, so the row is still
                              written, now as auto_approved)
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from jarvis import kill_switch, policy_guard
from jarvis.config import Settings
from jarvis.graph.nodes import make_confirmation_node
from jarvis.nlu.temporal import AUTO_THRESHOLD

CONFIDENT = {"action": "create", "title": "Baran ile toplantı", "date": "yarın", "time": "15:00"}

# The request CONFIDENT is a faithful reading of. Used by the transport tests
# below so the only thing that can force a confirmation there is the transport:
# an utterance that contradicted the args would make the gate fire for the
# wrong reason and the test would pass while proving nothing.
UTTERANCE = "Yarın saat 15:00'te Baran'la toplantı ekle"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **overrides)


def _evaluate(args, *, interactive=True, utterance="", **overrides):
    return policy_guard.evaluate(
        "google_calendar", args, _settings(**overrides),
        interactive=interactive, utterance=utterance,
    )


class TestConfidenceScoring:
    def test_a_fully_specified_create_reaches_the_auto_band(self):
        confidence, _ = policy_guard.calendar_confidence(CONFIDENT)
        assert confidence >= AUTO_THRESHOLD

    @pytest.mark.parametrize("args,why", [
        ({**CONFIDENT, "date": "pazartesi"}, "a bare weekday could be this week or next"),
        ({**CONFIDENT, "time": "4"}, "a bare small hour could be either half of the day"),
        ({**CONFIDENT, "date": "haftaya"}, "a week is not a day"),
        ({**CONFIDENT, "date": "zzz"}, "unparseable"),
        ({**CONFIDENT, "title": ""}, "no title"),
        ({**CONFIDENT, "title": "takvime ekle"}, "the title is an instruction"),
        ({**CONFIDENT, "title": "Müsait miyim?"}, "the title is a question"),
    ])
    def test_any_ambiguous_field_drops_the_whole_call_below_auto(self, args, why):
        confidence, reason = policy_guard.calendar_confidence(args)
        assert confidence < AUTO_THRESHOLD, why
        assert reason, "a below-threshold score must carry a reason to show the user"

    def test_confidence_is_the_minimum_so_good_fields_cannot_mask_a_bad_one(self):
        perfect_date_vague_time = {**CONFIDENT, "date": "2026-08-15", "time": "4"}
        assert policy_guard.calendar_confidence(perfect_date_vague_time)[0] < AUTO_THRESHOLD

    def test_an_all_day_event_is_not_penalised_for_having_no_time(self):
        """An empty time is a deliberate all-day event, not missing information."""
        assert policy_guard.calendar_confidence({**CONFIDENT, "time": ""})[0] >= AUTO_THRESHOLD

    def test_the_model_cannot_supply_its_own_confidence(self):
        """If confidence were an argument, the model could set it to 1.0 and
        approve its own actions. The gate has to derive it or it is not a gate."""
        cheating = {**CONFIDENT, "date": "haftaya", "confidence": 1.0, "certainty": "high"}
        assert policy_guard.calendar_confidence(cheating)[0] < AUTO_THRESHOLD

    def test_confidence_does_not_move_with_the_clock(self):
        """policy_guard must stay a pure function of (tool, args, settings):
        two evaluations of one call, in two graph nodes, must never disagree."""
        from jarvis import clock
        from jarvis.clock import FrozenClock

        scores = []
        for offset in (0, 1, 47, 180, 366):
            with clock.use_clock(FrozenClock.at("2026-01-31 23:59").advance(days=offset)):
                scores.append(policy_guard.calendar_confidence(CONFIDENT)[0])
        assert len(set(scores)) == 1


class TestTheUsersOwnWordsAreScoredToo:
    """Closes a hole found by LIVE measurement, not by reading the code.

    Asked *"Pazartesi saat 4'te spor salonu diye takvime bir şey ekle"*, real
    qwen3:8b did not pass `date="pazartesi"` through as the tool description
    asks. It resolved the weekday itself — to a Saturday — and passed an ISO
    date. Scored on arguments alone that is a 1.00, and an event on the wrong
    day would have been created with no prompt. A model that does the
    resolution itself launders an ambiguous request into a confident-looking
    one, so the gate has to read what the user actually said.
    """

    SAID = "Pazartesi saat 4'te spor salonu diye takvime bir şey ekle."

    def test_the_exact_measured_failure_no_longer_auto_approves(self, isolated_cwd):
        args = {"action": "create", "title": "Spor salonu",
                "date": "2026-08-01", "time": "16:00"}  # 2026-08-01 is a SATURDAY
        confidence, reason = policy_guard.calendar_confidence(args, self.SAID)
        assert confidence == 0.0
        assert "pazartesi" in reason and "cumartesi" in reason
        assert _evaluate(args, utterance=self.SAID).requires_confirmation is True

    def test_an_ambiguous_request_stays_ambiguous_however_precise_the_args_look(self, isolated_cwd):
        """Even with the RIGHT Monday, "Pazartesi" alone does not say which week."""
        args = {"action": "create", "title": "Spor salonu",
                "date": "2026-08-03", "time": "18:00"}  # a real Monday
        confidence, reason = policy_guard.calendar_confidence(args, self.SAID)
        assert confidence < AUTO_THRESHOLD
        assert "which week" in reason

    def test_a_bare_hour_in_the_request_is_caught_even_when_the_args_say_16_00(self, isolated_cwd):
        said = "Gelecek pazartesi saat 4'te spor salonu ekle."
        args = {"action": "create", "title": "Spor salonu", "date": "2026-08-03", "time": "16:00"}
        confidence, reason = policy_guard.calendar_confidence(args, said)
        assert confidence < AUTO_THRESHOLD
        assert "saat 4" in reason

    @pytest.mark.parametrize("said,args", [
        ("Gelecek pazartesi 18:00'de spor salonu ekle.",
         {"date": "2026-08-03", "time": "18:00", "title": "Spor salonu"}),
        ("Yarın öğlen saat 3'e Baran'la toplantı ekle.",
         {"date": "yarın", "time": "15:00", "title": "Baran ile toplantı"}),
        ("Yarın öğlen saat 3'e Baran'la toplantı ekle.",
         {"date": "2026-08-02", "time": "15:00", "title": "Baran ile toplantı"}),
        ("15 Ağustos 2026 saat 14:00'te diş hekimi randevusu ekle.",
         {"date": "2026-08-15", "time": "14:00", "title": "Diş hekimi randevusu"}),
    ])
    def test_an_unambiguous_request_still_runs_without_asking(self, isolated_cwd, said, args):
        """The feature has to survive the fix, or it is not a feature."""
        confidence, _ = policy_guard.calendar_confidence({"action": "create", **args}, said)
        assert confidence >= AUTO_THRESHOLD

    def test_a_weekday_that_is_part_of_a_NAME_does_not_manufacture_a_conflict(self, isolated_cwd):
        """"Cuma raporu" is a Friday REPORT. The sentence carries its own date
        ("yarın"), which is the signal that the weekday word is not the date —
        without that guard this check would fire on every event whose title
        happens to contain a weekday."""
        said = "Cuma raporu için yarın 15:00'te toplantı ayarla."
        args = {"action": "create", "title": "Cuma raporu toplantısı",
                "date": "2026-08-02", "time": "15:00"}
        confidence, _ = policy_guard.calendar_confidence(args, said)
        assert confidence >= AUTO_THRESHOLD

    def test_omitting_the_utterance_is_safe_and_changes_nothing(self, isolated_cwd):
        """It can only ever LOWER confidence, so a caller that has no user text
        keeps the pre-existing args-only behaviour."""
        assert policy_guard.calendar_confidence(CONFIDENT, "")[0] >= AUTO_THRESHOLD
        assert policy_guard.calendar_confidence(CONFIDENT)[0] >= AUTO_THRESHOLD

    def test_the_utterance_can_never_RAISE_confidence(self, isolated_cwd):
        """Structural: a model could otherwise pad the request with reassuring
        words to buy itself an approval."""
        vague = {"action": "create", "title": "X", "date": "haftaya", "time": "4"}
        for said in ("", "kesinlikle yarın 15:00", "acil, çok net, hemen ekle", CONFIDENT["title"]):
            with_text = policy_guard.calendar_confidence(vague, said)[0]
            without = policy_guard.calendar_confidence(vague, "")[0]
            assert with_text <= without

    @pytest.mark.asyncio
    async def test_both_graph_nodes_reach_the_same_verdict_on_a_conflicting_request(
        self, isolated_cwd, monkeypatch,
    ):
        """prepare_execution_node and confirmation_node call evaluate()
        independently. Agreement depends on both deriving `utterance` and
        `interactive` from state the same way — asserted behaviourally, since
        a source-text check would pass on two nodes that both read the key and
        then used it differently."""
        from langgraph.errors import GraphInterrupt

        from jarvis.graph.nodes import make_prepare_execution_node

        conflicting = {"action": "create", "title": "Spor salonu",
                       "date": "2026-08-01", "time": "16:00"}
        state = {
            "messages": [AIMessage(content="", tool_calls=[
                {"name": "google_calendar", "args": conflicting, "id": "c1", "type": "tool_call"}])],
            "transport": "cli-text",
            "user_query": self.SAID,
        }

        prepared = await make_prepare_execution_node(_settings())(state)
        request = prepared["execution_requests"][0]["request"]
        assert request["requires_confirmation"] is True, "prepare_execution disagreed"

        reached = []
        monkeypatch.setattr("langgraph.types.interrupt",
                            lambda payload: (reached.append(payload), (_ for _ in ()).throw(GraphInterrupt()))[0])
        with pytest.raises(GraphInterrupt):
            await make_confirmation_node(_settings())({**state, **prepared})
        assert reached, "confirmation_node disagreed"

    @pytest.mark.asyncio
    async def test_a_resumed_checkpoint_with_no_user_query_still_reads_the_request(
        self, isolated_cwd, monkeypatch,
    ):
        """`user_query` is populated on the normal entry path but not on every
        one — a resumed checkpoint can lack it, which _is_turkish() in the same
        module already had to work around. Without a fallback, that path would
        silently give up this protection."""
        from langchain_core.messages import HumanMessage
        from langgraph.errors import GraphInterrupt

        conflicting = {"action": "create", "title": "Spor salonu",
                       "date": "2026-08-01", "time": "16:00"}
        state = {  # note: NO "user_query" key at all
            "messages": [
                HumanMessage(content=self.SAID),
                AIMessage(content="", tool_calls=[
                    {"name": "google_calendar", "args": conflicting, "id": "c1", "type": "tool_call"}]),
            ],
            "transport": "cli-text",
        }
        reached = []
        monkeypatch.setattr("langgraph.types.interrupt",
                            lambda p: (reached.append(p), (_ for _ in ()).throw(GraphInterrupt()))[0])
        with pytest.raises(GraphInterrupt):
            await make_confirmation_node(_settings())(state)
        assert reached, "a resumed checkpoint silently lost the utterance check"

    @pytest.mark.asyncio
    async def test_the_same_nodes_both_auto_approve_an_unambiguous_request(
        self, isolated_cwd,
    ):
        """The contrast — otherwise the previous test would pass on a gate that
        simply always confirms."""
        from jarvis.graph.nodes import make_prepare_execution_node

        said = "Yarın öğlen saat 3'e Baran'la toplantı ekle."
        state = {
            "messages": [AIMessage(content="", tool_calls=[
                {"name": "google_calendar", "args": CONFIDENT, "id": "c1", "type": "tool_call"}])],
            "transport": "cli-text",
            "user_query": said,
        }
        prepared = await make_prepare_execution_node(_settings())(state)
        assert prepared["execution_requests"][0]["request"]["requires_confirmation"] is False

        result = await make_confirmation_node(_settings())({**state, **prepared})
        assert result["confirmation_result"] == "approved"


class TestWhichActionsAreEligible:
    def test_a_confident_single_create_does_not_interrupt(self, isolated_cwd):
        assert _evaluate(CONFIDENT).requires_confirmation is False

    @pytest.mark.parametrize("action,args", [
        ("batch_create", {"events_json": '[{"title":"a","date":"yarın","time":"15:00"}]'}),
        ("update", {"event_id": "e1", "title": "Toplantı", "date": "yarın", "time": "15:00"}),
        ("delete", {"event_id": "e1"}),
    ])
    def test_every_other_write_action_still_asks_however_confident(self, isolated_cwd, action, args):
        """batch_create writes N events from one approval, update silently
        rewrites something that already exists, and delete is the one genuinely
        hard-to-reverse action in the tool. A single create is the only one a
        user can undo by looking at their calendar."""
        assert _evaluate({"action": action, **args}).requires_confirmation is True

    def test_an_ambiguous_create_still_asks(self, isolated_cwd):
        decision = _evaluate({**CONFIDENT, "date": "pazartesi"})
        assert decision.requires_confirmation is True
        assert "this week or next" in decision.reason

    def test_read_actions_are_unaffected(self, isolated_cwd):
        for action in ("list", "search"):
            decision = _evaluate({"action": action, "query": "x"})
            assert decision.requires_confirmation is False
            assert decision.risk_level == 1


class TestDefaultsAreFailClosed:
    def test_a_caller_that_does_not_pass_interactive_gets_the_old_behaviour(self, isolated_cwd):
        """The workflow engine and any future direct dispatcher call
        evaluate() without this keyword; they must be untouched by Faz 2."""
        decision = policy_guard.evaluate("google_calendar", CONFIDENT, _settings())
        assert decision.requires_confirmation is True

    def test_the_workflow_engine_call_site_still_confirms(self, isolated_cwd):
        """Pinned against the real call site rather than a paraphrase of it."""
        import inspect

        from jarvis.execution import workflow_engine

        source = inspect.getsource(workflow_engine)
        assert "policy_guard.evaluate(step.capability, step.args, self._settings)" in source, (
            "the workflow engine's evaluate() call changed -- re-check whether it "
            "should now be passing interactive=, and update this test deliberately"
        )

    def test_the_feature_can_be_turned_off_completely(self, isolated_cwd):
        decision = _evaluate(CONFIDENT, calendar_autonomy_enabled=False)
        assert decision.requires_confirmation is True

    @pytest.mark.parametrize("tool,args", [
        ("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}),
        ("gmail", {"action": "reply", "message_id": "m1", "body": "b"}),
        ("gmail", {"action": "trash", "message_id": "m1"}),
        ("itu_mail", {"action": "send", "to": "a@b.c"}),
        ("google_drive", {"action": "upload", "name": "x"}),
        ("google_drive", {"action": "share", "file_id": "f1"}),
        ("google_drive", {"action": "delete", "file_id": "f1"}),
        ("shell_run", {"command": "dir"}),
        ("python_run", {"script_path": "x.py"}),
    ])
    def test_other_external_write_tools_are_untouched(self, isolated_cwd, tool, args):
        """Mail is always confirmed — the owner's decision table says so
        explicitly ("Mail hep onaylı"), and nothing in Faz 2 may quietly widen
        past calendar."""
        d = policy_guard.evaluate(tool, args, _settings(), interactive=True)
        assert d.requires_confirmation is True or d.allowed is False, tool

    @pytest.mark.parametrize("tool", ["shell_run", "python_run", "workflow_start"])
    def test_a_schema_less_tool_cannot_borrow_the_calendar_downgrade(self, isolated_cwd, tool):
        """The reason the tool-name check is load-bearing rather than tidy.

        gmail/drive/itu_mail can never carry action="create" — their args
        schemas are Literal-typed and would reject it. shell_run, python_run
        and workflow_start have NO args schema at all, so a model is free to
        attach `action="create"` plus a confident date/title to a shell
        command. Only the tool-name check stops that from auto-approving.

        Asserted against _resolve_risk directly, because evaluate() would
        short-circuit on the alpha-disabled veto for two of these three and
        the test would pass without ever reaching the condition under test.
        """
        from jarvis.tool_registry import get_spec

        smuggled = {"command": "rm -rf /", "script_path": "x.py", **CONFIDENT}
        _risk, requires_confirmation, _side_effect, _note = policy_guard._resolve_risk(
            tool, smuggled, get_spec(tool), _settings(), interactive=True,
        )
        assert requires_confirmation is True, (
            f"{tool} borrowed the calendar confidence downgrade"
        )

    def test_no_mail_or_drive_action_name_is_in_the_eligible_set(self):
        """A second, independent guard on the same promise. If the eligible
        set ever grew a name that gmail/drive also uses, the tool_name check
        would be the only thing standing between a confident model and an
        unconfirmed email — one condition is not enough for that."""
        eligible = policy_guard._AUTONOMOUS_CALENDAR_ACTIONS
        assert eligible == {"create"}
        mail_and_drive_actions = {
            "send", "reply", "trash", "mark_read", "list_unread", "read",
            "upload", "share", "delete", "download",
        }
        assert not (eligible & mail_and_drive_actions)


class TestNoSafetyMechanismIsBypassed:
    """Each of these covers a mechanism written when risk_level>=3 implied
    requires_confirmation=True. This phase broke that equivalence."""

    def test_the_call_is_still_classified_l3_external_write(self, isolated_cwd):
        """Confidence buys not-being-asked. It does not buy a lower risk
        level, and every downstream consumer keys on the risk level."""
        decision = _evaluate(CONFIDENT)
        assert decision.risk_level == 3
        assert decision.side_effect_type == "external_write"
        assert decision.allowed is True

    def test_the_kill_switch_still_vetoes_an_auto_approvable_create(self, isolated_cwd):
        """THE regression this phase could have introduced.

        The veto used to read `requires_confirmation and risk_level >= 3`,
        which was a safe no-op only while requires_confirmation was implied by
        risk_level >= 3. An auto-approved create would have walked straight
        past a tripped kill switch. How confident JARVIS feels is not an input
        to an emergency stop.
        """
        kill_switch.disable("test veto")
        try:
            decision = _evaluate(CONFIDENT)
            assert decision.allowed is False
            assert decision.veto_kind == "kill_switch"
            assert "kill switch is off" in decision.reason
        finally:
            kill_switch.enable()

    def test_the_kill_switch_veto_is_keyed_on_risk_level_alone(self, isolated_cwd):
        """Pins the fix itself, so a future edit cannot quietly restore the
        old conjunction."""
        import inspect

        source = inspect.getsource(policy_guard.evaluate)
        assert "if risk_level >= _KILL_SWITCH_RISK_THRESHOLD and not kill_switch.is_enabled():" in source

    @pytest.mark.asyncio
    async def test_profile_test_still_blocks_an_auto_approvable_create(self, isolated_cwd):
        """--profile test's structural zero-external-side-effect guarantee
        keys on side_effect_type, which Faz 2 does not touch. Proven here
        rather than assumed."""
        node = make_confirmation_node(_settings(external_writes_enabled=False))
        state = {
            "messages": [AIMessage(content="", tool_calls=[
                {"name": "google_calendar", "args": CONFIDENT, "id": "c1", "type": "tool_call"}])],
            "transport": "cli",
        }
        result = await node(state)
        assert result["confirmation_result"] == "denied"
        assert any("external writes are disabled" in m.content
                   for m in result["messages"] if hasattr(m, "content"))

    def test_a_proactive_turn_never_reaches_the_confidence_downgrade(self, isolated_cwd):
        """A background check has nobody watching, so `interactive` is derived
        from the transport and a monitor-* turn simply never gets the
        downgrade — the call stays requires_confirmation=True.

        Worth stating precisely, because the FIRST version of this test
        expected the proactive read-only gate to catch it instead. It does
        not, and it must not: that gate only fires for calls that are NOT
        going to interrupt. Because `interactive=False` keeps this call
        confirmable, a proactive turn takes the pre-existing, deliberately-
        chosen L3 path (interrupt -> discarded into a "needs_confirmation"
        notification by proactive_turn()), which is more informative than a
        flat block. Two mechanisms, and only one of them is load-bearing here.
        """
        assert _evaluate(CONFIDENT, interactive=False).requires_confirmation is True

    @pytest.mark.asyncio
    async def test_a_proactive_turn_reaches_the_real_gate_and_never_executes(
        self, isolated_cwd, monkeypatch,
    ):
        """The behavioural half of the above, through the real node."""
        from langgraph.errors import GraphInterrupt

        reached = []

        def _fake_interrupt(payload):
            reached.append(payload)
            raise GraphInterrupt()

        monkeypatch.setattr("langgraph.types.interrupt", _fake_interrupt)

        node = make_confirmation_node(_settings())
        state = {
            "messages": [AIMessage(content="", tool_calls=[
                {"name": "google_calendar", "args": CONFIDENT, "id": "c1", "type": "tool_call"}])],
            "transport": "monitor-email",
        }
        with pytest.raises(GraphInterrupt):
            await node(state)
        assert reached, "a proactive calendar create must not slip past the gate"

    @pytest.mark.asyncio
    async def test_the_same_call_in_an_interactive_turn_does_auto_approve(self, isolated_cwd):
        """The contrast that makes the previous two tests meaningful: identical
        args, human present, no interrupt."""
        node = make_confirmation_node(_settings())
        state = {
            "messages": [AIMessage(content="", tool_calls=[
                {"name": "google_calendar", "args": CONFIDENT, "id": "c1", "type": "tool_call"}])],
            "transport": "cli-text",
        }
        result = await node(state)
        assert result["confirmation_result"] == "approved"

    def test_the_two_graph_nodes_evaluate_the_same_call_identically(self, isolated_cwd):
        """prepare_execution_node and confirmation_node each call evaluate()
        independently. They can only agree because it is a pure function AND
        both derive `interactive` the same way from the transport.

        Calls the REAL _gate_inputs. It used to re-implement the derivation
        inline, which made it agree with itself no matter what the shipped rule
        was -- it stayed green through the task-async bug below and would have
        stayed green through the fix too.
        """
        from jarvis.graph.nodes import _gate_inputs

        for transport in ("cli", "api", "voice-local", "monitor-email", "unknown", None):
            interactive, _ = _gate_inputs({"transport": transport, "user_query": UTTERANCE})
            a = policy_guard.evaluate("google_calendar", CONFIDENT, _settings(), interactive=interactive)
            b = policy_guard.evaluate("google_calendar", CONFIDENT, _settings(), interactive=interactive)
            assert a == b, transport


# ── the unattended-transport hole (found by external review, 2026-08-01) ──────

class TestOnlyAnAttendedTransportCountsAsInteractive:
    """`interactive` means "a human is present, in this turn, to see what
    happens" -- so it must be an allowlist of transports that actually have
    one, not "everything except monitor-*".

    The old rule was a denylist. `task-async` -- the background TaskExecutor --
    does not start with "monitor-", so it was treated as attended and a
    background job's calendar create took the Faz 2 confidence downgrade: an
    L3 external write, executed with nobody watching. Faz 2's own notes claimed
    background turns never reach the downgrade; that was verified for monitor-*
    and assumed for the rest.
    """

    def test_task_async_is_not_a_human(self, isolated_cwd):
        from jarvis.graph.nodes import _gate_inputs

        interactive, _ = _gate_inputs({"transport": "task-async", "user_query": UTTERANCE})
        assert interactive is False

    def test_an_unrecognized_transport_fails_safe(self, isolated_cwd):
        """The property the denylist could not have: a transport added later,
        by someone with no reason to look at this file, is unattended until
        it is listed."""
        from jarvis.graph.nodes import _gate_inputs

        for transport in ("some-future-surface", "unknown", "", None):
            interactive, _ = _gate_inputs({"transport": transport, "user_query": UTTERANCE})
            assert interactive is False, transport

    def test_the_real_attended_surfaces_still_auto_approve(self, isolated_cwd):
        """The regression guard in the other direction: a fix that made
        everything unattended would pass every test above and silently delete
        the feature Faz 2 shipped."""
        from jarvis.graph.nodes import _gate_inputs

        for transport in ("cli", "cli-text", "api", "api-stream", "api-upload",
                          "voice-cli", "voice-local", "voice-remote"):
            interactive, _ = _gate_inputs({"transport": transport, "user_query": UTTERANCE})
            assert interactive is True, transport

    @pytest.mark.asyncio
    async def test_a_background_calendar_create_reaches_the_gate(
        self, isolated_cwd, monkeypatch,
    ):
        """The behavioural half, through the real confirmation node.

        Before the fix this returned confirmation_result="approved" and the
        event was written unattended.
        """
        from langgraph.errors import GraphInterrupt

        reached = []

        def _fake_interrupt(payload):
            reached.append(payload)
            raise GraphInterrupt()

        monkeypatch.setattr("langgraph.types.interrupt", _fake_interrupt)

        node = make_confirmation_node(_settings())
        state = {
            "messages": [AIMessage(content="", tool_calls=[
                {"name": "google_calendar", "args": CONFIDENT, "id": "c1", "type": "tool_call"}])],
            "transport": "task-async",
            "user_query": UTTERANCE,
        }
        with pytest.raises(GraphInterrupt):
            await node(state)
        assert reached, "a background calendar create must not slip past the gate"

        # Same node, same args, same utterance -- only the transport differs.
        # Without this the test above would also pass if the utterance check
        # were what fired, or if the downgrade had simply been deleted.
        approved = await node({**state, "transport": "cli-text"})
        assert approved["confirmation_result"] == "approved"

    @pytest.mark.asyncio
    async def test_an_auto_approved_create_is_still_written_to_the_audit_log(self, isolated_cwd):
        """"Not asked about" must never mean "not recorded". The audit filter
        is risk_level >= 2, and the risk level is untouched."""
        from jarvis import audit_log

        recorded = []
        original = audit_log.record
        audit_log.record = lambda *a, **kw: recorded.append(kw)
        try:
            node = make_confirmation_node(_settings())
            state = {
                "messages": [AIMessage(content="", tool_calls=[
                    {"name": "google_calendar", "args": CONFIDENT, "id": "c1", "type": "tool_call"}])],
                "transport": "cli",
            }
            await node(state)
        finally:
            audit_log.record = original

        rows = [r for r in recorded if r.get("tool") == "google_calendar"]
        assert rows, "an auto-approved L3 call left no audit trail"
        assert rows[0]["outcome"] == "auto_approved"
        assert rows[0]["risk_level"] == 3


class TestTheConfirmationPromptShowsTheResolvedDate:
    def test_the_prompt_names_the_day_not_the_expression(self):
        """A user cannot tell from the word "yarın" whether it resolved to the
        right day — which is exactly how a one-day-early event got approved."""
        from jarvis import clock
        from jarvis.clock import FrozenClock

        with clock.use_clock(FrozenClock.at("2026-08-01 01:30")):
            described = policy_guard.describe_call("google_calendar", {**CONFIDENT, "date": "yarın"})
        assert "2026-08-02 15:00" in described
        assert "create a calendar event" in described

    def test_an_unresolvable_date_falls_back_to_showing_the_raw_field(self):
        described = policy_guard.describe_call("google_calendar", {**CONFIDENT, "date": "haftaya"})
        assert "haftaya" in described

    def test_other_tools_are_described_exactly_as_before(self):
        described = policy_guard.describe_call("gmail", {"action": "send", "to": "a@b.c", "subject": "Hi"})
        assert described == "send an email (to=a@b.c, subject=Hi)"
