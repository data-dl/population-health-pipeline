"""Shared fixtures. The whole five-run scenario runs once per test session (about a minute and a half)
and every end-to-end test reads what it left behind. Unit tests build their own small inboxes."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pytest

from pophealth.demo import run_scenario
from pophealth.pipeline import RunResult
from pophealth.settings import Settings, load_settings
from synth.generate import generate
from synth.world import World, build_world


@dataclass
class Scenario:
    settings: Settings
    results: list[RunResult]
    key: dict
    root: Path

    def rows(self, sql: str, params: list | None = None) -> list[dict]:
        con = duckdb.connect(str(self.settings.warehouse_path), read_only=True)
        try:
            cur = con.execute(sql, params or [])
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()

    def scalar(self, sql: str, params: list | None = None):
        rows = self.rows(sql, params)
        return next(iter(rows[0].values())) if rows else None

    def run(self, n: int) -> RunResult:
        """The n-th run of the scenario, counting from 1."""
        return self.results[n - 1]


@pytest.fixture(scope="session")
def scenario(tmp_path_factory) -> Scenario:
    root = tmp_path_factory.mktemp("scenario")
    key = generate(root / "demo")
    settings = load_settings(workspace=root, inbox=root / "demo" / "inbox")
    results = [r for r, _ in run_scenario(settings, quiet=True)]
    return Scenario(settings, results, key, root)


@pytest.fixture(scope="session")
def world() -> World:
    return build_world()


# ------------------------------------------------------------------------------------------------ small inboxes

ROSTER_HEADER = [
    "MRN",
    "FIRST_NAME",
    "LAST_NAME",
    "DOB",
    "SEX",
    "RACE",
    "ETHNICITY",
    "PREF_LANGUAGE",
    "ADDRESS1",
    "CITY",
    "ZIP",
    "PHONE",
    "EMAIL",
    "PAYER_TYPE",
    "SITE_CODE",
    "PCP",
    "CARE_MANAGER",
    "PROBLEM_LIST",
    "REG_DATE",
]


def roster_row(mrn: str, **overrides) -> dict[str, str]:
    row = {
        "MRN": mrn,
        "FIRST_NAME": "Test",
        "LAST_NAME": f"Person{mrn[-3:]}",
        "DOB": "1970-05-06",
        "SEX": "F",
        "RACE": "White",
        "ETHNICITY": "Not Hispanic or Latino",
        "PREF_LANGUAGE": "en",
        "ADDRESS1": "1 Oak St",
        "CITY": "Riverton",
        "ZIP": "70201",
        "PHONE": "555-234-5678",
        "EMAIL": "",
        "PAYER_TYPE": "COMMERCIAL",
        "SITE_CODE": "S03",
        "PCP": "Pat Doctor",
        "CARE_MANAGER": "Cam Manager",
        "PROBLEM_LIST": "E11.9",
        "REG_DATE": "2015-01-01",
    }
    row.update(overrides)
    return row


def write_file(folder: Path, name: str, header: list[str], rows: list[dict[str, str]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        for r in rows:
            writer.writerow([r.get(h, "") for h in header])
    return path


@pytest.fixture
def small(tmp_path) -> tuple[Settings, Path]:
    """A workspace with an empty inbox; tests drop files into inbox/<arrival date>/."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return load_settings(workspace=tmp_path, inbox=inbox), inbox


def query(settings: Settings, sql: str, params: list | None = None) -> list[dict]:
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        cur = con.execute(sql, params or [])
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


DAY = date(2026, 2, 16)
