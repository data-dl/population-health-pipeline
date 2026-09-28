"""Contracts load, are internally consistent, and match supplier files the way they should."""

from __future__ import annotations

import re
from datetime import date

import pytest

from pophealth.contracts import ACTIONS, CLASSIFICATIONS, RULE_TYPES, ContractError, load_contract
from pophealth.settings import load_settings

PREFIX = {"roster": "ROS", "screenings": "SCR", "labs": "LAB", "appointments": "APT", "pharmacy": "RX"}


@pytest.fixture(scope="module")
def settings():
    return load_settings()


def test_every_feed_has_a_contract(settings):
    assert set(settings.contracts) == set(settings.feeds_order) == set(PREFIX)


def test_rules_are_well_formed(settings):
    seen = set()
    for feed, contract in settings.contracts.items():
        for rule in contract.rules:
            assert re.fullmatch(rf"{PREFIX[feed]}-[0-9]{{3}}", rule.id), rule.id
            assert rule.type in RULE_TYPES and rule.action in ACTIONS
            assert rule.id not in seen
            seen.add(rule.id)
            if rule.retryable:
                assert rule.action == "quarantine", rule.id


def test_every_feed_checks_patients_against_the_roster(settings):
    for feed, contract in settings.contracts.items():
        if feed != "roster":
            assert any(r.type == "on_roster" and r.retryable for r in contract.rules), feed
        assert any(r.type == "volume" and r.action == "block" for r in contract.rules), feed
        assert any(r.type == "key_conflict" for r in contract.rules), feed


def test_columns_are_classified(settings):
    for contract in settings.contracts.values():
        assert contract.columns
        assert all(c["classification"] in CLASSIFICATIONS for c in contract.columns)
    assert settings.contracts["roster"].classification("patient_id") == "direct_identifier"


def test_file_names_match_sources_with_redelivery(settings):
    appts = settings.contracts["appointments"]
    m = appts.match_file("appointments_main_20260302_r1.csv")
    assert (m.source, m.extract, m.redelivery) == ("clinic_scheduling", date(2026, 3, 2), 1)
    assert appts.match_file("appointments_mobile_20260302.csv").source == "mobile_unit"
    assert appts.match_file("appointments_20260302.csv") is None
    assert settings.contracts["labs"].match_file("labs_2026030.csv") is None


def test_layouts_match_exact_headers_only(settings):
    lab = settings.contracts["labs"].sources["reference_lab"]
    v1 = ["OrderID", "MRN", "Collected", "LOINC", "TestName", "Result", "RefRange", "AbnFlag"]
    assert lab.match_layout(v1).version == 1
    assert lab.match_layout(list(reversed(v1))).version == 1
    assert lab.match_layout(v1 + ["Units"]) is None
    best, missing, unexpected = lab.closest_layout(v1[:-1] + ["Units"])
    assert best.version == 1 and missing == ["AbnFlag"] and unexpected == ["Units"]


def test_a_snapshot_feed_cannot_have_retryable_rules(tmp_path, settings):
    text = settings.contracts["roster"].path.read_text(encoding="utf-8")
    held_for_site = "    action: quarantine\n    column: site_id"
    assert held_for_site in text
    text = text.replace(held_for_site, "    action: quarantine\n    retryable: true\n    column: site_id")
    bad = tmp_path / "roster.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError, match="snapshot"):
        load_contract(bad)


def test_a_bad_contract_is_rejected(tmp_path, settings):
    text = settings.contracts["pharmacy"].path.read_text(encoding="utf-8").replace("type: mapped", "type: guessed", 1)
    bad = tmp_path / "bad.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract(bad)
