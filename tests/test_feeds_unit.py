"""Single feeds on small, hand-written files: parsing, scoring, repairs, rejections, blocking,
waivers, resends and releases - each rule exercised in isolation."""

from __future__ import annotations

import shutil
from datetime import date

import yaml

from pophealth.cli import main as cli
from pophealth.pipeline import run_pipeline
from pophealth.settings import load_settings

from .conftest import DAY, ROSTER_HEADER, query, roster_row, write_file

LAB_V1 = ["OrderID", "MRN", "Collected", "LOINC", "TestName", "Result", "RefRange", "AbnFlag"]
LAB_V2 = [
    "accession_id",
    "patient_mrn",
    "collection_date",
    "loinc_code",
    "test_description",
    "result_value",
    "result_units",
    "reference_range",
    "abnormal_flag",
    "specimen_id",
]
SCREEN = (
    ["screening_id", "mrn", "screen_date", "admin_mode"]
    + [f"phq9_q{i}" for i in range(1, 10)]
    + ["phq9_total"]
    + [f"gad7_q{i}" for i in range(1, 8)]
    + ["gad7_total"]
    + [f"sdoh_{d}" for d in ("housing", "food", "transport", "utilities", "safety")]
)
APPT = ["APPT_ID", "MRN", "SITE", "APPT_DATE", "VISIT_TYPE", "STATUS", "PROVIDER"]


def _roster(inbox, day="2026-02-16", ids=("00000101", "00000102")):
    write_file(inbox / day, f"roster_{day.replace('-', '')}.csv", ROSTER_HEADER, [roster_row(i) for i in ids])


def _lab(aid, mrn, day, loinc, result, unit=""):
    return {
        "accession_id": aid,
        "patient_mrn": mrn,
        "collection_date": day,
        "loinc_code": loinc,
        "result_value": result,
        "result_units": unit,
    }


def test_lab_units_censoring_repairs_and_rejections(small):
    settings, inbox = small
    _roster(inbox)
    write_file(
        inbox / "2026-02-16",
        "labs_20260216.csv",
        LAB_V2,
        [
            _lab("A1", "00000101", "2026-02-01", "59261-8", "64", "mmol/mol"),
            _lab("A2", "00000101", "2026-02-02", "4548-4", ">14.0", "%"),
            _lab("A3", "00000102", "2026-02-03", "13457-7", "131", "mg%"),
            _lab("A4", "00000102", "2026-02-03", "4548-4", "QNS", "%"),
            _lab("A5", "00000101", "2026-02-04", "4548-4", "53", "%"),
            _lab("A6", "101", "2026-02-05", "4548-4", "7.25", "%"),
            _lab("A7", "00000999", "2026-02-05", "4548-4", "6.1", "%"),
            _lab("A8", "00000101", "2026-03-05", "4548-4", "6.0", "%"),
            _lab("A9", "00000101", "2026-02-06", "1234-5", "5", ""),
        ],
    )
    assert run_pipeline(settings, DAY, feeds=["roster", "labs"], downstream=False).status == "success"
    published = {r["lab_result_id"]: r for r in query(settings, "select * from published.lab_results")}
    assert float(published["A1"]["value"]) == 8.0  # 64 mmol/mol = 8.00672 %
    assert (published["A2"]["censor"], float(published["A2"]["value"])) == (">", 14.0)
    assert float(published["A6"]["value"]) == 7.3 and published["A6"]["patient_id"] == "00000101"
    held = {
        r["lab_result_id"]: r["_q_rule_ids"]
        for r in query(settings, "select lab_result_id, _q_rule_ids from quarantine.labs")
    }
    assert held == {
        "A3": "LAB-009",
        "A4": "LAB-008",
        "A5": "LAB-010",
        "A7": "LAB-011",
        "A8": "LAB-006",
        "A9": "LAB-007",
    }
    repaired = query(settings, "select rule_id, row_num from audit.row_findings where action = 'repair'")
    assert repaired == [{"rule_id": "LAB-003", "row_num": 6}]


def _screen(sid, *, items=("1",) * 9, total="9", sdoh=("", "", "", "", ""), mode="IN_PERSON", day="2026-02-10"):
    row = {"screening_id": sid, "mrn": "00000101", "screen_date": day, "admin_mode": mode, "phq9_total": total}
    row.update({f"phq9_q{i + 1}": v for i, v in enumerate(items)})
    row.update(
        {f"sdoh_{d}": v for d, v in zip(("housing", "food", "transport", "utilities", "safety"), sdoh, strict=True)}
    )
    return row


def test_screening_scoring_and_validation(small):
    settings, inbox = small
    _roster(inbox)
    write_file(
        inbox / "2026-02-16",
        "screenings_20260216.csv",
        SCREEN,
        [
            _screen("S1"),
            _screen("S2", items=("1", "2.5", "1", "1", "1", "1", "1", "1", "1")),
            _screen("S3", items=("1", "", "1", "1", "1", "1", "1", "1", "1"), total=""),
            _screen("S4", total="12"),
            _screen("S5", sdoh=("N", "maybe", "N", "N", "N")),
            _screen("S6", mode="Kiosk"),
            _screen("S7", day="2026-02-30"),
            _screen("S8", items=("2", "2", "2", "2", "2", "2", "1", "1", "1"), total="15"),
            _screen("S9", items=("", "", "", "", "", "", "", "", ""), total="", sdoh=("N", "Y", "N", "D", "N")),
        ]
        # Enough ordinary rows that one bad item is under SCR-007's 5% stop threshold.
        + [_screen(f"F{i:02d}") for i in range(30)],
    )
    assert run_pipeline(settings, DAY, feeds=["roster", "screenings"], downstream=False).status == "success"
    events = {
        (r["screening_id"], r["instrument"]): r for r in query(settings, "select * from published.screening_events")
    }
    assert (events[("S1", "PHQ9")]["total_score"], events[("S1", "PHQ9")]["severity_band"]) == (9, "mild")
    assert events[("S3", "PHQ9")]["complete"] is False and events[("S3", "PHQ9")]["total_score"] is None
    assert events[("S4", "PHQ9")]["total_score"] == 9  # the platform said 12; the items say 9
    s8 = events[("S8", "PHQ9")]
    assert (s8["total_score"], s8["severity_band"], s8["positive"], s8["safety_flag"]) == (
        15,
        "moderately_severe",
        True,
        True,
    )
    s9 = events[("S9", "SDOH5")]
    assert (s9["complete"], s9["positive"], s9["total_score"]) == (True, True, 1)
    assert ("S9", "PHQ9") not in events  # not administered: no row, not an incomplete one
    held = {
        r["screening_id"]: r["_q_rule_ids"]
        for r in query(settings, "select screening_id, _q_rule_ids from quarantine.screenings")
    }
    assert held == {"S2": "SCR-007", "S5": "SCR-008", "S6": "SCR-009", "S7": "SCR-005"}
    warned = query(settings, "select rule_id, row_num from audit.row_findings where action = 'warn'")
    assert warned == [{"rule_id": "SCR-012", "row_num": 4}]


def test_too_many_bad_rows_stop_the_whole_file_until_a_steward_accepts_it(small):
    settings, inbox = small
    _roster(inbox)
    rows = [_screen(f"S{i}") for i in range(10)]
    rows[3]["phq9_q1"] = "9"
    rows[7]["phq9_q2"] = "-1"
    write_file(inbox / "2026-02-16", "screenings_20260216.csv", SCREEN, rows)
    run_pipeline(settings, DAY, feeds=["roster", "screenings"], downstream=False)
    load = query(settings, "select load_id, status, blocked_rule from audit.loads where feed = 'screenings'")[0]
    assert (load["status"], load["blocked_rule"]) == ("blocked", "SCR-007")
    gate = query(settings, "select decision from audit.gate_decisions where feed = 'screenings'")[0]["decision"]
    assert gate == "HOLD"

    # The steward confirms the file with the platform and accepts it: it loads on the next run, with the
    # two bad rows held one by one and the stop recorded as overridden.
    ws, box = str(settings.workspace), str(settings.inbox)
    assert cli(["--workspace", ws, "--inbox", box, "accept", load["load_id"], "--reason", "confirmed"]) == 0
    run_pipeline(settings, date(2026, 2, 17), feeds=["roster", "screenings"], downstream=False)
    published = query(settings, "select distinct screening_id from published.screening_events order by 1")
    assert [r["screening_id"] for r in published] == [f"S{i}" for i in range(10) if i not in (3, 7)]
    held = query(settings, "select screening_id, _q_status from quarantine.screenings order by 1")
    assert held == [{"screening_id": "S3", "_q_status": "held"}, {"screening_id": "S7", "_q_status": "held"}]
    outcomes = query(settings, "select outcome from audit.rule_results where rule_id = 'SCR-007' order by run_id")
    assert [r["outcome"] for r in outcomes] == ["blocked", "overridden"]


def test_unregistered_layout_is_stopped_explained_and_can_be_waived(small):
    settings, inbox = small
    _roster(inbox)
    write_file(
        inbox / "2026-02-16",
        "labs_20260216.csv",
        LAB_V1 + ["Units"],
        [
            {
                "OrderID": "L1",
                "MRN": "00000101",
                "Collected": "2026-02-01",
                "LOINC": "4548-4",
                "Result": "7.0",
                "Units": "%",
            }
        ],
    )
    first = run_pipeline(settings, DAY, feeds=["roster", "labs"], downstream=False)
    load = query(settings, "select load_id, status, blocked_rule, status_reason from audit.loads where feed = 'labs'")[
        0
    ]
    assert (load["status"], load["blocked_rule"]) == ("blocked", "LAYOUT")
    assert "unexpected: Units" in load["status_reason"]
    assert (first.evidence_dir / "shape_diff_labs_20260216.csv.md").exists()

    ws, box = str(settings.workspace), str(settings.inbox)
    assert cli(["--workspace", ws, "--inbox", box, "waive", load["load_id"], "--reason", "test file"]) == 0
    write_file(
        inbox / "2026-02-23",
        "labs_20260223.csv",
        LAB_V1,
        [{"OrderID": "L2", "MRN": "00000101", "Collected": "2026-02-18", "LOINC": "4548-4", "Result": "7.1"}],
    )
    run_pipeline(settings, date(2026, 2, 23), feeds=["roster", "labs"], downstream=False)
    assert [r["lab_result_id"] for r in query(settings, "select lab_result_id from published.lab_results")] == ["L2"]


def test_a_byte_for_byte_resend_is_not_loaded_twice(small):
    settings, inbox = small
    _roster(inbox)
    path = write_file(
        inbox / "2026-02-16",
        "labs_20260216.csv",
        LAB_V1,
        [{"OrderID": "L1", "MRN": "00000101", "Collected": "2026-02-01", "LOINC": "4548-4", "Result": "7.0"}],
    )
    (inbox / "2026-02-23").mkdir()
    shutil.copyfile(path, inbox / "2026-02-23" / "labs_20260223.csv")
    run_pipeline(settings, DAY, feeds=["roster", "labs"], downstream=False)
    run_pipeline(settings, date(2026, 2, 23), feeds=["roster", "labs"], downstream=False)
    files = {
        r["name"]: r["status"] for r in query(settings, "select name, status from audit.files where feed = 'labs'")
    }
    assert files == {"labs_20260216.csv": "loaded", "labs_20260223.csv": "duplicate"}


def test_a_code_mapping_added_later_releases_held_rows(small, tmp_path):
    settings, inbox = small
    config = tmp_path / "config"
    shutil.copytree(settings.config_dir, config)
    settings = load_settings(workspace=settings.workspace, config_dir=config, inbox=inbox)
    _roster(inbox)
    write_file(
        inbox / "2026-02-16",
        "appointments_main_20260216.csv",
        APPT,
        [
            {
                "APPT_ID": "A1",
                "MRN": "00000101",
                "SITE": "S03",
                "APPT_DATE": "2026-02-02",
                "VISIT_TYPE": "OFFICE",
                "STATUS": "LWBS",
            },
            {
                "APPT_ID": "A2",
                "MRN": "00000102",
                "SITE": "S03",
                "APPT_DATE": "2026-02-03",
                "VISIT_TYPE": "OFFICE",
                "STATUS": "KEPT",
            },
        ],
    )
    run_pipeline(settings, DAY, feeds=["roster", "appointments"], downstream=False)
    held = query(settings, "select appointment_id, _q_status, _q_retryable from quarantine.appointments")
    assert held == [{"appointment_id": "A1", "_q_status": "held", "_q_retryable": True}]

    # Nothing new arrives, and nothing changed: the next run has no work for the feed.
    quiet = run_pipeline(settings, date(2026, 2, 17), feeds=["roster", "appointments"], downstream=False)
    assert quiet.dag("feed_appointments").tasks["wait_for_delivery"].status == "skipped"

    # The steward maps the code. Still no new file, yet the next run re-checks the held row and releases it.
    maps_path = config / "reference" / "code_maps.yaml"
    maps = yaml.safe_load(maps_path.read_text(encoding="utf-8"))
    maps["appointment_status"]["no_show"].append("LWBS")
    maps_path.write_text(yaml.safe_dump(maps), encoding="utf-8")
    run_pipeline(settings, date(2026, 2, 18), feeds=["roster", "appointments"], downstream=False)
    status = {r["appointment_id"]: r["status"] for r in query(settings, "select * from published.appointments")}
    assert status == {"A1": "no_show", "A2": "completed"}
    assert query(settings, "select _q_status from quarantine.appointments") == [{"_q_status": "released"}]


def test_a_waiver_lets_the_feed_publish_on_the_next_run(small):
    settings, inbox = small
    _roster(inbox)
    lab = {"MRN": "00000101", "Collected": "2026-02-05", "LOINC": "4548-4", "Result": "7.0"}
    write_file(inbox / "2026-02-16", "labs_20260209.csv", LAB_V1 + ["Units"], [{"OrderID": "L0", **lab}])
    write_file(inbox / "2026-02-16", "labs_20260216.csv", LAB_V1, [{"OrderID": "L1", **lab}])
    run_pipeline(settings, DAY, feeds=["roster", "labs"], downstream=False)
    loads = {r["name"]: r for r in query(settings, "select name, load_id, status from audit.loads")}
    assert (loads["labs_20260209.csv"]["status"], loads["labs_20260216.csv"]["status"]) == ("blocked", "merged")

    ws, box = str(settings.workspace), str(settings.inbox)
    assert (
        cli(["--workspace", ws, "--inbox", box, "waive", loads["labs_20260209.csv"]["load_id"], "--reason", "t"]) == 0
    )
    run_pipeline(settings, date(2026, 2, 17), feeds=["roster", "labs"], downstream=False)  # no new file
    assert [r["lab_result_id"] for r in query(settings, "select lab_result_id from published.lab_results")] == ["L1"]


def test_a_run_that_stopped_part_way_resumes_cleanly(small):
    settings, inbox = small
    _roster(inbox)
    rows = [_lab(f"A{i}", "00000101", "2026-02-02", "4548-4", "6.5", "%") for i in range(4)]
    rows.append(_lab("A9", "00000999", "2026-02-02", "4548-4", "6.1", "%"))  # not on the roster: held, retryable
    write_file(inbox / "2026-02-16", "labs_20260216.csv", LAB_V2, rows)
    run_pipeline(settings, DAY, feeds=["roster"], downstream=False)

    # A run stops after validating the rows, before merging them.
    ws, box = str(settings.workspace), str(settings.inbox)
    for task in ("land_to_lake", "load_raw", "validate_files", "stage", "recheck_quarantine", "validate_rows"):
        assert cli(["--workspace", ws, "--inbox", box, "task", "feed_labs", task, "--as-of", "2026-02-16"]) == 0
    assert query(settings, "select status from audit.loads where feed = 'labs'") == [{"status": "validated"}]

    # The next run finishes the load: every row once, the held row held once, the counts reconciled.
    run_pipeline(settings, DAY, feeds=["roster", "labs"], downstream=False)
    load = query(settings, "select * from audit.loads where feed = 'labs'")[0]
    assert load["status"] == "published"
    assert (load["rows_staged"], load["rows_passed"], load["rows_quarantined"], load["rows_inserted"]) == (5, 4, 1, 4)
    held = query(settings, "select lab_result_id, _q_status from quarantine.labs")
    assert held == [{"lab_result_id": "A9", "_q_status": "held"}]
    assert len(query(settings, "select * from published.lab_results")) == 4
