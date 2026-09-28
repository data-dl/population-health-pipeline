"""Laboratory results: both supplier layouts to one table, results parsed and put in standard units.

Censored results ("<4.0", ">14.0") keep the bound and a censor mark. Units come from the file (layout
2) or from the code (layout 1); IFCC mmol/mol HbA1c is converted to NGSP % (0.09148 x IFCC + 2.152,
rounded half up to one decimal, in exact decimal arithmetic). A unit the contract does not list is
never converted by guesswork: the result has no standard value and its row is held.
"""

from __future__ import annotations

from ..warehouse import Warehouse
from .base import FeedSpec, meta_select, parsed_date, pipeline_columns, text

CLEAN = [
    "lab_result_id",
    "patient_id",
    "collected_date",
    "loinc_code",
    "analyte",
    "test_name",
    "result_raw",
    "result_value",
    "censor",
    "units",
    "value_std",
    "reference_range",
    "abnormal_flag",
    "specimen_id",
]
HASH = [
    "lab_result_id",
    "patient_id",
    "collected_date",
    "loinc_code",
    "analyte",
    "result_value",
    "censor",
    "units",
    "value_std",
    "specimen_id",
]
NUMBER = "[<>]? ?[0-9]+([.][0-9]+)?"


def stage_select(rel: str, fmt: str) -> str:
    return f"""
select p.*,
    lu.unit is not null as _unit_known,
    case lu.convert
        when 'none' then round(p.result_value, 1)
        when 'ifcc_to_ngsp' then round(p.result_value * 0.09148 + 2.152, 1)
    end::decimal(10, 1) as value_std,
    a.plausible_min as _plausible_min,
    a.plausible_max as _plausible_max
from (
    select {meta_select()}, {pipeline_columns()},
        {text("lab_result_id")} as lab_result_id,
        {text("patient_id")} as patient_id_raw,
        {text("patient_id")} as patient_id,
        {text("collected_date")} as collected_date_raw,
        {parsed_date("collected_date", fmt)} as collected_date,
        upper({text("loinc_code")}) as loinc_code,
        lc.analyte,
        {text("test_name")} as test_name,
        {text("result")} as result_raw,
        case when regexp_full_match({text("result")}, '[<>] ?[0-9]+([.][0-9]+)?') then left({text("result")}, 1)
        end as censor,
        case when regexp_full_match({text("result")}, '{NUMBER}')
             then try_cast(regexp_replace({text("result")}, '^[<>] ?', '') as decimal(10, 2)) end as result_value,
        {text("units")} as units_raw,
        coalesce({text("units")}, lc.default_unit) as units,
        {text("reference_range")} as reference_range,
        upper({text("abnormal_flag")}) as abnormal_flag,
        {text("specimen_id")} as specimen_id
    from ({rel}) t
    left join ref.lab_codes lc on lc.loinc_code = upper({text("loinc_code")})
) p
left join ref.lab_units lu on lu.analyte = p.analyte and lu.unit = p.units
left join ref.analytes a on a.analyte = p.analyte
"""


def curate(wh: Warehouse, ctx: dict) -> dict[str, int]:
    wh.execute("""
        create or replace table curated.lab_results as
        select l.lab_result_id, l.patient_id, l.collected_date, l.analyte, l.loinc_code, l.value_std as value,
               a.standard_unit as unit, l.censor, l.abnormal_flag, l._layout as source_layout,
               row_number() over (partition by l.patient_id, l.analyte
                                  order by l.collected_date desc, l.lab_result_id desc) = 1 as is_most_recent
        from clean.labs l
        join ref.analytes a on a.analyte = l.analyte
        order by l.patient_id, l.analyte, l.collected_date
    """)
    return {"lab_results": wh.scalar("select count(*) from curated.lab_results")}


def build(settings) -> FeedSpec:
    return FeedSpec("labs", stage_select, CLEAN, HASH, curate, clean_table="labs")
