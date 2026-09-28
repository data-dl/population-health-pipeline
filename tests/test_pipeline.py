"""The five-run scenario graded against the answer key: every planted defect is caught by the rule
it was planted for, nothing else is touched, every row is accounted for, and the scenario's story
(stopped file, re-delivery, late delivery, late registrations, layout change, idempotent re-run)
happens exactly as intended."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

FEEDS = ["roster", "screenings", "labs", "appointments", "pharmacy"]


def _quarantine(scenario) -> dict[tuple[str, str, int], dict]:
    held = {}
    for feed in FEEDS:
        for r in scenario.rows(f"select _file, _row_num, _q_rule_ids, _q_status from quarantine.{feed}"):
            held[(feed, r["_file"], r["_row_num"])] = {
                "rules": set(r["_q_rule_ids"].split(",")),
                "status": r["_q_status"],
            }
    return held


def _planted(scenario, *outcomes: str) -> list[dict]:
    return [p for p in scenario.key["planted"] if p["expect"] in outcomes]


def test_every_run_succeeds(scenario):
    assert [r.status for r in scenario.results] == ["success"] * 5


def test_every_planted_row_defect_is_caught_by_its_rule(scenario):
    held = _quarantine(scenario)
    missed = [
        p
        for p in _planted(scenario, "quarantined")
        if p["rule"] not in held.get((p["feed"], p["file"], p["row"]), {}).get("rules", set())
    ]
    assert not missed, missed[:5]


def test_nothing_else_is_held(scenario):
    planted = {(p["feed"], p["file"], p["row"]) for p in _planted(scenario, "quarantined")}
    unexpected = sorted(set(_quarantine(scenario)) - planted)
    assert not unexpected, unexpected[:5]


def test_held_rows_end_where_the_story_says(scenario):
    held = _quarantine(scenario)
    wrong = [
        (p["id"], p["kind"], p["final"], held[(p["feed"], p["file"], p["row"])]["status"])
        for p in _planted(scenario, "quarantined")
        if held[(p["feed"], p["file"], p["row"])]["status"] != p["final"]
    ]
    assert not wrong, wrong[:5]


def test_late_registrations_are_released_on_the_second_run(scenario):
    released = scenario.rows("""
        select distinct _q_released_run from quarantine.labs where _q_status = 'released'
        union select distinct _q_released_run from quarantine.screenings where _q_status = 'released'
        union select distinct _q_released_run from quarantine.appointments where _q_status = 'released'
        union select distinct _q_released_run from quarantine.pharmacy where _q_status = 'released'""")
    assert {r["_q_released_run"] for r in released} == {scenario.run(2).run_id}


def test_repairs_and_warnings_are_exactly_the_planted_ones(scenario):
    got = {
        (r["feed"], r["name"], r["row_num"], r["rule_id"])
        for r in scenario.rows("""
        select f.feed, l.name, f.row_num, f.rule_id from audit.row_findings f join audit.loads l using (load_id)
        where f.candidate = 'new' and f.action in ('repair', 'warn')""")
    }
    expected = {(p["feed"], p["file"], p["row"], p["rule"]) for p in _planted(scenario, "repaired", "warned")}
    assert got == expected, (sorted(got - expected)[:5], sorted(expected - got)[:5])


def test_merge_counts_match_the_answer_key(scenario):
    loads = {r["name"]: r for r in scenario.rows("select * from audit.loads")}
    for f in scenario.key["files"]:
        for count, expected in f.get("counts", {}).items():
            assert loads[f["name"]][f"rows_{count}"] == expected, (f["name"], count)


def test_every_file_ends_as_expected(scenario):
    loads = {r["name"]: r for r in scenario.rows("select * from audit.loads")}
    for f in scenario.key["files"]:
        load = loads[f["name"]]
        if f["outcome"] == "blocked":
            assert load["blocked_rule"] == f["rule"] and load["status"] == "superseded", load
        else:
            assert load["status"] == "published", load
            assert load["layout_version"] == f["layout"]
    for missing in scenario.key["missing_files"]:
        assert missing["name"] not in loads


def test_every_row_of_every_file_is_accounted_for(scenario):
    # Each count is measured on its own (de-duplicated rows included), so these identities can fail.
    for load in scenario.rows("select * from audit.loads where status in ('merged', 'published')"):
        assert load["rows_raw"] == load["rows_staged"] + load["rows_malformed"], load["name"]
        assert load["rows_staged"] == load["rows_passed"] + load["rows_quarantined"], load["name"]
        assert load["rows_passed"] == (
            load["rows_inserted"] + load["rows_updated"] + load["rows_unchanged"] + load["rows_deduped"]
        ), load["name"]


def test_the_truncated_file_holds_the_feed_until_it_is_redelivered(scenario):
    gates = {(r["run_id"], r["feed"]): r["decision"] for r in scenario.rows("select * from audit.gate_decisions")}
    assert gates[(scenario.run(3).run_id, "appointments")] == "HOLD"
    assert gates[(scenario.run(4).run_id, "appointments")] == "PUBLISH"
    mobile = scenario.rows(
        "select merged_run, published_run from audit.loads where name = 'appointments_mobile_20260302.csv'"
    )
    assert mobile == [{"merged_run": scenario.run(3).run_id, "published_run": scenario.run(4).run_id}]


def test_measures_run_only_when_published_content_changed(scenario):
    triggered = [r.dag("measures_and_documents") is not None for r in scenario.results]
    assert triggered == [True, True, True, True, False]
    run3 = {d.dag_id: d.triggers for d in scenario.run(3).dag_runs}
    assert run3["feed_appointments"] == [] and run3["feed_pharmacy"] == []


def test_same_day_rerun_changes_nothing(scenario):
    last = scenario.run(5).run_id
    assert scenario.scalar("select count(*) from audit.loads where run_id = ?", [last]) == 0
    assert scenario.scalar("select count(*) from audit.publish_log where run_id = ?", [last]) == 0
    assert scenario.scalar("select count(*) from audit.row_findings where run_id = ?", [last]) == 0
    for dag in scenario.run(5).dag_runs:
        if dag.dag_id.startswith("feed_"):
            assert dag.tasks["wait_for_delivery"].status == "skipped", dag.dag_id
            assert dag.tasks["load_raw"].status == "skipped" and dag.status == "success"


def test_layout_change_is_recognised_and_clean_archived_first(scenario):
    changes = scenario.rows("select * from audit.schema_changes")
    assert len(changes) == 1
    change = changes[0]
    assert (change["feed"], change["from_version"], change["to_version"], change["change_record"]) == (
        "labs",
        1,
        2,
        "CR-004",
    )
    archived = scenario.scalar(f"select count(*) from archive.{change['archived_table']}")
    assert archived > 2000


def test_ifcc_results_are_converted_and_censored_results_keep_their_bound(scenario):
    rows = {
        r["lab_result_id"]: r
        for r in scenario.rows("select lab_result_id, value, censor from published.lab_results where analyte = 'HBA1C'")
    }
    ifcc = [
        p
        for p in scenario.rows("select lab_result_id, result_value, value_std from clean.labs where units = 'mmol/mol'")
    ]
    assert ifcc, "the third extract carries IFCC results"
    for r in ifcc:
        expected = (Decimal(r["result_value"]) * Decimal("0.09148") + Decimal("2.152")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        assert Decimal(rows[r["lab_result_id"]]["value"]) == expected
    censored = [r for r in rows.values() if r["censor"]]
    assert {r["censor"] for r in censored} == {"<", ">"}
    assert {float(r["value"]) for r in censored} == {4.0, 14.0}


def test_roster_history_is_type_2(scenario):
    history = scenario.rows(
        "select patient_id, site_id, valid_from, valid_to, is_current from published.patient_attribution_history"
    )
    versions: dict[str, list] = {}
    for h in history:
        versions.setdefault(h["patient_id"], []).append(h)
    movers = [p for p, v in versions.items() if len({x["site_id"] for x in v}) > 1]
    assert len(movers) == 25
    for p in movers:
        first, second = sorted(versions[p], key=lambda x: x["valid_from"])
        assert first["valid_to"] == date(2026, 2, 22) and not first["is_current"]
        assert second["valid_from"] == date(2026, 2, 23) and second["is_current"]
    assert all(sum(x["is_current"] for x in v) == 1 for v in versions.values())


def test_patients_whose_clinic_does_not_exist_never_reach_published(scenario):
    unknown = {p["key"] for p in scenario.key["planted"] if p["kind"] == "roster_unknown_site"}
    published = {r["patient_id"] for r in scenario.rows("select patient_id from published.patients")}
    assert unknown and not unknown & published


def test_freshness_tells_the_story(scenario):
    def fresh(run: int) -> dict[tuple[str, str], dict]:
        return {
            (r["feed"], r["source"]): r
            for r in scenario.rows("select * from audit.freshness where run_id = ?", [scenario.run(run).run_id])
        }

    third, fourth = fresh(3), fresh(4)
    assert third[("pharmacy", "pharmacy_claims")]["delivery_status"] == "DUE"
    assert third[("appointments", "clinic_scheduling")]["pipeline_status"] == "STUCK"
    assert third[("appointments", "mobile_unit")]["pipeline_status"] == "STUCK"
    assert fourth[("pharmacy", "pharmacy_claims")]["delivery_status"] == "LATE"
    assert fourth[("appointments", "clinic_scheduling")]["overall"] == "OK"
    assert all(r["overall"] == "OK" for k, r in fourth.items() if k[0] not in ("pharmacy",))


def test_issue_register_opens_ages_and_resolves(scenario):
    issues = {r["issue_id"]: r for r in scenario.rows("select * from audit.issues")}
    stopped = issues["appointments:APT-001:appointments_main_20260302.csv"]
    assert (stopped["first_seen"], stopped["status"], stopped["resolved_on"]) == (
        date(2026, 3, 2),
        "resolved",
        date(2026, 3, 3),
    )
    assert issues["roster:ROS-010"]["status"] == "resolved"
    assert issues["pharmacy:pharmacy_claims:late"]["status"] == "open"
    still_held = {f"{p['feed']}:{p['rule']}" for p in _planted(scenario, "quarantined") if p["final"] == "held"}
    open_now = {i for i, r in issues.items() if r["status"] == "open"}
    assert open_now == still_held | {"pharmacy:pharmacy_claims:late"}


def test_each_run_leaves_its_evidence(scenario):
    for result in scenario.results:
        for name in (
            "summary.md",
            "manifest.json",
            "dq_scorecard.csv",
            "freshness.csv",
            "issues.csv",
            "reconciliation.csv",
            "task_results.jsonl",
        ):
            assert (result.evidence_dir / name).exists(), (result.run_id, name)
    summary = (scenario.run(3).evidence_dir / "summary.md").read_text(encoding="utf-8")
    assert "publication held" in summary and "appointments_main_20260302.csv" in summary
    assert "layout v1 -> v2" in summary
