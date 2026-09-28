"""De-identification: a demo-site copy of the documents and a Safe Harbor-style analytic extract.

The crosswalk (restricted.deid_crosswalk) is the only link between a real value and its surrogate.
It lives in the warehouse's restricted schema and is never exported. Surrogates are drawn from a
seeded generator in order of first appearance, not computed from the real value; the same real
value gets the same surrogate on every run because the crosswalk remembers it.
"""

from __future__ import annotations

import copy
import csv
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from ..documents.builders import slug
from ..documents.docstore import DocumentStore
from ..settings import Settings
from ..warehouse import Warehouse
from . import vocabulary as V

ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
WORDS = re.compile(r"[a-z0-9]+")


def street_key(text: str) -> str:
    """Lower-case words and numbers only, so "12 Oak St." in a note matches "12 OAK ST" on the roster."""
    return " ".join(WORDS.findall(text.lower()))


def load_policy(settings: Settings, name: str) -> dict:
    return yaml.safe_load((settings.config_dir / "deid" / f"{name}.yaml").read_text(encoding="utf-8"))


class Crosswalk:
    def __init__(self, wh: Warehouse, seed: int):
        self.wh, self.seed = wh, seed
        wh.execute("""create table if not exists restricted.deid_crosswalk (
            kind varchar, real_value varchar, surrogate varchar, created_run varchar,
            primary key (kind, real_value))""")
        self.map: dict[tuple[str, str], str] = {}
        self.taken: dict[str, set[str]] = {}
        for r in wh.rows("select kind, real_value, surrogate from restricted.deid_crosswalk"):
            self.map[(r["kind"], r["real_value"])] = r["surrogate"]
            self.taken.setdefault(r["kind"], set()).add(r["surrogate"])
        self.rngs: dict[str, random.Random] = {}
        self.new: list[tuple[str, str, str]] = []

    def get(
        self, kind: str, real: str, make: Callable[[random.Random], str], forbidden=frozenset(), unique: bool = False
    ) -> str:
        """The surrogate for `real`, drawing a new one the first time. `unique` for identifiers: no two
        real values may share a surrogate. `forbidden` values (real names, real numbers) are never drawn."""
        key = (kind, real)
        if key in self.map:
            return self.map[key]
        taken = self.taken.setdefault(kind, set())
        rng = self.rngs.setdefault(kind, random.Random(f"{self.seed}:{kind}:{len(taken)}"))
        for _ in range(10_000):
            candidate = make(rng)
            if (
                (not unique or candidate not in taken)
                and candidate not in forbidden
                and candidate.lower() not in forbidden
            ):
                break
        else:
            raise RuntimeError(f"could not draw a unique {kind} surrogate")
        self.map[key] = candidate
        taken.add(candidate)
        self.new.append((kind, real, candidate))
        return candidate

    def flush(self, run_id: str) -> int:
        self.wh.bulk_insert(
            "restricted.deid_crosswalk",
            ["kind", "real_value", "surrogate", "created_run"],
            [(k, r, s, run_id) for k, r, s in self.new],
        )
        n, self.new = len(self.new), []
        return n


@dataclass
class RealValues:
    """What must never appear in a de-identified output, read from the published roster."""

    mrns: set[str] = field(default_factory=set)
    full_names: set[str] = field(default_factory=set)  # "first last", lower case
    phones: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)  # lower case
    streets: set[str] = field(default_factory=set)  # as street_key()
    workforce: set[str] = field(default_factory=set)
    by_patient: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, wh: Warehouse) -> RealValues:
        rv = cls()
        for p in wh.rows("select * from published.patients"):
            rv.mrns.add(p["patient_id"])
            rv.full_names.add(f"{p['first_name']} {p['last_name']}".lower())
            if p["phone"]:
                rv.phones.add(p["phone"])
            if p["email"]:
                rv.emails.add(p["email"].lower())
            if p["address_line1"]:
                rv.streets.add(street_key(p["address_line1"]))
            for name in (p["pcp_name"], p["care_manager_name"]):
                if name:
                    rv.workforce.add(name.lower())
            rv.by_patient[p["patient_id"]] = p
        return rv


def apply_path(doc: dict, path: str, fn: Callable) -> None:
    """Apply `fn` to every value at `path`: dots for nesting, `*` for any key, `[]` for list items."""
    parts = path.split(".")

    def walk(node, i: int) -> None:
        part = parts[i]
        is_list = part.endswith("[]")
        key = part[:-2] if is_list else part
        if not isinstance(node, dict):
            return
        keys = list(node) if key == "*" else ([key] if key in node else [])
        last = i == len(parts) - 1
        for k in keys:
            child = node[k]
            if is_list:
                if not isinstance(child, list):
                    continue
                if last:
                    node[k] = [fn(v) for v in child]
                else:
                    for item in child:
                        walk(item, i + 1)
            elif last:
                node[k] = fn(child)
            elif child is not None:
                walk(child, i + 1)

    walk(doc, 0)


class DemoSite:
    """The demo_site policy's transforms, bound to a crosswalk and the real values to avoid."""

    def __init__(self, cw: Crosswalk, real: RealValues):
        self.cw, self.real = cw, real
        self.forbidden_names = real.full_names | real.workforce

    def patient_id(self, real_id: str) -> str:
        return self.cw.get("patient", real_id, lambda r: f"D{r.randint(0, 9_999_999):07d}", self.real.mrns, unique=True)

    def shift(self, real_id: str) -> int:
        return int(self.cw.get("date_shift", real_id, lambda r: str(r.choice([d for d in range(-180, 181) if d != 0]))))

    def name(self, real_id: str) -> tuple[str, str]:
        def draw(r: random.Random) -> str:
            return f"{r.choice(V.FIRST)} {r.choice(V.LAST)}"

        full = self.cw.get("patient_name", real_id, draw, self.forbidden_names)
        first, last = full.split(" ", 1)
        return first, last

    def workforce(self, real_name: str | None) -> str | None:
        if not real_name:
            return real_name
        return self.cw.get(
            "workforce",
            real_name,
            lambda r: f"{r.choice(V.FIRST)} {r.choice(V.LAST)}",
            self.forbidden_names,
            unique=True,
        )

    def transforms(self, doc: dict, collection: str) -> dict[str, Callable]:
        ctx: dict = {}
        if collection == "patients":
            pid = doc["_id"]
            ctx = {
                "pid": pid,
                "shift": self.shift(pid),
                "name": self.name(pid),
                "real": self.real.by_patient.get(pid, {}),
            }
        original = copy.deepcopy(doc)

        def shift_date(v):
            return (date.fromisoformat(v) + timedelta(days=ctx["shift"])).isoformat() if v else v

        def fake_zip(v):
            if not v:
                return v
            return self.cw.get("zip", ctx["pid"], lambda r: f"{v[:3]}{r.randint(0, 99):02d}", {v})

        def fake_email(v):
            if not v:
                return v
            n = self.cw.get("email_suffix", ctx["pid"], lambda r: str(r.randint(1, 99)))
            first, last = ctx["name"]
            return f"{first}.{last}{n}@example.org".lower()

        return {
            "surrogate_patient_id": self.patient_id,
            "fake_first_name": lambda v: ctx["name"][0],
            "fake_last_name": lambda v: ctx["name"][1],
            "shift_date": shift_date,
            "shift_dates_in_text": lambda v: ISO_DATE.sub(lambda m: shift_date(m.group()), v) if v else v,
            "fake_street": lambda v: (
                v
                and self.cw.get(
                    "street",
                    ctx["pid"],
                    lambda r: f"{r.randint(1, 9899)} {r.choice(V.STREETS)} {r.choice(V.SUFFIX)}",
                    self.real.streets,
                )
            ),
            "fake_zip_same_area": fake_zip,
            "fake_phone": lambda v: (
                v
                and self.cw.get(
                    "phone", ctx["pid"], lambda r: f"555{r.randint(2_000_000, 9_999_999)}", self.real.phones
                )
            ),
            "fake_email": fake_email,
            "fake_workforce_name": self.workforce,
            "fake_workforce_slug": lambda v: slug(self.workforce(original.get("careManager")) or v),
            "keep": lambda v: v,
        }


def deidentify_documents(settings: Settings, run_id: str) -> dict:
    policy = load_policy(settings, settings.config["deidentification"]["demo_policy"])
    source = DocumentStore(settings.docstore, "restricted")
    target = DocumentStore(settings.docstore, "demo_staging")
    report = {}
    with Warehouse(settings.warehouse_path) as wh:
        cw = Crosswalk(wh, int(policy["seed"]))
        engine = DemoSite(cw, RealValues.load(wh))
        for collection, spec in policy["collections"].items():
            out = []
            for doc in sorted(source.find(collection), key=lambda d: d["_id"]):
                fns = engine.transforms(doc, collection)
                new = copy.deepcopy(doc)
                for path, transform in spec["transforms"].items():
                    apply_path(new, path, fns[transform])
                out.append(new)
            report[collection] = target.sync(collection, out)
        report["crosswalk_new"] = cw.flush(run_id)
    return report


def build_safe_harbor_extract(settings: Settings, run_id: str) -> dict:
    policy = load_policy(settings, settings.config["deidentification"]["extract_policy"])
    restricted = set(policy.get("restricted_zip3", []))
    out = settings.extracts / "staging" / "safe_harbor_patients.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with Warehouse(settings.warehouse_path) as wh:
        cw = Crosswalk(wh, int(policy["seed"]))
        rows = wh.rows("""
            select p.patient_id, year(p.birth_date) as birth_year, f.age_my_end, f.age_band, p.sex,
                   left(p.zip, 3) as zip3, s.region, p.race_ethnicity, p.language_group, p.payer_type,
                   f.has_diabetes, f.has_hypertension, f.has_hyperlipidemia, f.has_depression,
                   f.active_my as seen_this_year
            from published.patients p
            join dbt_intermediate.int_patient_facts f using (patient_id)
            join ref.sites s on s.site_id = p.site_id
            order by p.patient_id""")
        measures = [r["measure_id"] for r in wh.rows("select measure_id from marts.measure_definitions order by 1")]
        results = {
            (r["patient_id"], r["measure_id"]): r["numerator"]
            for r in wh.rows("select patient_id, measure_id, numerator from marts.measure_patient_results")
        }
        header = list(policy["allowed_columns"]) + [f"{m.lower()}_{k}" for m in measures for k in ("eligible", "met")]
        records = []
        for r in rows:
            ninety = r["age_my_end"] >= 90
            record = {
                "record_id": cw.get(
                    "extract_patient", r["patient_id"], lambda g: f"R{g.randint(0, 9_999_999):07d}", unique=True
                ),
                "birth_year": None if ninety else r["birth_year"],
                "age_at_year_end": "90+" if ninety else r["age_my_end"],
                "age_band": r["age_band"],
                "sex": r["sex"],
                "zip3": None if not r["zip3"] else ("000" if r["zip3"] in restricted else r["zip3"]),
                "region": r["region"],
                "race_ethnicity": r["race_ethnicity"],
                "language_group": r["language_group"],
                "payer_type": r["payer_type"],
                "has_diabetes": r["has_diabetes"],
                "has_hypertension": r["has_hypertension"],
                "has_hyperlipidemia": r["has_hyperlipidemia"],
                "has_depression": r["has_depression"],
                "seen_this_year": r["seen_this_year"],
            }
            for m in measures:
                met = results.get((r["patient_id"], m))
                record[f"{m.lower()}_eligible"] = met is not None
                record[f"{m.lower()}_met"] = bool(met) if met is not None else None
            records.append(record)
        cw.flush(run_id)
    records.sort(key=lambda x: x["record_id"])
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    return {"rows": len(records), "path": str(out), "columns": len(header)}


def release_path(settings: Settings) -> Path:
    return settings.extracts / "safe_harbor_patients.csv"
