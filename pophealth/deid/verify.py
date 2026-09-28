"""The leak gate for de-identified outputs. Nothing is released unless every check passes.

Checks, in plain terms:
- no real record number, full name, e-mail, phone number or street address appears anywhere in the
  demo documents or the extract (searched as text, not only in the fields expected to hold them)
- every demo patient differs from its real patient in name, birth date, ZIP and contact details, and
  its dates moved by one consistent shift (intervals survive)
- the demo policy transforms every document path registered as carrying an identifier
- relationships still resolve: patients to clinics, worklists to patients
- the extract has only allowed columns, no age above 89, no birth year for anyone 90 or older, and
  restricted ZIP3 prefixes replaced
Group sizes on the extract's quasi-identifiers are reported alongside (Safe Harbor does not require
a minimum, but the report shows how identifiable combinations are).
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date

from ..documents.builders import PHI_PATHS
from ..documents.docstore import DocumentStore
from ..settings import Settings
from ..warehouse import Warehouse
from .engine import RealValues, load_policy, street_key

EIGHT_DIGITS = re.compile(r"(?<![0-9])[0-9]{8}(?![0-9])")
TEN_DIGITS = re.compile(r"(?<![0-9])[0-9]{10}(?![0-9])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _strings(node) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for v in node.values():
            out += _strings(v)
    elif isinstance(node, list):
        for v in node:
            out += _strings(v)
    elif isinstance(node, str):
        out.append(node)
    return out


def _text_leaks(texts: list[str], real: RealValues) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {
        "record number": set(),
        "full name": set(),
        "e-mail": set(),
        "phone": set(),
        "street address": set(),
    }
    street_lengths = {len(s.split()) for s in real.streets}
    for text in texts:
        found["record number"] |= set(EIGHT_DIGITS.findall(text)) & real.mrns
        found["phone"] |= set(TEN_DIGITS.findall(text)) & real.phones
        found["e-mail"] |= {e.lower() for e in EMAIL.findall(text)} & real.emails
        tokens = street_key(text).split()
        for n in street_lengths:  # every run of n words, so an address inside a note is found too
            found["street address"] |= {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)} & real.streets
        words = [w.lower() for w in WORD.findall(text)]
        found["full name"] |= {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)} & real.full_names
    return found


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"check": name, "passed": bool(passed), "detail": detail}


def verify(settings: Settings) -> dict:
    policy = load_policy(settings, settings.config["deidentification"]["demo_policy"])
    extract_policy = load_policy(settings, settings.config["deidentification"]["extract_policy"])
    demo = DocumentStore(settings.docstore, "demo_staging")
    extract_path = settings.extracts / "staging" / "safe_harbor_patients.csv"
    checks: list[dict] = []
    with Warehouse(settings.warehouse_path) as wh:
        real = RealValues.load(wh)
        entries = wh.rows("select kind, real_value, surrogate from restricted.deid_crosswalk")
    crosswalk = {(r["kind"], r["surrogate"]): r["real_value"] for r in entries}
    by_real = {(r["kind"], r["real_value"]): r["surrogate"] for r in entries}

    patients, clinics, worklists = demo.find("patients"), demo.find("clinics"), demo.find("worklists")
    texts = [s for doc in patients + clinics + worklists for s in _strings(doc)] + [
        str(d["_id"]) for d in patients + clinics + worklists
    ]
    rows = []
    if extract_path.exists():
        with open(extract_path, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        texts += [v for r in rows for v in r.values() if v]
    leaks = _text_leaks(texts, real)
    for kind, values in leaks.items():
        checks.append(
            _check(
                f"no real {kind} anywhere",
                not values,
                f"{len(values)} found" if values else f"searched {len(texts):,} strings",
            )
        )

    mismatched, unshifted = [], []
    for doc in patients:
        real_id = crosswalk.get(("patient", doc["_id"]))
        p = real.by_patient.get(real_id)
        if p is None:
            mismatched.append(f"{doc['_id']}: no crosswalk entry")
            continue
        d = doc["demographics"]
        same = [
            f
            for f, a, b in (
                ("name", f"{d['firstName']} {d['lastName']}".lower(), f"{p['first_name']} {p['last_name']}".lower()),
                ("birth date", d["birthDate"], p["birth_date"].isoformat()),
                ("zip", d["address"]["zip"], p["zip"]),
                ("phone", d["phone"], p["phone"]),
                ("street", d["address"]["line1"], p["address_line1"]),
            )
            if a and a == b
        ]
        if same:
            mismatched.append(f"{doc['_id']}: {', '.join(same)} unchanged")
        shift = int(by_real.get(("date_shift", real_id)) or 0)
        if not shift or date.fromisoformat(d["birthDate"]).toordinal() - p["birth_date"].toordinal() != shift:
            unshifted.append(doc["_id"])
    checks.append(
        _check(
            "every demo patient differs from the real one",
            not mismatched,
            "; ".join(mismatched[:5]) or f"{len(patients):,} patients compared",
        )
    )
    checks.append(
        _check(
            "dates moved by one consistent, non-zero shift",
            not unshifted,
            f"{len(unshifted)} inconsistent" if unshifted else "birth dates checked against shifts",
        )
    )

    identifier_classes = {"direct_identifier"}
    uncovered = []
    for collection, paths in PHI_PATHS.items():
        transforms = policy["collections"].get(collection, {}).get("transforms", {})
        for path, (feed, column) in paths.items():
            cls = settings.contracts[feed].classification(column)
            if cls is None:
                uncovered.append(f"{collection}.{path} ({feed}.{column} is not classified in its contract)")
            elif cls in identifier_classes and transforms.get(path, "keep") == "keep":
                uncovered.append(f"{collection}.{path}")
    checks.append(
        _check(
            "policy transforms every registered identifier path",
            not uncovered,
            ", ".join(uncovered) or f"{sum(len(p) for p in PHI_PATHS.values())} paths covered",
        )
    )

    clinic_ids = {c["_id"] for c in clinics}
    patient_ids = {p["_id"] for p in patients}
    dangling = [p["_id"] for p in patients if p["site"]["id"] not in clinic_ids]
    dangling += [x["patientId"] for w in worklists for x in w["patients"] if x["patientId"] not in patient_ids]
    checks.append(
        _check(
            "patients point at clinics, worklists at patients",
            not dangling,
            f"{len(dangling)} dangling references" if dangling else "all references resolve",
        )
    )

    allowed = set(extract_policy["allowed_columns"])
    extra_cols = [
        c
        for c in (rows[0].keys() if rows else [])
        if c not in allowed and not re.fullmatch(r"[a-z0-9_]+_(eligible|met)", c)
    ]
    old = [r["record_id"] for r in rows if r["age_at_year_end"] != "90+" and int(r["age_at_year_end"]) > 89]
    old += [r["record_id"] for r in rows if r["age_at_year_end"] == "90+" and r["birth_year"]]
    bad_zip = [r["record_id"] for r in rows if r["zip3"] in set(extract_policy.get("restricted_zip3", []))]
    checks.append(_check("extract carries only allowed columns", not extra_cols, ", ".join(extra_cols) or "ok"))
    checks.append(_check("no age above 89 and no birth year for 90+", not old, f"{len(old)} rows" if old else "ok"))
    checks.append(
        _check("restricted ZIP3 prefixes replaced by 000", not bad_zip, f"{len(bad_zip)} rows" if bad_zip else "ok")
    )

    group_sizes = []
    min_group = int(settings.config["deidentification"].get("min_group_size", 11))
    for qis in extract_policy.get("quasi_identifiers", []):
        sizes = Counter(tuple(r[c] for c in qis) for r in rows)
        small = sum(n for n in sizes.values() if n < min_group)
        group_sizes.append(
            {
                "quasi_identifiers": qis,
                "groups": len(sizes),
                "smallest_group": min(sizes.values()) if sizes else 0,
                "records_in_groups_below": small,
                "threshold": min_group,
                "share_below": round(small / max(1, len(rows)), 3),
            }
        )
    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "group_sizes": group_sizes,
        "documents": {"patients": len(patients), "clinics": len(clinics), "worklists": len(worklists)},
        "extract_rows": len(rows),
    }


def write_report(report: dict, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
