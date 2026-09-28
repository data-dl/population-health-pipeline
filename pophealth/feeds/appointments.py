"""Appointments: two scheduling systems, two layouts, one table; statuses change between deliveries."""

from __future__ import annotations

from ..warehouse import Warehouse
from .base import FeedSpec, mapped, meta_select, parsed_date, pipeline_columns, text

CLEAN = ["source", "appointment_id", "patient_id", "site_id", "appt_date", "visit_type", "status", "provider_name"]


def stage_select(rel: str, fmt: str) -> str:
    return f"""
select {meta_select()}, {pipeline_columns()},
    t._source as source,
    {text("appointment_id")} as appointment_id,
    {text("patient_id")} as patient_id_raw,
    {text("patient_id")} as patient_id,
    upper({text("site_id")}) as site_id,
    {text("appt_date")} as appt_date_raw,
    {parsed_date("appt_date", fmt)} as appt_date,
    {text("visit_type")} as visit_type_raw,
    {mapped("visit_type", text("visit_type"))} as visit_type,
    {text("status")} as status_raw,
    {mapped("appointment_status", text("status"))} as status,
    {text("provider_name")} as provider_name
from ({rel}) t
"""


def curate(wh: Warehouse, ctx: dict) -> dict[str, int]:
    wh.execute("""
        create or replace table curated.appointments as
        select source, appointment_id, patient_id, site_id, appt_date, coalesce(visit_type, 'other') as visit_type,
               status, status = 'completed' as is_visit, provider_name
        from clean.appointments
        order by appt_date, source, appointment_id
    """)
    return {"appointments": wh.scalar("select count(*) from curated.appointments")}


def build(settings) -> FeedSpec:
    return FeedSpec("appointments", stage_select, CLEAN, CLEAN, curate, clean_table="appointments")
