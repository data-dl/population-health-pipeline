"""What every feed module provides, and the SQL helpers they share.

A feed module supplies three things the contract cannot: how to type and standardise the canonical
text columns (`stage_select`), which columns identify a version of a row (`hash_columns`), and how to
rebuild its curated tables from clean (`curate`). Everything else - files, layouts, rules, merge,
gates - is generic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..warehouse import Warehouse, lit

META = ["_load_id", "_row_num", "_file", "_source", "_layout", "_extract_date", "_arrival_date"]


@dataclass(frozen=True)
class FeedSpec:
    feed: str
    stage_select: Callable[[str, str], str]  # (text relation sql, date format) -> select
    clean_columns: list[str]  # business columns kept in clean
    hash_columns: list[str]  # the subset that defines a version of a row
    curate: Callable[[Warehouse, dict], dict[str, int]]  # rebuild curated.*; returns row counts
    clean_table: str
    scd2: bool = False


def text(col: str) -> str:
    """A text column from the supplier file, trimmed, blank as NULL."""
    return f"nullif(trim(t.{col}), '')"


def whole_number(expr: str) -> str:
    """An integer only when the text is written as one. (A plain cast would round '2.5' to 3.)"""
    return f"case when regexp_full_match({expr}, '-?[0-9]+') then cast({expr} as integer) end"


def parsed_date(col: str, fmt: str) -> str:
    return f"try_strptime({text(col)}, {lit(fmt)})::date"


def mapped(map_name: str, expr: str) -> str:
    """Look a value up in ref.code_map; NULL when the value is not listed (never guessed)."""
    return (
        f"(select m.canonical from ref.code_map m where m.map_name = {lit(map_name)} "
        f"and m.raw_value = upper(trim({expr})))"
    )


def meta_select() -> str:
    return ", ".join(f"t.{c}" for c in META)


def pipeline_columns() -> str:
    return (
        "'new' as _candidate, cast(null as varchar) as _row_hash, 'pending' as _dq_status, "
        "cast(null as varchar) as _rule_ids"
    )


def row_hash_sql(columns: list[str], alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    parts = ", ".join(f"coalesce(cast({prefix}{c} as varchar), '~')" for c in columns)
    return f"md5(concat_ws('|', {parts}))"
