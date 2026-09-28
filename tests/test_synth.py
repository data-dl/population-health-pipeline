"""The generator's contract: deterministic, byte-stable, LF-only, and every planted defect is really in
the file at the row the answer key names."""

from __future__ import annotations

import csv
import filecmp
import json
from datetime import date
from pathlib import Path

from synth.generate import generate
from synth.world import MY_END, build_world

REPO = Path(__file__).resolve().parent.parent


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_same_seed_same_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a)
    generate(b)
    left = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    right = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert left == right
    assert all(filecmp.cmp(a / p, b / p, shallow=False) for p in left)


def test_committed_samples_and_answer_key_are_current(tmp_path):
    generate(tmp_path)
    for rel in ["answer_key.json"] + [f"samples/{p.name}" for p in (REPO / "demo" / "samples").glob("*.csv")]:
        assert (tmp_path / rel).read_bytes() == (REPO / "demo" / rel).read_bytes(), rel


def test_files_use_lf_only(scenario):
    for path in (scenario.root / "demo").rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes(), path.name


def test_truth_is_clinically_valid():
    world = build_world()
    for s in world.screenings:
        for items, top in ((s.phq9, 3), (s.gad7, 3)):
            assert all(v is None or 0 <= v <= top for v in (items or []))
    assert all(0 < c.days_supply <= 90 for c in world.claims)
    assert len({p.patient_id for p in world.patients}) == len(world.patients)


def test_planted_defects_are_where_the_key_says(scenario):
    inbox = scenario.root / "demo" / "inbox"
    files = {f["name"]: inbox / f["arrival"] / f["name"] for f in scenario.key["files"]}
    cache: dict[str, list[dict]] = {}
    for p in scenario.key["planted"]:
        rows = cache.setdefault(p["file"], _read(files[p["file"]]))
        row = rows[p["row"] - 1]
        kind = p["kind"]
        if kind == "leading_zeros_lost":
            assert len(row["MRN"]) < 8
        elif kind == "lab_non_reportable":
            assert not row["Result"].replace(".", "").isdigit()
        elif kind == "roster_unknown_site":
            assert row["SITE_CODE"] == "S99"
        elif kind == "roster_site_lowercase":
            assert row["SITE_CODE"] != row["SITE_CODE"].upper()
        elif kind == "screening_future_date":
            month, day, year = (int(x) for x in row["screen_date"].split("/"))
            assert date(year, month, day) > date(2026, 2, 23)
        elif kind == "appointment_status_unmapped":
            assert row["STATUS"] == "LWBS"
        elif kind == "claim_days_supply_invalid":
            assert not 1 <= int(row["days_supply"]) <= 365
        elif kind in ("late_result_closed_year", "corrected_result"):
            collected = row.get("Collected") or row.get("collection_date")
            assert date.fromisoformat(collected) <= MY_END


def test_answer_key_is_complete():
    key = json.loads((REPO / "demo" / "answer_key.json").read_text(encoding="utf-8"))
    assert len(key["planted"]) > 250
    assert len({p["kind"] for p in key["planted"]}) >= 25
    assert {f["outcome"] for f in key["files"]} == {"loaded", "blocked"}
    assert set(key["measures"]) == {
        "DM_A1C_TESTED",
        "DM_A1C_CONTROLLED",
        "DM_A1C_POOR",
        "DEP_SCREEN",
        "DEP_FOLLOWUP",
        "PDC_DIABETES",
        "PDC_RAS",
        "PDC_STATIN",
        "SDOH_SCREEN",
    }
