"""Screenings: one wide row per encounter in, one scored row per instrument and one row per item out.

The SQL is generated from config/reference/instruments.yaml, so a new instrument is a config change.
Scoring rules: a total is computed only when every item is answered (a blank never becomes zero); the
platform's own total is compared with the computed one and never trusted; bands and positive screens
come from the instrument definition.
"""

from __future__ import annotations

from ..warehouse import Warehouse, lit
from .base import FeedSpec, mapped, meta_select, parsed_date, pipeline_columns, text, whole_number


def _instruments(settings) -> dict:
    return settings.reference("instruments.yaml")["instruments"]


def _scored(instruments: dict) -> dict:
    return {k: v for k, v in instruments.items() if v["kind"] == "scored"}


def _domains(instruments: dict) -> dict:
    return {k: v for k, v in instruments.items() if v["kind"] == "domains"}


def clean_columns(instruments: dict) -> list[str]:
    cols = ["screening_id", "patient_id", "screen_date", "admin_mode"]
    for spec in _scored(instruments).values():
        cols += spec["items"] + [spec["reported_total"]]
    for spec in _domains(instruments).values():
        cols += spec["items"]
    return cols


def make_stage_select(instruments: dict):
    scored, domains = _scored(instruments), _domains(instruments)

    def stage_select(rel: str, fmt: str) -> str:
        fields: list[str] = []
        item_bad, item_detail, sdoh_bad, sdoh_detail, mismatch, mismatch_detail = [], [], [], [], [], []
        for name, spec in scored.items():
            lo, hi = spec["item_min"], spec["item_max"]
            for item in spec["items"]:
                value = whole_number(text(item))
                bad = f"({text(item)} is not null and ({value} is null or {value} not between {lo} and {hi}))"
                fields.append(f"case when {value} between {lo} and {hi} then {value} end as {item}")
                item_bad.append(f"(case when {bad} then 1 else 0 end)")
                item_detail.append(f"case when {bad} then {lit(item + '=')} || {text(item)} end")
            reported = spec["reported_total"]
            fields.append(f"{whole_number(text(reported))} as {reported}")
            all_valid = " and ".join(f"{whole_number(text(i))} between {lo} and {hi}" for i in spec["items"])
            computed = " + ".join(whole_number(text(i)) for i in spec["items"])
            differs = (
                f"({all_valid} and {whole_number(text(reported))} is not null "
                f"and {whole_number(text(reported))} <> ({computed}))"
            )
            mismatch.append(differs)
            mismatch_detail.append(
                f"case when {differs} then {lit(name + ' reported ')} || {text(reported)} "
                f"|| ' computed ' || cast(({computed}) as varchar) end"
            )
        for spec in domains.values():
            allowed = ", ".join(lit(v) for v in spec["item_values"])
            for item in spec["items"]:
                value = f"upper({text(item)})"
                bad = f"({text(item)} is not null and {value} not in ({allowed}))"
                fields.append(f"{value} as {item}")
                sdoh_bad.append(f"(case when {bad} then 1 else 0 end)")
                sdoh_detail.append(f"case when {bad} then {lit(item + '=')} || {text(item)} end")
        return f"""
select {meta_select()}, {pipeline_columns()},
    {text("screening_id")} as screening_id,
    {text("patient_id")} as patient_id_raw,
    {text("patient_id")} as patient_id,
    {text("screen_date")} as screen_date_raw,
    {parsed_date("screen_date", fmt)} as screen_date,
    {text("admin_mode")} as admin_mode_raw,
    {mapped("admin_mode", text("admin_mode"))} as admin_mode,
    {", ".join(fields)},
    ({" + ".join(item_bad) or "0"}) as _bad_items,
    concat_ws('; ', {", ".join(item_detail) or "''"}) as _bad_items_detail,
    ({" + ".join(sdoh_bad) or "0"}) as _bad_sdoh,
    concat_ws('; ', {", ".join(sdoh_detail) or "''"}) as _bad_sdoh_detail,
    ({" or ".join(mismatch) or "false"}) as _total_mismatch,
    concat_ws('; ', {", ".join(mismatch_detail) or "''"}) as _total_mismatch_detail
from ({rel}) t
"""

    return stage_select


def make_curate(instruments: dict):
    scored, domains = _scored(instruments), _domains(instruments)

    def curate(wh: Warehouse, ctx: dict) -> dict[str, int]:
        parts = []
        for name, spec in scored.items():
            items = spec["items"]
            answered = " + ".join(f"(case when {i} is not null then 1 else 0 end)" for i in items)
            total = " + ".join(f"coalesce({i}, 0)" for i in items)
            n = len(items)
            safety = f"coalesce({spec['safety_item']}, 0) > 0" if spec.get("safety_item") else "false"
            parts.append(f"""
                select screening_id, patient_id, screen_date, admin_mode, {lit(name)} as instrument,
                       answered as items_answered, {n} as items_expected, answered = {n} as complete,
                       case when answered = {n} then total end as total_score,
                       {spec["reported_total"]} as reported_total,
                       case when answered = {n} then total >= {spec["positive_at"]} end as positive,
                       case when answered = {n} and {safety} then true else false end as safety_flag
                from (select *, {answered} as answered, {total} as total from clean.screenings)
                where answered > 0""")
        for name, spec in domains.items():
            items = spec["items"]
            answered = " + ".join(f"(case when {i} is not null then 1 else 0 end)" for i in items)
            needs = " + ".join(f"(case when {i} = {lit(spec['positive_value'])} then 1 else 0 end)" for i in items)
            n = len(items)
            parts.append(f"""
                select screening_id, patient_id, screen_date, admin_mode, {lit(name)} as instrument,
                       answered as items_answered, {n} as items_expected, answered = {n} as complete,
                       case when answered = {n} then needs end as total_score,
                       cast(null as integer) as reported_total,
                       needs > 0 as positive,
                       false as safety_flag
                from (select *, {answered} as answered, {needs} as needs from clean.screenings)
                where answered > 0""")
        wh.execute(f"""
            create or replace table curated.screening_events as
            with events as ({" union all ".join(parts)})
            select e.*, b.band as severity_band,
                   e.complete and row_number() over (
                       partition by e.patient_id, e.instrument, e.complete
                       order by e.screen_date desc, e.screening_id desc) = 1 as is_most_recent
            from events e
            left join ref.instrument_bands b
              on b.instrument = e.instrument and e.total_score between b.min_score and b.max_score
            order by e.patient_id, e.screen_date, e.instrument
        """)
        item_cols = [i for spec in instruments.values() for i in spec["items"]]
        casts = ", ".join(f"cast({i} as varchar) as {i}" for i in item_cols)
        cases = " ".join(
            f"when item_code in ({', '.join(lit(i) for i in spec['items'])}) then {lit(name)}"
            for name, spec in instruments.items()
        )
        wh.execute(f"""
            create or replace table curated.screening_items as
            select screening_id, patient_id, screen_date, case {cases} end as instrument, item_code, response
            from (unpivot (select screening_id, patient_id, screen_date, {casts} from clean.screenings)
                  on {", ".join(item_cols)} into name item_code value response)
            order by screening_id, item_code
        """)
        return {t: wh.scalar(f"select count(*) from curated.{t}") for t in ("screening_events", "screening_items")}

    return curate


def build(settings) -> FeedSpec:
    instruments = _instruments(settings)
    cols = clean_columns(instruments)
    return FeedSpec(
        "screenings", make_stage_select(instruments), cols, cols, make_curate(instruments), clean_table="screenings"
    )
