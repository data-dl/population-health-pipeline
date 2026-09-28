"""Pharmacy claims: paid and reversed rows kept as sent; fills netted for adherence."""

from __future__ import annotations

from ..warehouse import Warehouse
from .base import FeedSpec, mapped, meta_select, parsed_date, pipeline_columns, text, whole_number

CLEAN = [
    "claim_id",
    "claim_status",
    "patient_id",
    "fill_date",
    "drug_name",
    "drug_class",
    "strength",
    "days_supply",
    "quantity",
    "adjudicated_date",
]


def stage_select(rel: str, fmt: str) -> str:
    return f"""
select {meta_select()}, {pipeline_columns()},
    {text("claim_id")} as claim_id,
    {text("patient_id")} as patient_id_raw,
    {text("patient_id")} as patient_id,
    {text("fill_date")} as fill_date_raw,
    {parsed_date("fill_date", fmt)} as fill_date,
    lower({text("drug_name")}) as drug_name,
    d.drug_class,
    {text("strength")} as strength,
    {text("days_supply")} as days_supply_raw,
    {whole_number(text("days_supply"))} as days_supply,
    try_cast({text("quantity")} as decimal(10, 2)) as quantity,
    {text("claim_status")} as claim_status_raw,
    {mapped("claim_status", text("claim_status"))} as claim_status,
    {text("adjudicated_date")} as adjudicated_date_raw,
    {parsed_date("adjudicated_date", fmt)} as adjudicated_date
from ({rel}) t
left join ref.drugs d on d.drug_name = lower({text("drug_name")})
"""


def curate(wh: Warehouse, ctx: dict) -> dict[str, int]:
    wh.execute("""
        create or replace table curated.pharmacy_claims as
        select claim_id, claim_status, patient_id, fill_date, drug_name, drug_class, strength, days_supply, quantity,
               adjudicated_date
        from clean.pharmacy
        order by claim_id, claim_status
    """)
    wh.execute("""
        create or replace table curated.pharmacy_fills as
        select p.claim_id, p.patient_id, p.fill_date, p.drug_name, p.drug_class, p.strength, p.days_supply,
               p.quantity, p.adjudicated_date
        from clean.pharmacy p
        where p.claim_status = 'P'
          and not exists (select 1 from clean.pharmacy r where r.claim_id = p.claim_id and r.claim_status = 'R')
        order by p.patient_id, p.drug_class, p.fill_date, p.claim_id
    """)
    return {t: wh.scalar(f"select count(*) from curated.{t}") for t in ("pharmacy_claims", "pharmacy_fills")}


def build(settings) -> FeedSpec:
    return FeedSpec("pharmacy", stage_select, CLEAN, CLEAN, curate, clean_table="pharmacy")
