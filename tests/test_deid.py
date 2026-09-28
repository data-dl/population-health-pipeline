"""The de-identified copies: the leak gate passed, it would have caught a leak, surrogates are stable,
and intervals survive the date shift."""

from __future__ import annotations

import copy
import json
import shutil
from datetime import date

import yaml

from pophealth.deid.engine import RealValues, apply_path
from pophealth.deid.verify import _strings, _text_leaks, verify
from pophealth.documents.docstore import DocumentStore
from pophealth.settings import load_settings
from pophealth.warehouse import Warehouse


def test_leak_gate_passed_and_the_copy_was_released(scenario):
    report = json.loads((scenario.run(4).evidence_dir / "deid_verification.json").read_text(encoding="utf-8"))
    assert report["passed"], [c for c in report["checks"] if not c["passed"]]
    released = DocumentStore(scenario.settings.docstore, "demo")
    assert released.count("patients") == scenario.scalar("select count(*) from published.patients")
    assert (scenario.settings.extracts / "safe_harbor_patients.csv").exists()


def test_the_leak_detector_finds_planted_leaks(scenario):
    with Warehouse(scenario.settings.warehouse_path) as wh:
        real = RealValues.load(wh)
    doc = copy.deepcopy(DocumentStore(scenario.settings.docstore, "demo").find("patients")[0])
    victim = next(p for p in real.by_patient.values() if p["phone"] and p["address_line1"])
    doc["careGaps"].append(
        {
            "gap": "note",
            "detail": f"called {victim['first_name']} {victim['last_name']} "
            f"on {victim['phone']} about record {victim['patient_id']}; lives at {victim['address_line1']}.",
        }
    )
    found = _text_leaks(_strings(doc), real)
    assert found["full name"] and found["phone"] and found["record number"] and found["street address"]
    clean = DocumentStore(scenario.settings.docstore, "demo").find("patients")[0]
    assert not any(_text_leaks(_strings(clean), real).values())


def test_a_policy_that_misses_an_identifier_fails_the_gate(scenario, tmp_path):
    config = tmp_path / "config"
    shutil.copytree(scenario.settings.config_dir, config)
    policy_path = config / "deid" / "demo_site.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    del policy["collections"]["patients"]["transforms"]["demographics.phone"]
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    settings = load_settings(workspace=scenario.root, config_dir=config, inbox=scenario.settings.inbox)
    report = verify(settings)
    failed = {c["check"]: c["detail"] for c in report["checks"] if not c["passed"]}
    assert "policy transforms every registered identifier path" in failed
    assert "demographics.phone" in failed["policy transforms every registered identifier path"]


def test_a_document_path_from_an_unclassified_column_fails_the_gate(scenario, tmp_path):
    config = tmp_path / "config"
    shutil.copytree(scenario.settings.config_dir, config)
    contract_path = config / "contracts" / "roster.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    contract["columns"] = [c for c in contract["columns"] if c["name"] != "email"]
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    settings = load_settings(workspace=scenario.root, config_dir=config, inbox=scenario.settings.inbox)
    failed = {c["check"]: c["detail"] for c in verify(settings)["checks"] if not c["passed"]}
    assert "roster.email is not classified" in failed["policy transforms every registered identifier path"]


def test_surrogates_are_stable_and_unique(scenario):
    rows = scenario.rows(
        "select kind, real_value, surrogate, created_run from restricted.deid_crosswalk where kind = 'patient'"
    )
    assert len({r["surrogate"] for r in rows}) == len(rows)
    created = {r["created_run"] for r in rows}
    assert created <= {scenario.run(1).run_id, scenario.run(2).run_id, scenario.run(3).run_id}
    demo_ids = {d["_id"] for d in DocumentStore(scenario.settings.docstore, "demo").find("patients")}
    published = {p["patient_id"] for p in scenario.rows("select patient_id from published.patients")}
    assert demo_ids == {r["surrogate"] for r in rows if r["real_value"] in published}
    extract = scenario.rows("select surrogate from restricted.deid_crosswalk where kind = 'extract_patient'")
    assert not {r["surrogate"] for r in extract} & demo_ids


def test_date_shift_preserves_intervals(scenario):
    real = {d["_id"]: d for d in DocumentStore(scenario.settings.docstore, "restricted").find("patients")}
    demo = {d["_id"]: d for d in DocumentStore(scenario.settings.docstore, "demo").find("patients")}
    pairs = {
        r["surrogate"]: r["real_value"]
        for r in scenario.rows("select real_value, surrogate from restricted.deid_crosswalk where kind = 'patient'")
    }
    checked = 0
    for surrogate, doc in demo.items():
        original = real[pairs[surrogate]]
        for block_name in ("screenings", "labs"):
            for name, block in original[block_name].items():
                for a, b in zip(block["history"], doc[block_name][name]["history"], strict=True):
                    gap_real = date.fromisoformat(a["date"]) - date.fromisoformat(original["demographics"]["birthDate"])
                    gap_demo = date.fromisoformat(b["date"]) - date.fromisoformat(doc["demographics"]["birthDate"])
                    assert gap_real == gap_demo
                    checked += 1
    assert checked > 5000


def test_extract_meets_safe_harbor_shape(scenario):
    import csv

    with open(scenario.settings.extracts / "safe_harbor_patients.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows and "first_name" not in rows[0] and "birth_date" not in rows[0] and "zip" not in rows[0]
    assert all(r["birth_year"] == "" for r in rows if r["age_at_year_end"] == "90+")
    assert any(r["age_at_year_end"] == "90+" for r in rows)
    assert "704" not in {r["zip3"] for r in rows} and "000" in {r["zip3"] for r in rows}


def test_path_syntax():
    doc = {"a": {"x": {"h": [{"d": 1}, {"d": 2}]}, "y": {"h": [{"d": 3}]}}, "l": [1, 2], "n": None}
    apply_path(doc, "a.*.h[].d", lambda v: v * 10)
    apply_path(doc, "l[]", lambda v: -v)
    apply_path(doc, "n.z", lambda v: 1 / 0)  # a missing branch is skipped, never evaluated
    assert doc == {"a": {"x": {"h": [{"d": 10}, {"d": 20}]}, "y": {"h": [{"d": 30}]}}, "l": [-1, -2], "n": None}
