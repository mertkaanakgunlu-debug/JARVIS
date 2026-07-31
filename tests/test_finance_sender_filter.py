"""The Gmail sender filter must be able to name more than one sender.

Found live on the owner's own mailbox, 2026-07-30. finance_sender_filter was the
single value "burgan", but Burgan Bank's consumer digital brand is **ON** and its
mail arrives from `m.on.com.tr` — which `from:burgan` never matches. The owner had
notifications switched on and reasonably expected them to be picked up; the sync
would have reported "📭 Banka bildirimi bulunamadı" forever and looked like an
empty mailbox rather than a misconfigured filter.

A bank's notification sender is frequently not its brand domain, so this is a
class of bug, not a one-off: the setting is now a comma-separated list.
"""
from __future__ import annotations

import pytest

from jarvis.config import Settings
from jarvis.tools.finance import _sender_clause


def test_single_value_stays_a_plain_from_clause():
    """Every pre-existing single-value config must behave exactly as before."""
    assert _sender_clause("burgan") == "from:burgan"


def test_multiple_values_become_one_or_clause():
    assert _sender_clause("burgan,on.com.tr") == "from:(burgan OR on.com.tr)"


def test_whitespace_and_empty_entries_are_tolerated():
    assert _sender_clause(" burgan , , on.com.tr ") == "from:(burgan OR on.com.tr)"


@pytest.mark.parametrize("value", ["", "   ", ",,,", None])
def test_empty_filter_falls_back_rather_than_matching_everything(value):
    """An empty clause would make the query `after:...` alone -- i.e. EVERY mail in
    the window fed to the extractor. Failing closed matters more than being clever."""
    clause = _sender_clause(value)
    assert clause.startswith("from:")
    assert clause != "from:"


def test_the_shipped_default_covers_the_on_domain():
    """The specific regression: the default must match Burgan's actual notification
    sender, not just its brand name."""
    clause = _sender_clause(Settings(_env_file=None).finance_sender_filter)

    assert "on.com.tr" in clause
    assert "burgan" in clause
