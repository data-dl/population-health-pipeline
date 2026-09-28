"""Patient roster: full snapshots, kept as history (slowly changing dimension, type 2)."""

from __future__ import annotations

from ..warehouse import Warehouse
from .base import FeedSpec, mapped, meta_select, parsed_date, pipeline_columns, text

CLEAN = [
    "patient_id",
    "first_name",
    "last_name",
    "birth_date",
    "sex",
    "race",
    "ethnicity",
    "race_ethnicity",
    "language",
    "language_group",
    "address_line1",
    "city",
    "zip",
    "phone",
    "email",
    "payer_type",
    "site_id",
    "pcp_name",
    "care_manager_name",
    "problem_list",
    "registration_date",
]


def stage_select(rel: str, fmt: str) -> str:
    digits = f"regexp_replace(coalesce({text('phone')}, ''), '[^0-9]', '', 'g')"
    return f"""
select {meta_select()}, {pipeline_columns()},
    {text("patient_id")} as patient_id_raw,
    {text("patient_id")} as patient_id,
    {text("first_name")} as first_name,
    {text("last_name")} as last_name,
    {text("birth_date")} as birth_date_raw,
    {parsed_date("birth_date", fmt)} as birth_date,
    {text("sex")} as sex_raw,
    {mapped("sex", text("sex"))} as sex,
    {text("race")} as race,
    {text("ethnicity")} as ethnicity,
    case when {mapped("ethnicity", text("ethnicity"))} = 'Hispanic or Latino' then 'Hispanic or Latino'
         else coalesce({mapped("race", text("race"))}, 'Unknown') end as race_ethnicity,
    {text("language")} as language_raw,
    {mapped("language", text("language"))} as language,
    case when {text("language")} is null then 'Unknown'
         when {mapped("language", text("language"))} = 'en' then 'English'
         when {mapped("language", text("language"))} = 'es' then 'Spanish'
         else 'Other' end as language_group,
    {text("address_line1")} as address_line1,
    {text("city")} as city,
    {text("zip")} as zip_raw,
    case when regexp_full_match({text("zip")}, '[0-9]{{5}}') then {text("zip")} end as zip,
    {text("phone")} as phone_raw,
    case when length({digits}) = 10 then {digits} end as phone,
    lower({text("email")}) as email,
    {text("payer_type")} as payer_type_raw,
    {mapped("payer_type", text("payer_type"))} as payer_type,
    {text("site_id")} as site_id,
    {text("pcp_name")} as pcp_name,
    {text("care_manager_name")} as care_manager_name,
    upper(replace({text("problem_list")}, ' ', '')) as problem_list,
    {text("registration_date")} as registration_date_raw,
    {parsed_date("registration_date", fmt)} as registration_date
from ({rel}) t
"""


def curate(wh: Warehouse, ctx: dict) -> dict[str, int]:
    wh.execute("""
        create or replace table curated.patients as
        select patient_id, first_name, last_name, birth_date, sex, race_ethnicity, language, language_group,
               address_line1, city, zip, phone, email, coalesce(payer_type, 'unknown') as payer_type, site_id,
               pcp_name, care_manager_name, problem_list, registration_date, _valid_from as attributed_since
        from clean.patients_history
        where _is_current
        order by patient_id
    """)
    wh.execute("""
        create or replace table curated.patient_conditions as
        with codes as (
            select patient_id, unnest(string_split(problem_list, ';')) as icd10_code
            from curated.patients where problem_list is not null and problem_list <> '')
        select distinct c.patient_id, c.icd10_code, p.condition_group
        from codes c
        join ref.condition_prefixes p on c.icd10_code like p.prefix || '%'
        order by c.patient_id, c.icd10_code
    """)
    wh.execute("""
        create or replace table curated.patient_attribution_history as
        select patient_id, site_id, pcp_name, care_manager_name, _valid_from as valid_from, _valid_to as valid_to,
               _is_current as is_current, _removed as removed
        from clean.patients_history
        order by patient_id, _valid_from
    """)
    return {
        t: wh.scalar(f"select count(*) from curated.{t}")
        for t in ("patients", "patient_conditions", "patient_attribution_history")
    }


def build(settings) -> FeedSpec:
    return FeedSpec("roster", stage_select, CLEAN, CLEAN, curate, clean_table="patients_history", scd2=True)
