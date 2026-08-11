"""The tournament corpus stays product-shaped, Turkish-heavy, and safety-complete."""

from __future__ import annotations

from pathlib import Path

from jarvis.evals.nvidia_scenarios import SCENARIOS, SMOKE_SCENARIO_IDS


def test_corpus_is_at_least_seventy_percent_turkish():
    turkish = sum(scenario.language == "tr" for scenario in SCENARIOS)
    assert turkish / len(SCENARIOS) >= 0.70


def test_scenario_ids_are_unique_and_smoke_ids_exist():
    ids = [scenario.id for scenario in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert SMOKE_SCENARIO_IDS <= set(ids)


def test_required_product_and_safety_classes_are_present():
    tags = {tag for scenario in SCENARIOS for tag in scenario.tags}
    assert {
        "conversation",
        "calendar",
        "file",
        "mail",
        "drive",
        "task",
        "weather",
        "multi-step",
        "source-binding",
        "missing-source",
        "timeout",
        "denial",
        "must-call",
        "turkish-date",
    } <= tags


def test_confirmed_writes_use_fake_approval_and_denials_expect_no_write():
    confirmed = [scenario for scenario in SCENARIOS if scenario.confirmation == "approve"]
    denied = [scenario for scenario in SCENARIOS if scenario.confirmation == "deny"]
    assert confirmed and denied
    assert all(scenario.expected_tools for scenario in confirmed)
    assert all(scenario.expected_effect == "no_external_write" for scenario in denied)


def test_finalist_subset_contains_every_hard_safety_shape():
    critical = [scenario for scenario in SCENARIOS if scenario.critical]
    effects = {scenario.expected_effect for scenario in critical}
    assert {"calendar_created", "mail_sent", "no_external_write", "tool_failure", "unknown"} <= effects
    assert any(scenario.complex for scenario in critical)


def test_dependent_drive_chain_uses_the_documented_read_action():
    scenario = next(item for item in SCENARIOS if item.id == "tr-drive-dependent-chain")
    assert [item.name for item in scenario.expected_tools] == ["google_drive", "google_drive"]
    assert [item.args["action"] for item in scenario.expected_tools] == ["search", "read"]


def test_synthetic_drive_backend_supports_the_scenario_read_action():
    harness = (Path(__file__).parents[1] / "scripts" / "nvidia_model_tournament.py").read_text(
        encoding="utf-8"
    )
    assert 'if action == "read":' in harness
    assert 'return "Q3 toplam bütçe: 420000 TL"' in harness
