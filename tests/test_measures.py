"""dbt's measures against the generator's independent Python implementation (synth/oracle.py): every
clinic, every measure, and adherence patient by patient."""

from __future__ import annotations

from synth.oracle import patient_pdc


def test_every_dbt_model_and_test_passed_in_every_run(scenario):
    statuses = {r["status"] for r in scenario.rows("select status from audit.dbt_results")}
    assert statuses <= {"success", "pass"}, statuses
    runs = {r["run_id"] for r in scenario.rows("select distinct run_id from audit.dbt_results")}
    assert runs == {r.run_id for r in scenario.results[:4]}


def test_measure_summary_matches_the_answer_key_everywhere(scenario):
    got = {
        (r["measure_id"], r["site_id"]): [r["numerator"], r["denominator"]]
        for r in scenario.rows("select measure_id, site_id, numerator, denominator from marts.measure_summary")
    }
    expected = {(m, site): v for m, by_site in scenario.key["measures"].items() for site, v in by_site.items()}
    wrong = {k: (expected[k], got.get(k)) for k in expected if got.get(k) != expected[k]}
    assert not wrong, list(wrong.items())[:5]
    assert set(got) == set(expected)


def test_adherence_matches_patient_by_patient(scenario, world):
    expected = patient_pdc(world)
    got = {
        (r["patient_id"], r["drug_class"]): (r["days_covered"], r["period_days"])
        for r in scenario.rows("select patient_id, drug_class, days_covered, period_days from dbt_intermediate.int_pdc")
    }
    assert got == expected


def test_late_rows_restate_the_closed_year(scenario):
    history = {
        (r["run_id"], r["measure_id"]): (r["numerator"], r["denominator"])
        for r in scenario.rows(
            "select run_id, measure_id, numerator, denominator from audit.measure_history where site_id = 'ALL'"
        )
    }
    first, second = scenario.run(1).run_id, scenario.run(2).run_id
    restated = [m for (run, m), v in history.items() if run == second and history[(first, m)] != v]
    assert "PDC_STATIN" in restated or "PDC_DIABETES" in restated or "PDC_RAS" in restated
    summary = (scenario.run(2).evidence_dir / "summary.md").read_text(encoding="utf-8")
    assert "(restated)" in summary


def test_equity_tables_never_show_a_small_cell(scenario):
    rows = scenario.rows("select * from marts.measure_equity")
    assert rows
    for r in rows:
        if r["suppressed"]:  # no value at all, or a small group's size could be recovered from the total
            assert r["denominator"] is None and r["numerator"] is None and r["rate"] is None
            assert r["share_of_denominator"] is None and r["share_of_numerator"] is None
        else:
            assert r["denominator"] >= 11
            assert not 1 <= r["numerator"] <= 10
            assert not 1 <= r["denominator"] - r["numerator"] <= 10
    by_stratifier: dict[tuple[str, str], int] = {}
    for r in rows:
        by_stratifier[(r["measure_id"], r["stratifier"])] = by_stratifier.get(
            (r["measure_id"], r["stratifier"]), 0
        ) + int(r["suppressed"])
    assert 1 not in by_stratifier.values()


def test_care_gaps_follow_from_measure_results(scenario):
    gaps = scenario.scalar("select count(*) from marts.care_gaps where gap = 'hba1c_poor_control'")
    poor = scenario.scalar(
        "select numerator from marts.measure_summary where measure_id = 'DM_A1C_POOR' and site_id = 'ALL'"
    )
    assert gaps == poor
