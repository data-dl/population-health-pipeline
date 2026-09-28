"""Monitoring helpers and the run summaries they produce."""

from __future__ import annotations

import json

from pophealth.monitoring import mask, normalise_error


def test_errors_that_differ_only_in_identifiers_group_together():
    a = normalise_error("Patient [00412345] not found in roster")
    b = normalise_error("Patient [00098765] not found  in roster")
    assert a == b == "Patient [] not found in roster"


def test_masking_hides_identifiers_but_keeps_file_dates():
    text = mask("appointments_main_20260302.csv: patient 00412345 called from 5552345678 on 2026-03-02")
    assert "20260302" in text and "2026-03-02" in text
    assert "00412345" not in text and "5552345678" not in text


def test_run_summaries_hold_no_record_numbers(scenario):
    mrns = {r["patient_id"] for r in scenario.rows("select patient_id from published.patients")}
    for result in scenario.results:
        for name in ("summary.md", "run_summary.json", "quarantine.csv", "issues.csv"):
            path = result.evidence_dir / name
            if path.exists():
                text = path.read_text(encoding="utf-8")
                assert not any(m in text for m in mrns), (result.run_id, name)


def test_task_results_are_recorded_for_every_task(scenario):
    for result in scenario.results:
        lines = (result.evidence_dir / "task_results.jsonl").read_text(encoding="utf-8").splitlines()
        recorded = {(json.loads(x)["dag_id"], json.loads(x)["task_id"]) for x in lines}
        expected = {(d.dag_id, t) for d in result.dag_runs for t in d.tasks}
        assert recorded == expected
