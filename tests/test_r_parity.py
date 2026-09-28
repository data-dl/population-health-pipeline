"""The screening rules and scoring exist twice - SQL in the pipeline, data.table in r/score_screenings.R -
and must agree row for row on the first week's file (about 3,000 screenings, planted defects included).

Skipped when R is not installed, unless POPHEALTH_REQUIRE_R is set (CI sets it). RSCRIPT may name the
Rscript executable; a project-local R library in .rlib is used when present.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from pophealth.pipeline import run_pipeline
from pophealth.settings import load_settings
from synth.generate import generate

from .conftest import query

REPO = Path(__file__).resolve().parent.parent


def _flag(value: str):
    return {"TRUE": True, "FALSE": False, "": None}[value]


@pytest.fixture(scope="module")
def parity(tmp_path_factory):
    rscript = os.environ.get("RSCRIPT") or shutil.which("Rscript")
    if not rscript:
        if os.environ.get("POPHEALTH_REQUIRE_R"):
            pytest.fail("Rscript is required (POPHEALTH_REQUIRE_R is set) but was not found")
        pytest.skip("Rscript not found")
    root = tmp_path_factory.mktemp("rparity")
    generate(root / "demo")
    settings = load_settings(workspace=root, inbox=root / "demo" / "inbox")
    run_pipeline(settings, date(2026, 2, 16), feeds=["roster", "screenings"], downstream=False)
    env = dict(os.environ)
    if (REPO / ".rlib").exists():
        env["R_LIBS"] = str(REPO / ".rlib")
    source = root / "demo" / "inbox" / "2026-02-16" / "screenings_20260216.csv"
    out = root / "r_out"
    subprocess.run(
        [rscript, str(REPO / "r" / "score_screenings.R"), str(source), "2026-02-16", str(out), str(REPO / "config")],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    return settings, out


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_r_rejects_the_same_rows_for_the_same_reasons(parity):
    settings, out = parity
    python = {
        (r["row_num"], r["rule_id"])
        for r in query(
            settings,
            "select row_num, rule_id from audit.row_findings where feed = 'screenings' and candidate = 'new' "
            "and action = 'quarantine' and rule_id <> 'SCR-010'",
        )
    }
    in_r = {(int(r["row"]), r["rule"]) for r in _read(out / "rejects.csv")}
    assert python and in_r == python


def test_r_scores_match_the_warehouse(parity):
    settings, out = parity
    not_on_roster = {
        r["screening_id"]
        for r in query(
            settings,
            "select s.screening_id from staging.screenings s join audit.row_findings f "
            "on f.load_id = s._load_id and f.row_num = s._row_num where f.rule_id = 'SCR-010'",
        )
    }
    python = {
        (
            r["screening_id"],
            r["instrument"],
            r["items_answered"],
            r["complete"],
            r["total_score"],
            r["positive"],
            r["severity_band"],
        )
        for r in query(settings, "select * from published.screening_events")
    }
    in_r = {
        (
            r["screening_id"],
            r["instrument"],
            int(r["items_answered"]),
            _flag(r["complete"]),
            int(r["total_score"]) if r["total_score"] else None,
            _flag(r["positive"]),
            r["band"] or None,
        )
        for r in _read(out / "scores.csv")
        if r["screening_id"] not in not_on_roster
    }
    assert len(python) > 3000
    assert in_r == python
