"""Reference data from config/reference, loaded into the warehouse's `ref` schema.

Reference data lives in version control, reviewed like code. Every run compares its content hash with
the warehouse copy's and reloads it when they differ; the new hash also tells each feed to re-check the
rows it holds for an unmapped code.
"""

from __future__ import annotations

import csv
import hashlib

from .settings import Settings
from .warehouse import Warehouse


def reference_hash(settings: Settings) -> str:
    digest = hashlib.sha256()
    for path in sorted((settings.config_dir / "reference").glob("*")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def _replace(wh: Warehouse, table: str, columns: list[str], rows: list[tuple]) -> None:
    wh.execute(f"create or replace table ref.{table} ({', '.join(columns)})")
    if rows:
        width = len(rows[0])
        values = ", ".join("(" + ", ".join("?" for _ in range(width)) + ")" for _ in rows)
        wh.execute(f"insert into ref.{table} values {values}", [v for row in rows for v in row])


def load_reference(wh: Warehouse, settings: Settings) -> str:
    """Refresh ref.* when config/reference has changed since the last load; return its hash."""
    digest = reference_hash(settings)
    if wh.table_exists("ref", "meta") and wh.scalar("select reference_hash from ref.meta") == digest:
        return digest
    with wh.transaction():
        _load(wh, settings)
        wh.execute("create or replace table ref.meta as select ? as reference_hash, now() as loaded_at", [digest])
    return digest


def _load(wh: Warehouse, settings: Settings) -> None:
    ref_dir = settings.config_dir / "reference"
    with open(ref_dir / "sites.csv", encoding="utf-8", newline="") as f:
        sites = list(csv.DictReader(f))
    _replace(
        wh,
        "sites",
        ["site_id varchar", "site_name varchar", "region varchar", "zip3 varchar", "city varchar"],
        [(s["site_id"], s["site_name"], s["region"], s["zip3"], s["city"]) for s in sites],
    )

    maps = settings.reference("code_maps.yaml")
    code_rows = [
        (name, raw.strip().upper(), canonical)
        for name, targets in maps.items()
        for canonical, raws in targets.items()
        for raw in raws
    ]
    _replace(wh, "code_map", ["map_name varchar", "raw_value varchar", "canonical varchar"], code_rows)

    labs = settings.reference("lab_tests.yaml")["analytes"]
    _replace(
        wh,
        "analytes",
        [
            "analyte varchar",
            "label varchar",
            "standard_unit varchar",
            "plausible_min decimal(10,2)",
            "plausible_max decimal(10,2)",
        ],
        [
            (a, spec["label"], spec["standard_unit"], spec["plausible_min"], spec["plausible_max"])
            for a, spec in labs.items()
        ],
    )
    _replace(
        wh,
        "lab_codes",
        ["loinc_code varchar", "analyte varchar", "default_unit varchar"],
        [(code, a, c["default_unit"]) for a, spec in labs.items() for code, c in spec["codes"].items()],
    )
    _replace(
        wh,
        "lab_units",
        ["analyte varchar", "unit varchar", "convert varchar"],
        [(a, unit, u["convert"]) for a, spec in labs.items() for unit, u in spec["units"].items()],
    )

    drugs = settings.reference("drugs.yaml")["classes"]
    _replace(
        wh,
        "drugs",
        ["drug_name varchar", "drug_class varchar", "class_label varchar"],
        [(d, cls, spec["label"]) for cls, spec in drugs.items() for d in spec["drugs"]],
    )

    groups = settings.reference("conditions.yaml")["groups"]
    _replace(
        wh,
        "condition_prefixes",
        ["prefix varchar", "condition_group varchar", "label varchar"],
        [(p, g, spec["label"]) for g, spec in groups.items() for p in spec["icd10_prefixes"]],
    )

    instruments = settings.reference("instruments.yaml")["instruments"]
    _replace(
        wh,
        "instruments",
        [
            "instrument varchar",
            "label varchar",
            "loinc_total varchar",
            "kind varchar",
            "n_items integer",
            "positive_at integer",
        ],
        [
            (i, s["label"], s.get("loinc_total"), s["kind"], len(s["items"]), s.get("positive_at"))
            for i, s in instruments.items()
        ],
    )
    _replace(
        wh,
        "instrument_bands",
        ["instrument varchar", "min_score integer", "max_score integer", "band varchar"],
        [(i, b["min"], b["max"], b["band"]) for i, s in instruments.items() for b in s.get("bands", [])],
    )

    value_sets = [("site_id", s["site_id"]) for s in sites]
    value_sets += [("loinc_code", code) for a, spec in labs.items() for code in spec["codes"]]
    value_sets += [("drug_name", d) for spec in drugs.values() for d in spec["drugs"]]
    _replace(wh, "value_sets", ["domain varchar", "value varchar"], value_sets)
