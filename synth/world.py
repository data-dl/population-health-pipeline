"""Ground truth for the synthetic network: clinics, staff, patients and every clinical event.

Everything is drawn from one seeded random generator in a fixed order, so the same seed always
produces the same world. Supplier files (with their planted defects) are rendered from this truth
by `synth.deliveries`; the expected measure results are computed from it by `synth.oracle`.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from . import vocab

REPO = Path(__file__).resolve().parent.parent
SEED = 20260926
N_PATIENTS = 5000

MY = 2025
MY_START = date(2025, 1, 1)
MY_END = date(2025, 12, 31)
# Weekly extracts. An extract dated E carries what became available up to the day before E.
EXTRACTS = (date(2026, 2, 16), date(2026, 2, 23), date(2026, 3, 2))
REDELIVERY_ARRIVAL = date(2026, 3, 3)
DATA_END = EXTRACTS[-1] - timedelta(days=1)
HORIZON = date(2026, 3, 20)  # appointments are booked up to this date

CONDITION_PREFIXES = {
    "E11": "diabetes_t2",
    "I10": "hypertension",
    "E78": "hyperlipidemia",
    "F32": "depression",
    "F33": "depression",
}
DRUGS = {
    "diabetes_oral": [("metformin", 0.70), ("glipizide", 0.15), ("glimepiride", 0.10), ("sitagliptin", 0.05)],
    "ras_antagonist": [("lisinopril", 0.50), ("losartan", 0.30), ("valsartan", 0.10), ("enalapril", 0.10)],
    "statin": [("atorvastatin", 0.55), ("simvastatin", 0.20), ("rosuvastatin", 0.20), ("pravastatin", 0.05)],
}
STRENGTHS = {
    "metformin": ["500 mg", "850 mg", "1000 mg"],
    "glipizide": ["5 mg", "10 mg"],
    "glimepiride": ["2 mg", "4 mg"],
    "sitagliptin": ["100 mg"],
    "lisinopril": ["10 mg", "20 mg", "40 mg"],
    "losartan": ["50 mg", "100 mg"],
    "valsartan": ["80 mg", "160 mg"],
    "enalapril": ["10 mg", "20 mg"],
    "atorvastatin": ["20 mg", "40 mg", "80 mg"],
    "simvastatin": ["20 mg", "40 mg"],
    "rosuvastatin": ["10 mg", "20 mg"],
    "pravastatin": ["40 mg"],
}
LOINC = {"HBA1C": "4548-4", "HBA1C_IFCC": "59261-8", "LDL": "13457-7"}


def delivery_index(available: date) -> int | None:
    """Which extract first carries a record that became available on `available`."""
    for i, extract in enumerate(EXTRACTS):
        if available < extract:
            return i
    return None


def one_decimal(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def ngsp_to_ifcc(pct: Decimal) -> int:
    """HbA1c % -> mmol/mol as a laboratory reports it (whole number)."""
    return int(((pct - Decimal("2.152")) / Decimal("0.09148")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ifcc_to_ngsp(mmol: int) -> Decimal:
    """mmol/mol -> HbA1c %, rounded to one decimal half up (the warehouse's rule)."""
    return (Decimal(mmol) * Decimal("0.09148") + Decimal("2.152")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


@dataclass
class Site:
    site_id: str
    name: str
    region: str
    zip3: str
    city: str
    weight: float
    screen_rate: float
    pcps: list[str] = field(default_factory=list)
    care_managers: list[str] = field(default_factory=list)


@dataclass
class Patient:
    patient_id: str
    first: str
    last: str
    birth_date: date
    sex: str
    race: str
    ethnicity: str
    language: str
    street: str
    city: str
    zip: str
    phone: str
    email: str
    payer: str
    site_id: str
    pcp: str
    care_manager: str
    problems: list[str]
    registration_date: date
    active: bool = True
    noshow_p: float = 0.1
    a1c_mean: float | None = None
    depressed: bool = False
    roster_from: int = 0  # index of the first extract that lists the patient
    tags: set[str] = field(default_factory=set)
    site_from_d2: str | None = None
    phone_from_d2: str | None = None
    street_from_d2: str | None = None

    @property
    def groups(self) -> set[str]:
        return {CONDITION_PREFIXES[c[:3]] for c in self.problems if c[:3] in CONDITION_PREFIXES}

    def age_on(self, day: date) -> int:
        years = day.year - self.birth_date.year
        if (day.month, day.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years

    def site_on(self, extract_index: int) -> str:
        return self.site_from_d2 if (self.site_from_d2 and extract_index >= 1) else self.site_id


@dataclass
class Appointment:
    source: str
    appointment_id: str
    patient_id: str
    site_id: str
    appt_date: date
    visit_type: str
    visit_type_raw: str
    final_status: str  # completed, no_show, cancelled, or lwbs (a code the warehouse does not map)
    booked_on: date
    provider: str | None


@dataclass
class Screening:
    screening_id: str
    patient_id: str
    screen_date: date
    admin_mode_raw: str
    phq9: list[int | None] | None
    gad7: list[int | None] | None
    sdoh: dict[str, str | None] | None
    phq9_reported: int | None = None
    gad7_reported: int | None = None

    @property
    def phq9_complete(self) -> bool:
        return self.phq9 is not None and all(v is not None for v in self.phq9)

    @property
    def phq9_total(self) -> int | None:
        return sum(self.phq9) if self.phq9_complete else None

    @property
    def sdoh_complete(self) -> bool:
        return self.sdoh is not None and all(v is not None for v in self.sdoh.values())


@dataclass
class LabResult:
    lab_result_id: str
    patient_id: str
    collected: date
    analyte: str
    value: Decimal  # final value in the standard unit
    resulted_on: date
    report_unit: str = ""  # unit the laboratory sends; decided when rendering layout 2
    censor: str | None = None
    corrected_from: Decimal | None = None
    corrected_on: date | None = None

    @property
    def loinc(self) -> str:
        if self.analyte == "LDL":
            return LOINC["LDL"]
        return LOINC["HBA1C_IFCC"] if self.report_unit == "mmol/mol" else LOINC["HBA1C"]


@dataclass
class Claim:
    claim_id: str
    patient_id: str
    fill_date: date
    drug: str
    drug_class: str
    strength: str
    days_supply: int
    quantity: int
    adjudicated: date
    reversed_on: date | None = None


@dataclass
class World:
    seed: int
    sites: dict[str, Site]
    patients: list[Patient]
    appointments: list[Appointment]
    screenings: list[Screening]
    labs: list[LabResult]
    claims: list[Claim]

    def patient(self, patient_id: str) -> Patient:
        return self._index[patient_id]

    def __post_init__(self) -> None:
        self._index = {p.patient_id: p for p in self.patients}


# --------------------------------------------------------------------------------------------- helpers


def _weighted(rng: random.Random, pairs):
    total = sum(w for _, w in pairs)
    r = rng.random() * total
    for value, weight in pairs:
        r -= weight
        if r <= 0:
            return value
    return pairs[-1][0]


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _weekday(rng: random.Random, start: date, end: date) -> date:
    day = start + timedelta(days=rng.randint(0, (end - start).days))
    if day.weekday() == 5:
        day -= timedelta(days=1)
    elif day.weekday() == 6:
        day += timedelta(days=1)
    return min(max(day, start), end)


def _spread(rng: random.Random, total: int, n_items: int) -> list[int]:
    items = [0] * n_items
    while total:
        i = rng.randrange(n_items)
        if items[i] < 3:
            items[i] += 1
            total -= 1
    return items


def _load_sites() -> dict[str, Site]:
    weights = [1.3, 1.0, 1.1, 0.9, 1.2, 0.8, 1.0, 0.7, 1.1, 0.9, 0.8, 1.2]
    rates = [0.88, 0.80, 0.74, 0.91, 0.62, 0.70, 0.84, 0.58, 0.77, 0.86, 0.66, 0.79]
    sites: dict[str, Site] = {}
    with open(REPO / "config" / "reference" / "sites.csv", encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            sites[row["site_id"]] = Site(
                row["site_id"], row["site_name"], row["region"], row["zip3"], row["city"], weights[i], rates[i]
            )
    return sites


# --------------------------------------------------------------------------------------------- world


def build_world(seed: int = SEED) -> World:
    rng = random.Random(seed)
    sites = _load_sites()
    zip3_city = {s.zip3: s.city for s in sites.values()}

    used_names: set[str] = set()

    def staff_name() -> str:
        while True:
            name = f"{rng.choice(vocab.FIRST_F + vocab.FIRST_M)} {rng.choice(vocab.LAST)}"
            if name not in used_names:
                used_names.add(name)
                return name

    for site in sites.values():
        site.pcps = [staff_name() for _ in range(3)]
        site.care_managers = [staff_name() for _ in range(2)]

    ids = [f"{n:08d}" for n in rng.sample(range(1_000_000, 100_000_000), N_PATIENTS)]
    site_pairs = [(s.site_id, s.weight) for s in sites.values()]
    patients: list[Patient] = []
    for pid in ids:
        sex = _weighted(rng, [("F", 0.51), ("M", 0.48), ("X", 0.005), ("U", 0.005)])
        pool = vocab.FIRST_F if sex == "F" else vocab.FIRST_M if sex == "M" else vocab.FIRST_F + vocab.FIRST_M
        first, last = rng.choice(pool), rng.choice(vocab.LAST)
        band = _weighted(
            rng, [((0, 17), 0.18), ((18, 39), 0.30), ((40, 64), 0.34), ((65, 89), 0.165), ((90, 99), 0.015)]
        )
        age = rng.randint(*band)
        birth = date(EXTRACTS[0].year - age - 1, 1, 1) + timedelta(days=rng.randint(46, 410))
        race = _weighted(
            rng,
            [
                ("White", 0.38),
                ("Black or African American", 0.24),
                ("Asian", 0.12),
                ("American Indian or Alaska Native", 0.01),
                ("Native Hawaiian or Other Pacific Islander", 0.005),
                ("Middle Eastern or North African", 0.02),
                ("Multiple", 0.035),
                ("", 0.04),
            ],
        )
        ethnicity = _weighted(rng, [("Hispanic or Latino", 0.28), ("Not Hispanic or Latino", 0.68), ("", 0.04)])
        language = _weighted(
            rng,
            [
                ("en", 0.68),
                ("es", 0.20),
                ("zh", 0.04),
                ("ru", 0.02),
                ("bn", 0.015),
                ("ht", 0.015),
                ("ar", 0.01),
                ("fr", 0.01),
                ("", 0.01),
            ],
        )
        site_id = _weighted(rng, site_pairs)
        site = sites[site_id]
        zip3 = site.zip3 if rng.random() < 0.85 else rng.choice(sorted(zip3_city))
        street = f"{rng.randint(1, 9899)} {rng.choice(vocab.STREETS)} {rng.choice(vocab.STREET_SUFFIX)}"
        phone = f"555{rng.randint(200, 999)}{rng.randint(0, 9999):04d}"
        email = f"{first}.{last}{rng.randint(1, 99)}@example.org".lower() if rng.random() < 0.6 else ""
        payer = _weighted(rng, [("government", 0.46), ("commercial", 0.34), ("marketplace", 0.10), ("self_pay", 0.10)])
        problems: list[str] = []
        if age >= 18:
            tier = 0 if age < 40 else 1 if age < 65 else 2
            if rng.random() < (0.03, 0.14, 0.22)[tier]:
                problems.append(rng.choice(["E11.9", "E11.65"]))
            if rng.random() < (0.06, 0.30, 0.55)[tier]:
                problems.append("I10")
            if rng.random() < (0.05, 0.25, 0.40)[tier]:
                problems.append(rng.choice(["E78.5", "E78.00"]))
        depressed = False
        if age >= 12 and rng.random() < (0.04 if age < 18 else 0.08):
            problems.append(rng.choice(["F32.9", "F33.1"]))
            depressed = True
        registration = date(2008, 1, 1) + timedelta(days=rng.randint(0, 6389))
        patient = Patient(
            patient_id=pid,
            first=first,
            last=last,
            birth_date=birth,
            sex=sex,
            race=race,
            ethnicity=ethnicity,
            language=language,
            street=street,
            city=zip3_city[zip3],
            zip=f"{zip3}{rng.randint(0, 99):02d}",
            phone=phone,
            email=email,
            payer=payer,
            site_id=site_id,
            pcp=rng.choice(site.pcps),
            care_manager=rng.choice(site.care_managers),
            problems=problems,
            registration_date=registration,
            active=rng.random() >= 0.15,
            noshow_p=rng.uniform(0.03, 0.15) if rng.random() < 0.9 else rng.uniform(0.30, 0.50),
            depressed=depressed,
        )
        if "diabetes_t2" in patient.groups:
            patient.a1c_mean = _weighted(
                rng, [(rng.uniform(6.3, 7.4), 0.45), (rng.uniform(7.6, 8.8), 0.30), (rng.uniform(9.2, 11.8), 0.25)]
            )
        patients.append(patient)

    # Roster-level stories. Patients chosen for roster defects have no clinical events, so the
    # defects exercise the roster rules without cascading into the clinical feeds.
    inactive = [p for p in patients if not p.active]
    for tag, count in (
        ("roster_unknown_site", 3),
        ("roster_conflicting_duplicate", 2),
        ("roster_invalid_birth_date", 2),
    ):
        for p in rng.sample([q for q in inactive if not q.tags], count):
            p.tags.add(tag)
    active_adults = [p for p in patients if p.active and p.age_on(EXTRACTS[0]) >= 18]
    for p in rng.sample(active_adults, 12):
        p.tags.add("late_registration")
        p.roster_from = 1
        p.registration_date = date(2026, 2, 9) + timedelta(days=rng.randint(0, 3))
    movable = [p for p in patients if p.active and not p.tags]
    for p in rng.sample(movable, 25):
        p.site_from_d2 = rng.choice([s for s in sites if s != p.site_id])
        p.tags.add("site_change")
    for p in rng.sample([p for p in movable if not p.tags], 30):
        if rng.random() < 0.5:
            p.phone_from_d2 = f"555{rng.randint(200, 999)}{rng.randint(0, 9999):04d}"
        else:
            p.street_from_d2 = f"{rng.randint(1, 9899)} {rng.choice(vocab.STREETS)} {rng.choice(vocab.STREET_SUFFIX)}"
        p.tags.add("contact_change")

    world = World(seed, sites, patients, [], [], [], [])
    _clinical_events(rng, world)
    return world


def _clinical_events(rng: random.Random, world: World) -> None:
    counters = {"A": 10_300_000, "MU": 400_000, "SCR": 5_200_000, "L": 70_100_000, "RX": 610_000_000}

    def next_id(prefix: str, width: int) -> str:
        counters[prefix] += rng.randint(1, 3)
        return f"{prefix}{counters[prefix]:0{width}d}"

    visit_types = [("office", 0.70), ("telehealth", 0.15), ("behavioral_health", 0.08), ("nurse", 0.07)]
    raw_types = {
        "office": ["OFFICE", "OV", "OFFICE VISIT"],
        "telehealth": ["TELEHEALTH", "VIDEO"],
        "behavioral_health": ["BH"],
        "nurse": ["NURSE", "RN VISIT"],
        "mobile": ["mobile", "van"],
    }

    def appointment(
        p: Patient, day: date, *, status: str | None = None, kind: str | None = None, booked: date | None = None
    ) -> Appointment:
        mobile = kind is None and rng.random() < 0.08
        if status is None:
            r = rng.random()
            status = "cancelled" if r < 0.07 else "no_show" if rng.random() < p.noshow_p else "completed"
        visit = "mobile" if mobile else (kind or _weighted(rng, visit_types))
        appt = Appointment(
            source="mobile_unit" if mobile else "clinic_scheduling",
            appointment_id=next_id("MU", 6) if mobile else next_id("A", 8),
            patient_id=p.patient_id,
            site_id=p.site_on(0 if day < EXTRACTS[1] else 1),
            appt_date=day,
            visit_type=visit,
            visit_type_raw=rng.choice(raw_types[visit]),
            final_status=status,
            booked_on=booked or day - timedelta(days=rng.randint(0, 28)),
            provider=None if mobile else p.pcp,
        )
        world.appointments.append(appt)
        return appt

    admin_modes = [("IN_PERSON", 0.55), ("In Person", 0.20), ("TELEHEALTH", 0.10), ("Video", 0.05), ("PORTAL", 0.10)]

    def phq9_items(depressed: bool) -> list[int]:
        weights = [0.25, 0.30, 0.25, 0.12, 0.08] if depressed else [0.62, 0.24, 0.09, 0.04, 0.01]
        low, high = _weighted(rng, list(zip([(0, 4), (5, 9), (10, 14), (15, 19), (20, 27)], weights, strict=True)))
        items = _spread(rng, rng.randint(low, high), 9)
        if items[8] and sum(items) < 15:  # keep the self-harm item rare, as it is in practice
            moving, items[8] = items[8], 0
            for _ in range(moving):
                spare = [i for i in range(8) if items[i] < 3]
                if spare:
                    items[rng.choice(spare)] += 1
                else:
                    items[8] += 1
        return items

    def screening(p: Patient, day: date, *, sdoh: bool, mode: str | None = None, gad: bool = True) -> Screening:
        phq = phq9_items(p.depressed)
        gad_items = _spread(rng, max(0, min(21, round(sum(phq) * 0.7 + rng.gauss(0, 2)))), 7) if gad else None
        needs = None
        if sdoh:
            rates = {"housing": 0.08, "food": 0.12, "transport": 0.10, "utilities": 0.07, "safety": 0.03}
            needs = {}
            for domain, rate in rates.items():
                r = rng.random()
                needs[domain] = "Y" if r < rate else "D" if r < rate + 0.03 else "N"
            if rng.random() < 0.03:
                needs[rng.choice(list(needs))] = None
        s = Screening(next_id("SCR", 7), p.patient_id, day, mode or _weighted(rng, admin_modes), phq, gad_items, needs)
        s.phq9_reported = sum(phq)
        s.gad7_reported = sum(gad_items) if gad_items else None
        world.screenings.append(s)
        return s

    def a1c(p: Patient, day: date) -> None:
        value = one_decimal(max(5.0, min(14.6, (p.a1c_mean or 5.6) + rng.gauss(0, 0.35))))
        world.labs.append(
            LabResult(next_id("L", 8), p.patient_id, day, "HBA1C", value, day + timedelta(days=rng.randint(1, 3)))
        )

    def ldl(p: Patient, day: date) -> None:
        world.labs.append(
            LabResult(
                next_id("L", 8),
                p.patient_id,
                day,
                "LDL",
                Decimal(rng.randint(70, 190)),
                day + timedelta(days=rng.randint(1, 3)),
            )
        )

    def therapy(p: Patient, drug_class: str, start: date) -> None:
        drug = _weighted(rng, DRUGS[drug_class])
        strength = rng.choice(STRENGTHS[drug])
        supply = 90 if rng.random() < 0.2 else 30
        adherent = rng.random() < 0.65
        stop_after = rng.randint(2, 6) if rng.random() < 0.12 else None
        multiplier = 2 if drug == "metformin" and rng.random() < 0.4 else 1
        day, fills = start, 0
        while day <= DATA_END:
            adjudicated = day + timedelta(days=rng.randint(0, 2))
            claim = Claim(
                next_id("RX", 9),
                p.patient_id,
                day,
                drug,
                drug_class,
                strength,
                supply,
                supply * multiplier,
                adjudicated,
            )
            if rng.random() < 0.03:
                claim.reversed_on = adjudicated + timedelta(days=rng.randint(1, 10))
            world.claims.append(claim)
            fills += 1
            if stop_after and fills >= stop_after:
                break
            gap = rng.choice([-6, -3, -2, -1, 0, 0, 0, 1, 2, 3, 4] if adherent else [0, 3, 8, 12, 18, 25, 35, 50])
            day += timedelta(days=supply + gap)

    for p in world.patients:
        if not p.active:
            continue
        adult = p.age_on(EXTRACTS[0]) >= 18
        if "late_registration" in p.tags:
            # A new patient seen at intake a few days before the roster extract caught up.
            day = p.registration_date
            appointment(p, day, status="completed", kind="office", booked=day)
            screening(p, day, sdoh=True, mode="IN_PERSON")
            if "diabetes_t2" in p.groups:
                a1c(p, day)
                world.labs[-1].resulted_on = day + timedelta(days=1)
            for group, drug_class in (
                ("diabetes_t2", "diabetes_oral"),
                ("hypertension", "ras_antagonist"),
                ("hyperlipidemia", "statin"),
            ):
                if group in p.groups:
                    fill = day + timedelta(days=1)
                    world.claims.append(
                        Claim(
                            next_id("RX", 9),
                            p.patient_id,
                            fill,
                            DRUGS[drug_class][0][0],
                            drug_class,
                            STRENGTHS[DRUGS[drug_class][0][0]][0],
                            30,
                            30,
                            fill,
                        )
                    )
            continue

        lam = 2 + 1.5 * len(p.groups) + (1 if p.age_on(MY_END) >= 65 else 0)
        n_2025 = max(1, min(12, _poisson(rng, lam)))
        mine = [appointment(p, _weekday(rng, MY_START, MY_END)) for _ in range(n_2025)]
        mine += [appointment(p, _weekday(rng, date(2026, 1, 2), HORIZON)) for _ in range(_poisson(rng, lam * 0.22))]

        # Screening at the first completed visit of the year, more often at some clinics than others,
        # and a little less often when an interpreter is needed.
        completed = sorted(
            (a for a in mine if a.final_status == "completed" and a.appt_date <= MY_END), key=lambda a: a.appt_date
        )
        rate = world.sites[p.site_id].screen_rate * (1.0 if p.language == "en" else 0.85)
        if p.age_on(MY_END) >= 12 and completed and rng.random() < rate:
            s = screening(p, completed[0].appt_date, sdoh=adult and rng.random() < 0.55)
            total = sum(s.phq9)
            if total >= 10 and s.screen_date <= date(2025, 12, 1):
                r = rng.random()
                if r < 0.45:
                    day = s.screen_date + timedelta(days=rng.randint(5, 30))
                    appointment(p, day, status="completed", kind="behavioral_health", booked=s.screen_date)
                elif r < 0.60:
                    screening(
                        p, s.screen_date + timedelta(days=rng.randint(7, 30)), sdoh=False, mode="PORTAL", gad=False
                    )
                elif r < 0.72:
                    day = s.screen_date + timedelta(days=rng.randint(31, 60))
                    appointment(p, day, status="completed", kind="behavioral_health", booked=s.screen_date)
        completed_2026 = sorted(
            (a for a in mine if a.final_status == "completed" and date(2026, 1, 1) <= a.appt_date <= DATA_END),
            key=lambda a: a.appt_date,
        )
        if p.age_on(MY_END) >= 12 and completed_2026 and rng.random() < 0.35:
            screening(p, completed_2026[0].appt_date, sdoh=False)

        if "diabetes_t2" in p.groups:
            n = _weighted(rng, [(0, 0.12), (1, 0.25), (2, 0.33), (3, 0.20), (4, 0.10)])
            for day in sorted(_weekday(rng, MY_START, MY_END) for _ in range(n)):
                a1c(p, day)
            if rng.random() < 0.3:
                a1c(p, _weekday(rng, date(2026, 1, 5), date(2026, 2, 27)))
        elif adult and p.age_on(MY_END) >= 40 and rng.random() < 0.08:
            a1c(p, _weekday(rng, MY_START, MY_END))
        if "hyperlipidemia" in p.groups:
            for day in sorted(_weekday(rng, MY_START, MY_END) for _ in range(rng.randint(1, 2))):
                ldl(p, day)
            if rng.random() < 0.2:
                ldl(p, _weekday(rng, date(2026, 1, 5), date(2026, 2, 27)))

        for group, drug_class, share in (
            ("diabetes_t2", "diabetes_oral", 0.80),
            ("hypertension", "ras_antagonist", 0.75),
            ("hyperlipidemia", "statin", 0.75),
        ):
            if group in p.groups and rng.random() < share:
                start = (
                    _weekday(rng, date(2025, 1, 2), date(2025, 1, 25))
                    if rng.random() < 0.7
                    else _weekday(rng, date(2025, 2, 1), date(2025, 10, 15))
                )
                therapy(p, drug_class, start)

    _planted_truth(rng, world)


def _planted_truth(rng: random.Random, world: World) -> None:
    """Changes to the truth itself: statuses the warehouse does not know, late and corrected results."""
    by_date = sorted(world.appointments, key=lambda a: (a.appt_date, a.appointment_id))
    clinic_2025 = [a for a in by_date if a.source == "clinic_scheduling" and a.appt_date <= MY_END]
    for a in rng.sample(clinic_2025, 6):
        a.final_status = "lwbs"  # left without being seen: not in the code map, held for a steward
    for a in rng.sample([a for a in clinic_2025 if a.final_status == "completed"], 5):
        a.visit_type, a.visit_type_raw = "other", "WALK-IN"

    a1c_2025 = [x for x in world.labs if x.analyte == "HBA1C" and x.collected <= MY_END]
    late_pool = [x for x in world.labs if date(2025, 11, 1) <= x.collected <= MY_END]
    for x in rng.sample(late_pool, 15):
        x.resulted_on = EXTRACTS[0] + timedelta(days=rng.randint(0, 6))  # late: arrives in the second extract
    december = [x for x in a1c_2025 if x.collected >= date(2025, 12, 1) and x.resulted_on < EXTRACTS[0]]
    for x in rng.sample(december, 8):
        x.resulted_on = EXTRACTS[1] + timedelta(days=rng.randint(0, 6))  # later still: third extract, layout 2
    early = [x for x in a1c_2025 if x.resulted_on < date(2025, 12, 1)]
    for x in rng.sample(early, 3):
        x.corrected_from = x.value + Decimal("1.5")
        x.corrected_on = EXTRACTS[0] + timedelta(days=rng.randint(0, 6))
    on_time = [x for x in a1c_2025 if x.resulted_on < EXTRACTS[0] and x.corrected_from is None]
    for x in rng.sample([x for x in on_time if x.value >= Decimal("10.5")], 4):
        x.value, x.censor = Decimal("14.0"), ">"  # reported as ">14.0"
    healthy = [x for x in on_time if x.value < Decimal("6.0") and x.censor is None]
    for x in rng.sample(healthy, 2):
        x.value, x.censor = Decimal("4.0"), "<"  # reported as "<4.0"

    late_claims = [c for c in world.claims if date(2025, 10, 1) <= c.fill_date <= MY_END and c.reversed_on is None]
    for c in rng.sample(late_claims, 40):
        c.adjudicated = EXTRACTS[0] + timedelta(days=rng.randint(0, 6))  # adjudicated late, second extract

    # A blank item never becomes zero: these PHQ-9s are incomplete and must not count as screened.
    complete_2025 = [s for s in world.screenings if s.phq9_complete and s.screen_date <= MY_END]
    for s in rng.sample(complete_2025, 15):
        for i in rng.sample(range(8), rng.randint(1, 2)):
            s.phq9[i] = None
        s.phq9_reported = None

    # Results first sent in the third extract use the laboratory's new layout: new accession numbers,
    # and part of its network reports HbA1c in IFCC mmol/mol.
    for x in world.labs:
        if delivery_index(x.resulted_on) == 2:
            x.lab_result_id = "ACC" + x.lab_result_id[1:]
            if x.analyte == "HBA1C":
                x.report_unit = "mmol/mol" if rng.random() < 0.4 else "%"
            else:
                x.report_unit = "mg/dL"
