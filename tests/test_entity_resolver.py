"""jarvis/nlu/entities.py -- person names, resolved only when corroborated.

Post-MVP Faz 2, plan item 3. The failure being closed is the "Baranla ->
Baranda" class: Turkish attaches case endings to names, a model relays the
inflected form, and the calendar ends up holding an event for a person who does
not exist.

The load-bearing test in this file is not any of the happy paths -- it is
`TestNeverInventsAName`. Blind suffix-stripping would turn "Metin" into "Met"
and "Erdem" into "Erd", which is a worse bug than the one being fixed. The
design answer is that a stem is only ever adopted when a SOURCE says that
person exists; those tests are what hold the design to it.
"""
from __future__ import annotations

import pytest

from jarvis.nlu.entities import (
    SourceChain, StaticPersonSource, candidate_stems, resolve_person,
)


def chain(contacts=(), durable=(), conversation=()):
    sources = []
    if contacts:
        sources.append(StaticPersonSource(list(contacts), name="contacts"))
    if durable:
        sources.append(StaticPersonSource(list(durable), name="durable_entities"))
    if conversation:
        sources.append(StaticPersonSource(list(conversation), name="conversation"))
    return SourceChain(sources)


class TestTurkishCaseEndings:
    @pytest.mark.parametrize("mention", [
        "Baranla", "Baran'la", "Baranda", "Baran'da", "Barandan", "Barana",
        "Baranı", "Baranın", "Baranı", "BARANLA", "baranla",
    ])
    def test_an_inflected_form_resolves_to_the_name_in_contacts(self, mention):
        r = resolve_person(mention, chain(contacts=["Baran"]))
        assert r.canonical == "Baran", f"{mention} -> {r.canonical} ({r.reason})"
        assert r.band == "auto"

    @pytest.mark.parametrize("mention,expected", [
        ("Ayşeyle", "Ayşe"), ("Ahmet'le", "Ahmet"), ("Mehmet'in", "Mehmet"),
        ("Zeynep'e", "Zeynep"), ("Ayşe'ye", "Ayşe"),
    ])
    def test_other_names_and_endings(self, mention, expected):
        assert resolve_person(mention, chain(contacts=[expected])).canonical == expected

    def test_an_already_correct_name_is_returned_unchanged(self):
        r = resolve_person("Baran", chain(contacts=["Baran"]))
        assert r.canonical == "Baran" and r.changed is False

    def test_the_mention_itself_is_always_the_first_candidate_stem(self):
        """A correct name must never lose to a shortened guess."""
        assert candidate_stems("Metin")[0] == ("Metin", False)

    def test_a_name_too_short_to_strip_is_left_whole(self):
        assert candidate_stems("Ali") == [("Ali", False)]


class TestNeverInventsAName:
    """The property that makes suffix-stripping safe at all."""

    @pytest.mark.parametrize("mention", ["Metin", "Erdem", "Selin", "Aydın", "Ergün", "Baranla"])
    def test_an_unknown_name_is_left_exactly_as_written(self, mention):
        """No source knows it -> no stem is adopted, nothing is rewritten, and
        confidence is zero rather than "probably fine"."""
        r = resolve_person(mention, chain(contacts=["Ayşe", "Mehmet"]))
        assert r.canonical == mention
        assert r.changed is False
        assert r.confidence == 0.0
        assert r.band == "leave"

    def test_with_no_sources_at_all_nothing_is_touched(self):
        r = resolve_person("Baranla", SourceChain([]))
        assert r.canonical == "Baranla" and r.confidence == 0.0

    def test_two_plausible_people_produce_a_question_not_a_choice(self):
        """"Baran" and "Baram" are one letter apart. Picking one silently is
        the failure mode; naming both is the fix."""
        r = resolve_person("Baranla", chain(contacts=["Baran", "Baram"]))
        assert r.band == "ask"
        assert r.canonical == "Baranla", "an ambiguous mention must not be rewritten"
        assert set(r.alternatives) == {"Baran", "Baram"}

    def test_a_stem_and_its_full_form_both_being_real_people_is_ambiguous(self):
        r = resolve_person("Metin", chain(contacts=["Metin", "Met"]))
        assert r.band == "ask" and r.canonical == "Metin"

    def test_a_name_three_edits_away_is_not_a_candidate_at_all(self):
        """"Barancan" is not a plausible reading of "Baranla", so it must not
        manufacture ambiguity where there is none."""
        r = resolve_person("Baranla", chain(contacts=["Baran", "Barancan"]))
        assert r.band == "auto" and r.canonical == "Baran"

    @pytest.mark.parametrize("mention,known", [("Barn", "Baran"), ("Zeyneb", "Zeynep")])
    def test_a_fuzzy_match_never_auto_corrects(self, mention, known):
        """A typo in a person's name is worth asking about. Silently rewriting
        it to a different real person is not recoverable by the user."""
        r = resolve_person(mention, chain(contacts=[known]))
        assert r.band == "ask" and r.canonical == mention


class TestSourcePriority:
    def test_contacts_can_auto_normalise_an_inflected_form(self):
        assert resolve_person("Baranla", chain(contacts=["Baran"])).band == "auto"

    def test_durable_entities_alone_only_reach_the_ask_band_for_an_inflected_form(self):
        """"JARVIS has seen this name before" is weaker evidence than "this is
        in the user's contact list", and the ladder has to show that."""
        r = resolve_person("Baranla", chain(durable=["Baran"]))
        assert r.band == "ask" and r.canonical == "Baranla"

    def test_a_conversation_only_match_can_never_auto_act(self):
        """The model wrote the name two turns ago -- that is self-corroboration."""
        for mention in ("Baran", "Baranla"):
            assert resolve_person(mention, chain(conversation=["Baran"])).band != "auto"

    def test_a_higher_priority_source_wins_the_score(self):
        r = resolve_person("Baranla", chain(contacts=["Baran"], durable=["Baran"]))
        assert r.candidates[0].source == "contacts"
        assert r.band == "auto"

    def test_an_unavailable_source_is_skipped_rather_than_raising(self):
        broken = StaticPersonSource([], name="contacts")
        r = resolve_person("Baranla", SourceChain([broken, StaticPersonSource(["Baran"], name="durable_entities")]))
        assert r.candidates and r.candidates[0].source == "durable_entities"


class TestDurableEntitySource:
    def test_only_person_type_rows_are_offered(self):
        """The entities table also holds projects, files and organisations. A
        project called "Baran" is not a person."""
        from jarvis.nlu.entities import DurableEntitySource

        class _Store:
            def top_entities(self, n):
                return [
                    {"name": "Baran", "type": "person"},
                    {"name": "Horizon", "type": "project"},
                    {"name": "report.pdf", "type": "file"},
                ]

        assert list(DurableEntitySource(_Store()).people()) == ["Baran"]

    def test_a_failing_store_returns_nothing_instead_of_breaking_the_turn(self):
        from jarvis.nlu.entities import DurableEntitySource

        class _Broken:
            def top_entities(self, n):
                raise RuntimeError("db is gone")

        assert list(DurableEntitySource(_Broken()).people()) == []


class TestGoogleContactsSource:
    def test_it_is_unavailable_unless_explicitly_enabled(self):
        """Enabling it costs one OAuth re-consent, so it is the owner's call
        and not a default."""
        from types import SimpleNamespace

        from jarvis.nlu.entities import GoogleContactsSource

        assert GoogleContactsSource(settings=None).available() is False
        assert GoogleContactsSource(
            settings=SimpleNamespace(google_contacts_enabled=False)).available() is False
        assert GoogleContactsSource(
            settings=SimpleNamespace(google_contacts_enabled=True)).available() is True

    def test_a_disabled_source_yields_no_names_and_makes_no_call(self):
        from jarvis.nlu.entities import GoogleContactsSource

        assert list(GoogleContactsSource(settings=None).people()) == []

    def test_it_uses_its_own_token_file_so_calendar_and_gmail_are_untouched(self):
        """Sharing a token file would mean turning contacts on invalidates the
        token the working integrations are using."""
        from types import SimpleNamespace

        from jarvis.nlu.entities import GoogleContactsSource

        source = GoogleContactsSource(settings=SimpleNamespace(google_contacts_enabled=True))
        assert source._token_file().name == ".contacts_token.json"

        from jarvis.tools import calendar as cal

        assert cal._token_file().name != source._token_file().name


class TestEmptyAndDegenerateInput:
    @pytest.mark.parametrize("mention", ["", "   ", None])
    def test_nothing_in_nothing_out(self, mention):
        r = resolve_person(mention, chain(contacts=["Baran"]))
        assert r.canonical == "" and r.confidence == 0.0
