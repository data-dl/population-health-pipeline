"""Expected measure results, computed straight from the truth.

This is deliberately a second, independent implementation of every measure: plain Python loops
over the generator's own objects, sharing nothing with the dbt models it grades. When the two
agree to the patient, both are probably right.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .world import EXTRACTS, MY_END, MY_START, Patient, World, delivery_index, ifcc_to_ngsp, ngsp_to_ifcc

MEASURES = [
    "DM_A1C_TESTED",
    "DM_A1C_CONTROLLED",
    "DM_A1C_POOR",
    "DEP_SCREEN",
    "DEP_FOLLOWUP",
    "PDC_DIABETES",
    "PDC_RAS",
    "PDC_STATIN",
    "SDOH_SCREEN",
]
PDC_CLASSES = {"PDC_DIABETES": "diabetes_oral", "PDC_RAS": "ras_antagonist", "PDC_STATIN": "statin"}
DELIVERED = {0, 1, 2}
PHARMACY_DELIVERED = {0, 1}  # the third claims file never arrives


def final_roster(world: World) -> dict[str, Patient]:
    """Patients listed in the last snapshot with a row the roster rules accept."""
    return {p.patient_id: p for p in world.patients if "roster_unknown_site" not in p.tags}


def _a1c_value(x) -> float:
    if x.report_unit == "mmol/mol" and x.censor is None:
        return float(ifcc_to_ngsp(ngsp_to_ifcc(x.value)))
    return float(x.value)


def patient_pdc(world: World) -> dict[tuple[str, str], tuple[int, int]]:
    """(patient, drug class) -> (days covered, days in the treatment period) for eligible patients."""
    roster = final_roster(world)
    fills: dict[tuple[str, str], list] = defaultdict(list)
    for c in world.claims:
        if c.patient_id not in roster or not (MY_START <= c.fill_date <= MY_END):
            continue
        if delivery_index(c.adjudicated) not in PHARMACY_DELIVERED:
            continue
        if c.reversed_on is not None and delivery_index(c.reversed_on) in PHARMACY_DELIVERED:
            continue
        fills[(c.patient_id, c.drug_class)].append(c)
    result = {}
    for key, claims in fills.items():
        if len({c.fill_date for c in claims}) < 2:
            continue
        start = min(c.fill_date for c in claims)
        covered: set[date] = set()
        by_drug: dict[str, list] = defaultdict(list)
        for c in claims:
            by_drug[c.drug].append(c)
        for drug_claims in by_drug.values():
            # An early refill of the same drug does not overlap: its supply starts when the last one ends.
            next_free = None
            for c in sorted(drug_claims, key=lambda x: (x.fill_date, x.claim_id)):
                begin = c.fill_date if next_free is None else max(c.fill_date, next_free)
                for offset in range(c.days_supply):
                    day = begin + timedelta(days=offset)
                    if start <= day <= MY_END:
                        covered.add(day)
                next_free = begin + timedelta(days=c.days_supply)
        result[key] = (len(covered), (MY_END - start).days + 1)
    return result


def expected_measures(world: World) -> dict[str, dict[str, list[int]]]:
    roster = final_roster(world)
    site = {pid: p.site_on(2) for pid, p in roster.items()}
    age = {pid: p.age_on(MY_END) for pid, p in roster.items()}

    completed: dict[str, list[date]] = defaultdict(list)
    for a in world.appointments:
        if a.patient_id in roster and a.final_status == "completed" and a.appt_date < EXTRACTS[-1]:
            completed[a.patient_id].append(a.appt_date)
    active = {pid for pid, days in completed.items() if any(MY_START <= d <= MY_END for d in days)}

    phq: dict[str, list] = defaultdict(list)
    sdoh_done: set[str] = set()
    for s in world.screenings:
        if s.patient_id not in roster:
            continue
        if s.phq9_complete:
            phq[s.patient_id].append((s.screen_date, sum(s.phq9)))
        if s.sdoh_complete and MY_START <= s.screen_date <= MY_END:
            sdoh_done.add(s.patient_id)

    a1c: dict[str, list] = defaultdict(list)
    for x in world.labs:
        if x.analyte != "HBA1C" or x.patient_id not in roster or not (MY_START <= x.collected <= MY_END):
            continue
        if delivery_index(x.resulted_on) in DELIVERED:
            a1c[x.patient_id].append((x.collected, x.lab_result_id, _a1c_value(x)))

    counts: dict[str, dict[str, list[int]]] = {m: defaultdict(lambda: [0, 0]) for m in MEASURES}

    def tally(measure: str, pid: str, hit: bool) -> None:
        for key in (site[pid], "ALL"):
            counts[measure][key][1] += 1
            counts[measure][key][0] += int(hit)

    for pid, p in roster.items():
        if pid in active and age[pid] >= 12:
            tally("DEP_SCREEN", pid, any(MY_START <= d <= MY_END for d, _ in phq[pid]))
        positives = sorted(d for d, total in phq[pid] if total >= 10 and MY_START <= d <= date(2025, 12, 1))
        if positives:
            index = positives[0]
            window = (index, index + timedelta(days=30))
            followed = any(window[0] < d <= window[1] for d in completed[pid]) or any(
                window[0] < d <= window[1] for d, _ in phq[pid]
            )
            tally("DEP_FOLLOWUP", pid, followed)
        if "diabetes_t2" in p.groups and 18 <= age[pid] <= 75 and pid in active:
            tests = sorted(a1c[pid])
            tally("DM_A1C_TESTED", pid, bool(tests))
            latest = tests[-1][2] if tests else None
            tally("DM_A1C_CONTROLLED", pid, latest is not None and latest < 8.0)
            tally("DM_A1C_POOR", pid, latest is None or latest > 9.0)
        if pid in active and age[pid] >= 18:
            tally("SDOH_SCREEN", pid, pid in sdoh_done)

    for (pid, drug_class), (days_covered, period) in patient_pdc(world).items():
        measure = next(m for m, c in PDC_CLASSES.items() if c == drug_class)
        tally(measure, pid, 5 * days_covered >= 4 * period)

    return {m: {k: list(v) for k, v in sorted(counts[m].items())} for m in MEASURES}
