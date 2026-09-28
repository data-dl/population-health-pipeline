"""The DuckDB warehouse: schemas by layer, the audit tables, and a thin connection wrapper.

Layers, in the order data moves through them:

    raw        every supplier file exactly as sent (text), one table per source layout
    staging    this run's rows, typed and canonical, with their rule outcomes
    clean      the merged, de-duplicated history of every feed
    curated    derived tables rebuilt from clean whenever the feed loads
    published  what downstream consumers read; replaced only when a feed passes its gate
    quarantine rows held back, with the rules they failed and whether they can be retried
    audit      files, loads, rule results, findings, gates, publishes, tasks, issues, freshness
    archive    snapshots taken before a layout change
    ref        reference data loaded from config/reference
    restricted re-identification crosswalks for the de-identified copies (never exported)
    marts      built by dbt from published
"""

from __future__ import annotations

import csv
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

SCHEMAS = ["raw", "staging", "clean", "curated", "published", "quarantine", "audit", "archive", "ref", "restricted"]

AUDIT_DDL = """
create table if not exists audit.runs (
    run_id varchar primary key, as_of date, started_at timestamp, ended_at timestamp, status varchar,
    summary varchar);
create table if not exists audit.files (
    file_id varchar primary key, feed varchar, source varchar, name varchar, arrival_date date,
    extract_date date, redelivery integer, sha256 varchar, size_bytes bigint, lake_path varchar,
    registered_run varchar, status varchar, status_reason varchar);
create table if not exists audit.loads (
    load_id varchar primary key, file_id varchar, run_id varchar, feed varchar, source varchar, name varchar,
    extract_date date, arrival_date date, layout_version integer, raw_table varchar, date_format varchar,
    rows_raw integer, rows_malformed integer default 0, rows_staged integer default 0, rows_passed integer default 0,
    rows_quarantined integer default 0, rows_repaired integer default 0, rows_warned integer default 0,
    rows_inserted integer default 0, rows_updated integer default 0, rows_unchanged integer default 0,
    rows_deduped integer default 0, rows_removed integer default 0, status varchar, status_reason varchar,
    blocked_rule varchar, loaded_at timestamp, merged_run varchar, published_run varchar, max_event_date date);
create table if not exists audit.measure_history (
    run_id varchar, as_of date, measure_id varchar, site_id varchar, numerator integer, denominator integer,
    rate double);
create table if not exists audit.rule_results (
    run_id varchar, feed varchar, load_id varchar, rule_id varchar, rule_type varchar, action varchar,
    dimension varchar, rows_evaluated integer, rows_failed integer, outcome varchar, detail varchar);
create table if not exists audit.row_findings (
    run_id varchar, feed varchar, load_id varchar, row_num integer, candidate varchar, rule_id varchar,
    action varchar, detail varchar);
create table if not exists audit.gate_decisions (
    run_id varchar, feed varchar, decision varchar, reasons varchar, decided_at timestamp);
create table if not exists audit.publish_log (
    run_id varchar, feed varchar, table_name varchar, rows integer, content_hash varchar, changed boolean,
    published_at timestamp);
create table if not exists audit.task_results (
    run_id varchar, dag_id varchar, task_id varchar, status varchar, attempts integer, started_at timestamp,
    ended_at timestamp, duration_s double, message varchar, errors varchar, metrics varchar);
create table if not exists audit.issues (
    issue_id varchar primary key, feed varchar, rule_id varchar, scope varchar, title varchar, severity varchar,
    first_seen_run varchar, first_seen date, last_seen_run varchar, last_seen date, current_count integer,
    peak_count integer, status varchar, resolution varchar, resolved_run varchar, resolved_on date,
    detail varchar);
create table if not exists audit.schema_changes (
    run_id varchar, feed varchar, source varchar, from_version integer, to_version integer,
    archived_table varchar, change_record varchar, recorded_at timestamp);
create table if not exists audit.decisions (
    decision_id varchar, run_id varchar, action varchar, target varchar, reason varchar, decided_by varchar,
    decided_at timestamp);
create table if not exists audit.freshness (
    run_id varchar, as_of date, feed varchar, source varchar, published_table varchar, event_column varchar,
    last_arrival date, last_extract date, expected_next date, delivery_status varchar,
    latest_delivered_event date, latest_published_event date, days_behind integer, pipeline_status varchar,
    overall varchar, detail varchar);
create table if not exists audit.dbt_results (
    run_id varchar, unique_id varchar, resource_type varchar, name varchar, status varchar, message varchar,
    execution_time double);
create table if not exists audit.recheck_basis (
    feed varchar primary key, basis varchar, run_id varchar, checked_at timestamp);
"""


class Warehouse:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.con: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> Warehouse:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        return self

    def __exit__(self, *exc) -> None:
        if self.con is not None:
            self.con.close()
            self.con = None

    def execute(self, sql: str, params: list | tuple | None = None) -> duckdb.DuckDBPyConnection:
        return self.con.execute(sql, params or [])

    def rows(self, sql: str, params: list | tuple | None = None) -> list[dict[str, Any]]:
        cur = self.con.execute(sql, params or [])
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def scalar(self, sql: str, params: list | tuple | None = None) -> Any:
        row = self.con.execute(sql, params or []).fetchone()
        return row[0] if row else None

    def table_exists(self, schema: str, table: str) -> bool:
        return bool(
            self.scalar(
                "select count(*) from information_schema.tables where table_schema = ? and table_name = ?",
                [schema, table],
            )
        )

    def columns(self, schema: str, table: str) -> list[str]:
        return [
            r["column_name"]
            for r in self.rows(
                "select column_name from information_schema.columns where table_schema = ? and table_name = ? "
                "order by ordinal_position",
                [schema, table],
            )
        ]

    def bulk_insert(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        """Insert many rows at once through a scratch CSV (row-at-a-time inserts are slow in DuckDB).
        Values are written as text and cast by the target table's column types."""
        if not rows:
            return 0
        scratch = self.path.parent / "tmp" / f"bulk_{table.replace('.', '_')}.csv"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        with open(scratch, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows(["" if v is None else v for v in row] for row in rows)
        spec = "{" + ", ".join(f"{lit(c)}: 'VARCHAR'" for c in columns) + "}"
        cols = ", ".join(q(c) for c in columns)
        self.execute(
            f"insert into {table} ({cols}) select {cols} from read_csv({lit(scratch.as_posix())}, "
            f"header = true, auto_detect = false, columns = {spec})"
        )
        scratch.unlink()
        return len(rows)

    @contextmanager
    def transaction(self):
        self.con.execute("begin transaction")
        try:
            yield self
        except BaseException:
            self.con.execute("rollback")
            raise
        else:
            self.con.execute("commit")


def ensure_schemas(wh: Warehouse) -> None:
    for schema in SCHEMAS:
        wh.execute(f"create schema if not exists {schema}")
    for statement in AUDIT_DDL.split(";"):
        if statement.strip():
            wh.execute(statement)


def q(identifier: str) -> str:
    """Quote an identifier for DuckDB."""
    return '"' + identifier.replace('"', '""') + '"'


def lit(value: str) -> str:
    """Quote a string literal for DuckDB."""
    return "'" + str(value).replace("'", "''") + "'"
