"""Render the world into supplier files, one set per weekly extract, and plant the defects.

Every planted row carries its expectation (which rule must catch it and what must happen to it by
the end of the scenario). `synth.generate` turns those into the answer key after the rows have
their final positions in each file.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .world import EXTRACTS, MY_END, REDELIVERY_ARRIVAL, World, delivery_index, ngsp_to_ifcc

E0, E1, E2 = EXTRACTS


@dataclass
class Row:
    values: dict[str, str]
    expect: list[dict] = field(default_factory=list)
    key: str = ""


@dataclass
class DeliveryFile:
    feed: str
    source: str
    name: str
    arrival: date
    extract: date
    header: list[str]
    rows: list[Row]
    layout: int = 1
    outcome: str = "loaded"  # or "blocked"
    outcome_rule: str | None = None
    counts: dict[str, int] = field(default_factory=dict)  # expected merge counts: unchanged, updated, deduped


def iso(d: date) -> str:
    return d.isoformat()


def us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def stamp(d: date) -> str:
    return d.strftime("%Y%m%d")


def expect(kind: str, outcome: str, rule: str, final: str | None = None) -> dict:
    entry = {"kind": kind, "expect": outcome, "rule": rule}
    if final:
        entry["final"] = final
    return entry


class Renderer:
    def __init__(self, world: World, rng: random.Random):
        self.world = world
        self.rng = rng
        self.files: list[DeliveryFile] = []
        roster_ids = {p.patient_id for p in world.patients}
        self.orphan_ids = []
        while len(self.orphan_ids) < 12:
            candidate = f"{rng.randint(1_000_000, 99_999_999):08d}"
            if candidate not in roster_ids and candidate not in self.orphan_ids:
                self.orphan_ids.append(candidate)
        # Real, ordinary patients that fabricated error rows are attached to.
        self.plain = [p for p in world.patients if p.active and not p.tags]
        self.fabricated = 0

    def fake_id(self, prefix: str) -> str:
        self.fabricated += 1
        return f"{prefix}{9_900_000 + self.fabricated}"

    def insert_randomly(self, rows: list[Row], extra: list[Row]) -> None:
        for row in extra:
            rows.insert(self.rng.randint(0, len(rows)), row)

    def render(self) -> list[DeliveryFile]:
        self.roster()
        self.screenings()
        self.labs()
        self.appointments()
        self.pharmacy()
        return self.files

    # ------------------------------------------------------------------ roster

    def roster(self) -> None:
        header = [
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
        payer_raw = {
            "government": ["GOVERNMENT", "GOVT", "Public"],
            "commercial": ["COMMERCIAL", "Comm"],
            "marketplace": ["MARKETPLACE", "Exchange"],
            "self_pay": ["SELF PAY", "Self-Pay", "UNINSURED"],
        }
        lang_raw = {"en": ["en", "ENG", "English"], "es": ["es", "SPA", "Spanish"]}
        lowercase_site = set(p.patient_id for p in self.rng.sample(self.plain, 5))
        bad_phone = set(
            p.patient_id for p in self.rng.sample([p for p in self.plain if p.patient_id not in lowercase_site], 10)
        )
        for i, extract in enumerate(EXTRACTS):
            rows: list[Row] = []
            last_extract = i == len(EXTRACTS) - 1
            for p in sorted(self.world.patients, key=lambda q: q.patient_id):
                if p.roster_from > i:
                    continue
                k = int(p.patient_id)
                languages = lang_raw.get(p.language, [p.language.upper()])
                phone = p.phone_from_d2 if (p.phone_from_d2 and i >= 1) else p.phone
                fmt = k % 4
                phone_text = (
                    f"({phone[:3]}) {phone[3:6]}-{phone[6:]}",
                    f"{phone[:3]}-{phone[3:6]}-{phone[6:]}",
                    phone,
                    f"{phone[:3]}.{phone[3:6]}.{phone[6:]}",
                )[fmt]
                values = {
                    "MRN": p.patient_id,
                    "FIRST_NAME": p.first,
                    "LAST_NAME": p.last,
                    "DOB": iso(p.birth_date),
                    "SEX": {"F": "F", "M": "M", "X": "X", "U": "U"}[p.sex]
                    if k % 29
                    else {"F": "Female", "M": "Male", "X": "X", "U": "Unknown"}[p.sex],
                    "RACE": p.race,
                    "ETHNICITY": p.ethnicity,
                    "PREF_LANGUAGE": languages[k % len(languages)],
                    "ADDRESS1": p.street_from_d2 if (p.street_from_d2 and i >= 1) else p.street,
                    "CITY": p.city,
                    "ZIP": p.zip,
                    "PHONE": phone_text,
                    "EMAIL": p.email,
                    "PAYER_TYPE": payer_raw[p.payer][k % len(payer_raw[p.payer])],
                    "SITE_CODE": p.site_on(i),
                    "PCP": p.pcp,
                    "CARE_MANAGER": p.care_manager,
                    "PROBLEM_LIST": ";".join(p.problems),
                    "REG_DATE": iso(p.registration_date),
                }
                row = Row(values, key=p.patient_id)
                final = "held" if last_extract else "superseded"
                if "roster_unknown_site" in p.tags:
                    values["SITE_CODE"] = "S99"
                    row.expect.append(expect("roster_unknown_site", "quarantined", "ROS-008", final))
                if "roster_invalid_birth_date" in p.tags and i == 0:
                    values["DOB"] = f"{p.birth_date.year}-02-30"
                    row.expect.append(expect("roster_invalid_birth_date", "quarantined", "ROS-005", "superseded"))
                if p.patient_id in lowercase_site:
                    values["SITE_CODE"] = values["SITE_CODE"].lower()
                    row.expect.append(expect("roster_site_lowercase", "repaired", "ROS-007"))
                if p.patient_id in bad_phone:
                    values["PHONE"] = f"{phone[3:6]}-{phone[6:]}"
                    row.expect.append(expect("roster_phone_malformed", "warned", "ROS-011"))
                rows.append(row)
                if "roster_conflicting_duplicate" in p.tags and i < 2:
                    twin = dict(
                        values,
                        DOB=iso(p.birth_date.replace(year=p.birth_date.year + 1)),
                        FIRST_NAME=self.rng.choice(["Chris", "Alex", "Sam", "Jordan"]),
                    )
                    row.expect.append(expect("roster_conflicting_duplicate", "quarantined", "ROS-010", "superseded"))
                    rows.append(
                        Row(
                            twin,
                            [expect("roster_conflicting_duplicate", "quarantined", "ROS-010", "superseded")],
                            p.patient_id,
                        )
                    )
            self.files.append(
                DeliveryFile("roster", "ehr_roster", f"roster_{stamp(extract)}.csv", extract, extract, header, rows)
            )

    # ------------------------------------------------------------------ screenings

    def screenings(self) -> None:
        items = (
            [f"phq9_q{i}" for i in range(1, 10)] + ["phq9_total"] + [f"gad7_q{i}" for i in range(1, 8)] + ["gad7_total"]
        )
        domains = ["housing", "food", "transport", "utilities", "safety"]
        header = ["screening_id", "mrn", "screen_date", "admin_mode"] + items + [f"sdoh_{d}" for d in domains]
        late_reg = {p.patient_id for p in self.world.patients if "late_registration" in p.tags}

        def values_of(s, fmt) -> dict[str, str]:
            v = {
                "screening_id": s.screening_id,
                "mrn": s.patient_id,
                "screen_date": fmt(s.screen_date),
                "admin_mode": s.admin_mode_raw,
            }
            for i in range(9):
                v[f"phq9_q{i + 1}"] = "" if s.phq9 is None or s.phq9[i] is None else str(s.phq9[i])
            v["phq9_total"] = "" if s.phq9_reported is None else str(s.phq9_reported)
            for i in range(7):
                v[f"gad7_q{i + 1}"] = "" if s.gad7 is None or s.gad7[i] is None else str(s.gad7[i])
            v["gad7_total"] = "" if s.gad7_reported is None else str(s.gad7_reported)
            for d in domains:
                v[f"sdoh_{d}"] = "" if s.sdoh is None or s.sdoh[d] is None else s.sdoh[d]
            return v

        by_extract: dict[int, list] = {0: [], 1: [], 2: []}
        for s in sorted(self.world.screenings, key=lambda x: (x.screen_date, x.screening_id)):
            idx = delivery_index(s.screen_date)
            if idx is not None:
                by_extract[idx].append(s)

        mismatch = set(
            s.screening_id
            for s in self.rng.sample([s for s in by_extract[0] if s.phq9_complete and s.screen_date <= MY_END], 8)
        )
        resend = self.rng.sample(
            [s for s in by_extract[0] if s.screen_date > MY_END and s.patient_id not in late_reg], 10
        )
        for i, extract in enumerate(EXTRACTS):
            fmt = us if i == 1 else iso  # the platform's export setting changed for one week
            rows: list[Row] = []
            for s in by_extract[i]:
                v = values_of(s, fmt)
                row = Row(v, key=s.screening_id)
                if s.screening_id in mismatch:
                    v["phq9_total"] = str(s.phq9_reported + self.rng.choice([1, 2, -2]))
                    row.expect.append(expect("screening_total_mismatch", "warned", "SCR-012"))
                if s.patient_id in late_reg:
                    row.expect.append(expect("orphan_late_registration", "quarantined", "SCR-010", "released"))
                rows.append(row)
            extra: list[Row] = []
            f = DeliveryFile(
                "screenings", "screening_platform", f"screenings_{stamp(extract)}.csv", extract, extract, header, rows
            )
            if i == 0:
                for bad in ["4", "7", "-1", "2.5", "x", "9"]:
                    p = self.rng.choice(self.plain)
                    v = values_of(self._fake_screening(p, date(2025, self.rng.randint(2, 11), 14)), iso)
                    v[f"phq9_q{self.rng.randint(1, 9)}"] = bad
                    extra.append(
                        Row(
                            v,
                            [expect("screening_item_out_of_range", "quarantined", "SCR-007", "held")],
                            v["screening_id"],
                        )
                    )
                for _ in range(4):
                    p = self.rng.choice(self.plain)
                    v = values_of(self._fake_screening(p, date(2025, self.rng.randint(2, 11), 9)), iso)
                    v["admin_mode"] = "Kiosk"
                    extra.append(
                        Row(
                            v,
                            [expect("screening_admin_mode_unmapped", "quarantined", "SCR-009", "held")],
                            v["screening_id"],
                        )
                    )
                for orphan in self.orphan_ids[:3]:
                    s = self._fake_screening(self.plain[0], date(2025, 6, 17))
                    s.patient_id = orphan
                    v = values_of(s, iso)
                    extra.append(
                        Row(v, [expect("orphan_never_registered", "quarantined", "SCR-010", "held")], v["screening_id"])
                    )
                self.insert_randomly(rows, extra)
                # Exact duplicates: the platform re-sent five rows inside the same file.
                for row in self.rng.sample([r for r in rows if not r.expect], 5):
                    at = rows.index(row)
                    rows.insert(
                        at + 1,
                        Row(dict(row.values), [expect("screening_exact_duplicate", "deduped", "merge")], row.key),
                    )
                f.counts["deduped"] = 5
            if i == 1:
                for s in resend:
                    rows.append(Row(values_of(s, fmt), key=s.screening_id))
                f.counts["unchanged"] = len(resend)
                for _ in range(2):
                    p = self.rng.choice(self.plain)
                    v = values_of(self._fake_screening(p, extract + timedelta(days=self.rng.randint(2, 5))), fmt)
                    extra.append(
                        Row(v, [expect("screening_future_date", "quarantined", "SCR-006", "held")], v["screening_id"])
                    )
                self.insert_randomly(rows, extra)
                resent = {s.screening_id for s in resend}
                target = self.rng.choice([r for r in rows if not r.expect and r.key not in resent])
                clash = dict(target.values)
                old = clash["phq9_q1"]
                clash["phq9_q1"] = "3" if old != "3" else "2"
                if clash["phq9_total"] and old:  # a consistent second version: only the key collides
                    clash["phq9_total"] = str(int(clash["phq9_total"]) - int(old) + int(clash["phq9_q1"]))
                target.expect.append(expect("screening_key_conflict", "quarantined", "SCR-011", "held"))
                rows.insert(
                    rows.index(target) + 1,
                    Row(clash, [expect("screening_key_conflict", "quarantined", "SCR-011", "held")], target.key),
                )
            self.files.append(f)

    def _fake_screening(self, p, day: date):
        from .world import Screening

        return Screening(
            self.fake_id("SCR"),
            p.patient_id,
            day,
            "IN_PERSON",
            [self.rng.randint(0, 1) for _ in range(9)],
            [self.rng.randint(0, 1) for _ in range(7)],
            None,
            phq9_reported=None,
            gad7_reported=None,
        )

    # ------------------------------------------------------------------ labs

    def labs(self) -> None:
        v1_header = ["OrderID", "MRN", "Collected", "LOINC", "TestName", "Result", "RefRange", "AbnFlag"]
        v2_header = [
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
        late_reg = {p.patient_id for p in self.world.patients if "late_registration" in p.tags}
        names = {"HBA1C": "Hemoglobin A1c", "LDL": "LDL Cholesterol Calc"}

        def result_text(x, value: Decimal, unit: str) -> str:
            if x.censor:
                return f"{x.censor}{value}"
            if unit == "mmol/mol":
                return str(ngsp_to_ifcc(value))
            return str(value)

        def flag(x, value: Decimal) -> str:
            if x.analyte == "HBA1C":
                return "H" if value >= Decimal("5.7") else ""
            return "H" if value >= 100 else ""

        def row_v1(x, value: Decimal) -> dict[str, str]:
            return {
                "OrderID": x.lab_result_id,
                "MRN": x.patient_id,
                "Collected": iso(x.collected),
                "LOINC": x.loinc,
                "TestName": names[x.analyte],
                "Result": result_text(x, value, "%"),
                "RefRange": "4.0-5.6" if x.analyte == "HBA1C" else "0-99",
                "AbnFlag": flag(x, value),
            }

        def row_v2(x, value: Decimal) -> dict[str, str]:
            unit = x.report_unit
            ref = {"%": "4.0-5.6", "mmol/mol": "20-38", "mg/dL": "0-99"}[unit]
            return {
                "accession_id": x.lab_result_id,
                "patient_mrn": x.patient_id,
                "collection_date": iso(x.collected),
                "loinc_code": x.loinc,
                "test_description": names[x.analyte].upper(),
                "result_value": result_text(x, value, unit),
                "result_units": unit,
                "reference_range": ref,
                "abnormal_flag": flag(x, value),
                "specimen_id": f"SP{int(x.lab_result_id[3:]) * 7 % 10_000_000:07d}",
            }

        per_extract: dict[int, list[Row]] = {0: [], 1: [], 2: []}
        for x in sorted(self.world.labs, key=lambda y: (y.collected, y.lab_result_id)):
            idx = delivery_index(x.resulted_on)
            if idx is None:
                continue
            first_value = x.corrected_from if x.corrected_from is not None else x.value
            render = row_v2 if idx == 2 else row_v1
            row = Row(render(x, first_value), key=x.lab_result_id)
            if x.patient_id in late_reg:
                row.expect.append(expect("orphan_late_registration", "quarantined", "LAB-011", "released"))
            if idx >= 1 and x.collected <= MY_END:
                row.expect.append(expect("late_result_closed_year", "warned", "LAB-013"))
            per_extract[idx].append(row)
            if x.corrected_from is not None:
                fixed = Row(row_v1(x, x.value), [expect("corrected_result", "warned", "LAB-013")], x.lab_result_id)
                per_extract[delivery_index(x.corrected_on)].append(fixed)

        # Leading zeros lost when someone opened the extract in a spreadsheet.
        zero_rows = [r for r in per_extract[0] if r.values["MRN"].startswith("0") and not r.expect]
        for r in self.rng.sample(zero_rows, 30):
            r.values["MRN"] = r.values["MRN"].lstrip("0")
            r.expect.append(expect("leading_zeros_lost", "repaired", "LAB-003"))

        extra0: list[Row] = []
        for text in ["QNS", "CANCELLED", "SEE NOTE", "QNS", "HEMOLYZED"]:
            p = self.rng.choice(self.plain)
            lid = self.fake_id("L")
            extra0.append(
                Row(
                    {
                        "OrderID": lid,
                        "MRN": p.patient_id,
                        "Collected": iso(date(2025, 9, 3)),
                        "LOINC": "4548-4",
                        "TestName": names["HBA1C"],
                        "Result": text,
                        "RefRange": "4.0-5.6",
                        "AbnFlag": "",
                    },
                    [expect("lab_non_reportable", "quarantined", "LAB-008", "held")],
                    lid,
                )
            )
        for value in ["53", "61"]:
            p = self.rng.choice(self.plain)
            lid = self.fake_id("L")
            extra0.append(
                Row(
                    {
                        "OrderID": lid,
                        "MRN": p.patient_id,
                        "Collected": iso(date(2025, 10, 21)),
                        "LOINC": "4548-4",
                        "TestName": names["HBA1C"],
                        "Result": value,
                        "RefRange": "4.0-5.6",
                        "AbnFlag": "H",
                    },
                    [expect("lab_implausible_value", "quarantined", "LAB-010", "held")],
                    lid,
                )
            )
        for orphan in self.orphan_ids[3:7]:
            lid = self.fake_id("L")
            extra0.append(
                Row(
                    {
                        "OrderID": lid,
                        "MRN": orphan,
                        "Collected": iso(date(2025, 7, 8)),
                        "LOINC": "13457-7",
                        "TestName": names["LDL"],
                        "Result": "121",
                        "RefRange": "0-99",
                        "AbnFlag": "H",
                    },
                    [expect("orphan_never_registered", "quarantined", "LAB-011", "held")],
                    lid,
                )
            )
        self.insert_randomly(per_extract[0], extra0)
        extra1: list[Row] = []
        for value in ["58", "70"]:
            p = self.rng.choice(self.plain)
            lid = self.fake_id("L")
            extra1.append(
                Row(
                    {
                        "OrderID": lid,
                        "MRN": p.patient_id,
                        "Collected": iso(date(2026, 2, 17)),
                        "LOINC": "4548-4",
                        "TestName": names["HBA1C"],
                        "Result": value,
                        "RefRange": "4.0-5.6",
                        "AbnFlag": "H",
                    },
                    [expect("lab_implausible_value", "quarantined", "LAB-010", "held")],
                    lid,
                )
            )
        self.insert_randomly(per_extract[1], extra1)
        extra2: list[Row] = []
        for _ in range(2):
            p = self.rng.choice(self.plain)
            lid = self.fake_id("ACC")
            extra2.append(
                Row(
                    {
                        "accession_id": lid,
                        "patient_mrn": p.patient_id,
                        "collection_date": iso(date(2026, 2, 25)),
                        "loinc_code": "13457-7",
                        "test_description": "LDL CHOLESTEROL CALC",
                        "result_value": "131",
                        "result_units": "mg%",
                        "reference_range": "0-99",
                        "abnormal_flag": "H",
                        "specimen_id": f"SP{self.fabricated:07d}",
                    },
                    [expect("lab_unknown_unit", "quarantined", "LAB-009", "held")],
                    lid,
                )
            )
        self.insert_randomly(per_extract[2], extra2)

        for i, extract in enumerate(EXTRACTS):
            f = DeliveryFile(
                "labs",
                "reference_lab",
                f"labs_{stamp(extract)}.csv",
                extract,
                extract,
                v2_header if i == 2 else v1_header,
                per_extract[i],
                layout=2 if i == 2 else 1,
            )
            if i == 1:
                f.counts["updated"] = sum(
                    1 for r in per_extract[1] if any(e["kind"] == "corrected_result" for e in r.expect)
                )
            self.files.append(f)

    # ------------------------------------------------------------------ appointments

    def appointments(self) -> None:
        clinic_header = ["APPT_ID", "MRN", "SITE", "APPT_DATE", "VISIT_TYPE", "STATUS", "PROVIDER"]
        mobile_header = ["visit_id", "mrn", "host_site", "visit_date", "visit_kind", "visit_status"]
        clinic_status = {
            "completed": ["COMPLETED", "KEPT", "CHK-OUT", "Arrived"],
            "no_show": ["NO SHOW", "NS", "NoShow"],
            "cancelled": ["CANCELLED", "CXL", "PT CANCEL"],
            "scheduled": ["SCHEDULED", "BOOKED"],
            "lwbs": ["LWBS"],
        }
        mobile_status = {
            "completed": ["completed"],
            "no_show": ["no show"],
            "cancelled": ["cancelled"],
            "scheduled": ["scheduled"],
        }
        late_reg = {p.patient_id for p in self.world.patients if "late_registration" in p.tags}

        def render(a, status: str) -> Row:
            n = int(a.appointment_id[-4:])
            if a.source == "clinic_scheduling":
                options = clinic_status[status]
                v = {
                    "APPT_ID": a.appointment_id,
                    "MRN": a.patient_id,
                    "SITE": a.site_id,
                    "APPT_DATE": iso(a.appt_date),
                    "VISIT_TYPE": a.visit_type_raw,
                    "STATUS": options[n % len(options)],
                    "PROVIDER": a.provider or "",
                }
            else:
                v = {
                    "visit_id": a.appointment_id,
                    "mrn": a.patient_id,
                    "host_site": a.site_id,
                    "visit_date": iso(a.appt_date),
                    "visit_kind": a.visit_type_raw,
                    "visit_status": mobile_status[status][0],
                }
            row = Row(v, key=f"{a.source}:{a.appointment_id}")
            if status == "lwbs":
                row.expect.append(expect("appointment_status_unmapped", "quarantined", "APT-006", "held"))
            if a.visit_type_raw == "WALK-IN":
                row.expect.append(expect("appointment_visit_type_unmapped", "warned", "APT-011"))
            if a.patient_id in late_reg:
                row.expect.append(expect("orphan_late_registration", "quarantined", "APT-009", "released"))
            return row

        rows = {(src, i): [] for src in ("clinic_scheduling", "mobile_unit") for i in range(3)}
        for a in sorted(self.world.appointments, key=lambda x: (x.appt_date, x.appointment_id)):
            for i, extract in enumerate(EXTRACTS):
                start = EXTRACTS[i - 1] if i else date.min
                final = a.final_status
                if start <= a.booked_on < extract:
                    rows[(a.source, i)].append(render(a, final if a.appt_date < extract else "scheduled"))
                elif a.booked_on < start and start <= a.appt_date < extract:
                    rows[(a.source, i)].append(render(a, final))

        clinic0 = rows[("clinic_scheduling", 0)]
        extra: list[Row] = []
        for _ in range(3):
            p = self.rng.choice(self.plain)
            aid = self.fake_id("A")
            extra.append(
                Row(
                    {
                        "APPT_ID": aid,
                        "MRN": p.patient_id,
                        "SITE": "S00",
                        "APPT_DATE": iso(date(2025, 5, 12)),
                        "VISIT_TYPE": "OFFICE",
                        "STATUS": "COMPLETED",
                        "PROVIDER": p.pcp,
                    },
                    [expect("appointment_unknown_site", "quarantined", "APT-007", "held")],
                    f"clinic_scheduling:{aid}",
                )
            )
        for orphan in self.orphan_ids[7:10]:
            aid = self.fake_id("A")
            extra.append(
                Row(
                    {
                        "APPT_ID": aid,
                        "MRN": orphan,
                        "SITE": "S05",
                        "APPT_DATE": iso(date(2025, 8, 4)),
                        "VISIT_TYPE": "OFFICE",
                        "STATUS": "COMPLETED",
                        "PROVIDER": "",
                    },
                    [expect("orphan_never_registered", "quarantined", "APT-009", "held")],
                    f"clinic_scheduling:{aid}",
                )
            )
        self.insert_randomly(clinic0, extra)
        clinic1 = rows[("clinic_scheduling", 1)]
        extra = []
        for _ in range(2):
            p = self.rng.choice(self.plain)
            aid = self.fake_id("A")
            day = E1 + timedelta(days=self.rng.randint(2, 6))
            extra.append(
                Row(
                    {
                        "APPT_ID": aid,
                        "MRN": p.patient_id,
                        "SITE": p.site_on(1),
                        "APPT_DATE": iso(day),
                        "VISIT_TYPE": "OFFICE",
                        "STATUS": "KEPT",
                        "PROVIDER": p.pcp,
                    },
                    [expect("appointment_completed_in_future", "quarantined", "APT-008", "held")],
                    f"clinic_scheduling:{aid}",
                )
            )
        self.insert_randomly(clinic1, extra)

        for i, extract in enumerate(EXTRACTS):
            for source, prefix, header in (
                ("clinic_scheduling", "appointments_main", clinic_header),
                ("mobile_unit", "appointments_mobile", mobile_header),
            ):
                file_rows = rows[(source, i)]
                if source == "clinic_scheduling" and i == 2:
                    # The clinic extract was cut off part-way: a third of the usual file arrives on the
                    # Monday, the complete file is re-delivered the next day.
                    cut = file_rows[: int(len(file_rows) * 0.35)]
                    self.files.append(
                        DeliveryFile(
                            "appointments",
                            source,
                            f"{prefix}_{stamp(extract)}.csv",
                            extract,
                            extract,
                            header,
                            [Row(dict(r.values), [], r.key) for r in cut],
                            outcome="blocked",
                            outcome_rule="APT-001",
                        )
                    )
                    self.files.append(
                        DeliveryFile(
                            "appointments",
                            source,
                            f"{prefix}_{stamp(extract)}_r1.csv",
                            REDELIVERY_ARRIVAL,
                            extract,
                            header,
                            file_rows,
                        )
                    )
                    continue
                self.files.append(
                    DeliveryFile(
                        "appointments", source, f"{prefix}_{stamp(extract)}.csv", extract, extract, header, file_rows
                    )
                )

    # ------------------------------------------------------------------ pharmacy

    def pharmacy(self) -> None:
        header = [
            "claim_id",
            "member_mrn",
            "fill_date",
            "drug_name",
            "strength",
            "days_supply",
            "quantity",
            "claim_status",
            "adjudicated_date",
        ]
        late_reg = {p.patient_id for p in self.world.patients if "late_registration" in p.tags}
        rows: dict[int, list[Row]] = {0: [], 1: [], 2: []}

        def render(c, status: str, when: date) -> Row:
            drug = c.drug if int(c.claim_id[-2:]) % 9 else c.drug.capitalize()
            v = {
                "claim_id": c.claim_id,
                "member_mrn": c.patient_id,
                "fill_date": iso(c.fill_date),
                "drug_name": drug,
                "strength": c.strength,
                "days_supply": str(c.days_supply),
                "quantity": str(c.quantity),
                "claim_status": status,
                "adjudicated_date": iso(when),
            }
            row = Row(v, key=f"{c.claim_id}:{status}")
            if c.patient_id in late_reg:
                row.expect.append(expect("orphan_late_registration", "quarantined", "RX-010", "released"))
            return row

        for c in sorted(self.world.claims, key=lambda x: (x.adjudicated, x.claim_id)):
            idx = delivery_index(c.adjudicated)
            if idx is not None:
                row = render(c, "P", c.adjudicated)
                if idx >= 1 and c.fill_date <= MY_END:
                    row.expect.append(expect("late_claim_closed_year", "warned", "RX-012"))
                rows[idx].append(row)
            if c.reversed_on is not None:
                ridx = delivery_index(c.reversed_on)
                if ridx is not None:
                    row = render(c, "R", c.reversed_on)
                    if ridx >= 1 and c.fill_date <= MY_END:
                        row.expect.append(expect("late_claim_closed_year", "warned", "RX-012"))
                    rows[ridx].append(row)

        extra: list[Row] = []
        for supply in ["0", "400", "-30", "0", "999"]:
            p = self.rng.choice(self.plain)
            cid = self.fake_id("RX")
            extra.append(
                Row(
                    {
                        "claim_id": cid,
                        "member_mrn": p.patient_id,
                        "fill_date": iso(date(2025, 4, 2)),
                        "drug_name": "atorvastatin",
                        "strength": "20 mg",
                        "days_supply": supply,
                        "quantity": "30",
                        "claim_status": "P",
                        "adjudicated_date": iso(date(2025, 4, 2)),
                    },
                    [expect("claim_days_supply_invalid", "quarantined", "RX-006", "held")],
                    f"{cid}:P",
                )
            )
        for _ in range(3):
            p = self.rng.choice(self.plain)
            cid = self.fake_id("RX")
            extra.append(
                Row(
                    {
                        "claim_id": cid,
                        "member_mrn": p.patient_id,
                        "fill_date": iso(date(2025, 3, 11)),
                        "drug_name": "amoxicillin",
                        "strength": "500 mg",
                        "days_supply": "10",
                        "quantity": "30",
                        "claim_status": "P",
                        "adjudicated_date": iso(date(2025, 3, 11)),
                    },
                    [expect("claim_drug_not_tracked", "quarantined", "RX-008", "held")],
                    f"{cid}:P",
                )
            )
        for orphan in self.orphan_ids[10:12]:
            cid = self.fake_id("RX")
            extra.append(
                Row(
                    {
                        "claim_id": cid,
                        "member_mrn": orphan,
                        "fill_date": iso(date(2025, 11, 20)),
                        "drug_name": "lisinopril",
                        "strength": "10 mg",
                        "days_supply": "30",
                        "quantity": "30",
                        "claim_status": "P",
                        "adjudicated_date": iso(date(2025, 11, 20)),
                    },
                    [expect("orphan_never_registered", "quarantined", "RX-010", "held")],
                    f"{cid}:P",
                )
            )
        self.insert_randomly(rows[0], extra)

        # The third weekly claims file never arrives: the freshness report must say so.
        for i, extract in enumerate(EXTRACTS[:2]):
            self.files.append(
                DeliveryFile(
                    "pharmacy", "pharmacy_claims", f"rx_claims_{stamp(extract)}.csv", extract, extract, header, rows[i]
                )
            )
