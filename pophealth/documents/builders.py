"""Builders for the document collections in config/collections.yaml.

Each builder receives a DuckDB connection (its own cursor when run in a worker thread), the clinics
to build, and the run context, and returns plain dicts. Flat warehouse rows are pulled once per
partition and nested in Python; blanks stay null, dates become ISO strings, decimals become numbers.

`PHI_PATHS` lists every document path that carries a patient identifier and the contract column it
comes from. The de-identification verifier checks that each policy transforms every one of them, so
adding an identifier to a document without covering it in a policy fails the build.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

PHI_PATHS = {
    "patients": {
        "_id": ("roster", "patient_id"),
        "demographics.firstName": ("roster", "first_name"),
        "demographics.lastName": ("roster", "last_name"),
        "demographics.birthDate": ("roster", "birth_date"),
        "demographics.registeredOn": ("roster", "registration_date"),
        "demographics.address.line1": ("roster", "address_line1"),
        "demographics.address.zip": ("roster", "zip"),
        "demographics.phone": ("roster", "phone"),
        "demographics.email": ("roster", "email"),
        "screenings.*.latest.date": ("screenings", "screen_date"),
        "screenings.*.history[].date": ("screenings", "screen_date"),
        "labs.*.latest.date": ("labs", "collected_date"),
        "labs.*.history[].date": ("labs", "collected_date"),
        "medications[].indexDate": ("pharmacy", "fill_date"),
        "appointments.lastVisit": ("appointments", "appt_date"),
        "careGaps[].detail": ("appointments", "appt_date"),
    },
    "worklists": {
        "patients[].patientId": ("roster", "patient_id"),
    },
}


def _plain(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [{k: _plain(v) for k, v in zip(names, r, strict=True)} for r in cur.fetchall()]


def _by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r[key]].append(r)
    return grouped


def _in_sites(sites: list[str]) -> tuple[str, list[str]]:
    return "(" + ", ".join("?" for _ in sites) + ")", list(sites)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_patients(con, sites: list[str], ctx: dict) -> list[dict]:
    marks, params = _in_sites(sites)
    scope = f"patient_id in (select patient_id from published.patients where site_id in {marks})"
    patients = _rows(
        con,
        f"""
        select p.*, s.site_name from published.patients p join ref.sites s using (site_id)
        where p.site_id in {marks} order by p.patient_id""",
        params,
    )
    conditions = _by(
        _rows(con, f"select * from published.patient_conditions where {scope} order by patient_id, icd10_code", params),
        "patient_id",
    )
    screenings = _by(
        _rows(
            con,
            f"select * from published.screening_events where {scope} "
            f"order by patient_id, instrument, screen_date, screening_id",
            params,
        ),
        "patient_id",
    )
    labs = _by(
        _rows(
            con,
            f"select * from published.lab_results where {scope} "
            f"order by patient_id, analyte, collected_date, lab_result_id",
            params,
        ),
        "patient_id",
    )
    adherence = _by(
        _rows(con, f"select * from dbt_intermediate.int_pdc where {scope} order by patient_id, drug_class", params),
        "patient_id",
    )
    windows = {r["patient_id"]: r for r in _rows(con, f"select * from marts.appointment_windows where {scope}", params)}
    gaps = _by(
        _rows(con, f"select * from marts.care_gaps where {scope} order by patient_id, gap", params), "patient_id"
    )

    docs = []
    for p in patients:
        pid = p["patient_id"]
        screens: dict[str, dict] = {}
        for s in screenings.get(pid, []):
            entry = {
                "date": s["screen_date"],
                "complete": s["complete"],
                "score": s["total_score"],
                "band": s["severity_band"],
                "positive": s["positive"],
            }
            block = screens.setdefault(s["instrument"], {"latest": None, "history": []})
            block["history"].append(entry)
            if s["is_most_recent"]:
                block["latest"] = entry
        results: dict[str, dict] = {}
        for x in labs.get(pid, []):
            entry = {"date": x["collected_date"], "value": x["value"], "censor": x["censor"]}
            block = results.setdefault(x["analyte"], {"unit": x["unit"], "latest": None, "history": []})
            block["history"].append(entry)
            if x["is_most_recent"]:
                block["latest"] = entry
        w = windows.get(pid, {})
        docs.append(
            {
                "_id": pid,
                "site": {"id": p["site_id"], "name": p["site_name"]},
                "demographics": {
                    "firstName": p["first_name"],
                    "lastName": p["last_name"],
                    "birthDate": p["birth_date"],
                    "sex": p["sex"],
                    "raceEthnicity": p["race_ethnicity"],
                    "language": p["language"],
                    "languageGroup": p["language_group"],
                    "payerType": p["payer_type"],
                    "address": {"line1": p["address_line1"], "city": p["city"], "zip": p["zip"]},
                    "phone": p["phone"],
                    "email": p["email"],
                    "registeredOn": p["registration_date"],
                },
                "careTeam": [
                    {"role": "primary_care", "name": p["pcp_name"]},
                    {"role": "care_manager", "name": p["care_manager_name"]},
                ],
                "conditions": [
                    {"code": c["icd10_code"], "group": c["condition_group"]} for c in conditions.get(pid, [])
                ],
                "screenings": screens,
                "labs": results,
                "medications": [
                    {
                        "drugClass": a["drug_class"],
                        "indexDate": a["index_date"],
                        "pdc": a["pdc"],
                        "daysCovered": a["days_covered"],
                        "periodDays": a["period_days"],
                        "adherent": a["adherent"],
                    }
                    for a in adherence.get(pid, [])
                ],
                "appointments": {
                    "keptRate30": w.get("kept_rate_30"),
                    "keptRate90": w.get("kept_rate_90"),
                    "keptRate180": w.get("kept_rate_180"),
                    "noShows180": w.get("no_shows_180"),
                    "lastVisit": w.get("last_completed_visit"),
                    "frequentNoShow": w.get("frequent_no_show"),
                },
                "careGaps": [{"gap": g["gap"], "detail": g["detail"]} for g in gaps.get(pid, [])],
                "meta": {"asOf": ctx["as_of"]},
            }
        )
    return docs


def build_clinics(con, sites: list[str], ctx: dict) -> list[dict]:
    marks, params = _in_sites(sites)
    clinics = _rows(con, f"select * from ref.sites where site_id in {marks} order by site_id", params)
    panel = {
        r["site_id"]: r
        for r in _rows(
            con,
            f"""
        select site_id, count(*) as patients, count(*) filter (where active_my) as active
        from dbt_intermediate.int_patient_facts where site_id in {marks} group by 1""",
            params,
        )
    }
    measures = _by(
        _rows(con, f"select * from marts.measure_summary where site_id in {marks} order by measure_id", params),
        "site_id",
    )
    gaps = _by(
        _rows(
            con,
            f"select site_id, gap, count(*) as n from marts.care_gaps where site_id in {marks} "
            f"group by 1, 2 order by 1, 2",
            params,
        ),
        "site_id",
    )
    managers = _by(
        _rows(
            con,
            f"select distinct site_id, care_manager_name from published.patients "
            f"where site_id in {marks} order by 1, 2",
            params,
        ),
        "site_id",
    )
    return [
        {
            "_id": c["site_id"],
            "name": c["site_name"],
            "region": c["region"],
            "panel": {
                "patients": panel.get(c["site_id"], {}).get("patients", 0),
                "activeThisYear": panel.get(c["site_id"], {}).get("active", 0),
            },
            "measures": [
                {
                    "measureId": m["measure_id"],
                    "name": m["measure_name"],
                    "direction": m["direction"],
                    "numerator": m["numerator"],
                    "denominator": m["denominator"],
                    "rate": m["rate"],
                }
                for m in measures.get(c["site_id"], [])
            ],
            "openGaps": {g["gap"]: g["n"] for g in gaps.get(c["site_id"], [])},
            "careManagers": [m["care_manager_name"] for m in managers.get(c["site_id"], [])],
            "meta": {"asOf": ctx["as_of"]},
        }
        for c in clinics
    ]


def build_worklists(con, sites: list[str], ctx: dict) -> list[dict]:
    marks, params = _in_sites(sites)
    rows = _rows(
        con,
        f"""
        select g.care_manager_name, g.site_id, g.patient_id, g.gap
        from marts.care_gaps g where g.site_id in {marks}
        order by g.care_manager_name, g.patient_id, g.gap""",
        params,
    )
    lists: dict[str, dict] = {}
    for r in rows:
        doc = lists.setdefault(
            r["care_manager_name"],
            {
                "_id": slug(r["care_manager_name"]),
                "careManager": r["care_manager_name"],
                "sites": [],  # usually one; a patient who moves clinic keeps their care manager
                "patients": [],
                "openGaps": 0,
                "meta": {"asOf": ctx["as_of"]},
            },
        )
        if r["site_id"] not in doc["sites"]:
            doc["sites"].append(r["site_id"])
        if not doc["patients"] or doc["patients"][-1]["patientId"] != r["patient_id"]:
            doc["patients"].append({"patientId": r["patient_id"], "gaps": []})
        doc["patients"][-1]["gaps"].append(r["gap"])
        doc["openGaps"] += 1
    for doc in lists.values():
        doc["sites"].sort()
    return list(lists.values())
