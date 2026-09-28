"""Feed operations, in the order a delivery moves through them.

Every operation works from durable state - audit.files, audit.loads, the staging and quarantine
tables - never from something held in memory by the previous step. Any one of them can be re-run on
its own (the troubleshooting path) and a run that died half-way resumes where it stopped.

    discover -> land (checksum, lake) -> load raw (layout match) -> file rules (volume, date format)
    -> stage (typed, canonical) -> re-check held rows -> row rules -> merge into clean
    -> curate -> gate -> publish
"""

from __future__ import annotations

import csv
import functools
import hashlib
import shutil
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .contracts import Contract
from .feeds import FeedSpec
from .rules import evaluate_rows
from .settings import Settings
from .warehouse import Warehouse, lit, q

LOAD_META = ["_row_hash", "_load_id", "_row_num", "_file", "_source", "_layout", "_extract_date"]
DONE = ("merged", "published")


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


# --------------------------------------------------------------------------------------------- discovery


@dataclass(frozen=True)
class Delivery:
    path: Path
    name: str
    arrival: date
    source: str
    extract: date
    redelivery: int


def _arrival(path: Path, inbox: Path) -> date:
    parts = path.relative_to(inbox).parts
    if len(parts) > 1:
        try:
            return date.fromisoformat(parts[0])
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def discover(settings: Settings, contract: Contract, as_of: date) -> list[Delivery]:
    """Files for this feed that had arrived by `as_of`.

    The demo inbox has one folder per arrival day, so a run "as of" a date sees exactly what had
    arrived by then; a flat drop folder works too, using each file's modification date.
    """
    inbox = settings.inbox
    if not inbox.exists():
        return []
    found = []
    for path in sorted(inbox.rglob("*")):
        if not path.is_file():
            continue
        match = contract.match_file(path.name)
        if match is None:
            continue
        arrival = _arrival(path, inbox)
        if arrival <= as_of:
            found.append(Delivery(path, path.name, arrival, match.source, match.extract, match.redelivery))
    return sorted(found, key=lambda d: (d.arrival, d.extract, d.redelivery, d.name))


def sha256(path: Path) -> str:
    stat = path.stat()
    return _sha256(str(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=4096)
def _sha256(path: str, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def new_deliveries(wh: Warehouse, settings: Settings, contract: Contract, as_of: date) -> list[Delivery]:
    known = {
        r["name"] + "@" + r["sha256"][:12]
        for r in wh.rows("select name, sha256 from audit.files where feed = ?", [contract.feed])
    }
    return [d for d in discover(settings, contract, as_of) if f"{d.name}@{sha256(d.path)[:12]}" not in known]


def recheck_basis(wh: Warehouse) -> str:
    """What held, retryable rows depend on: the published roster and the reference data (code maps,
    value sets). A row held against one version is worth re-checking once either has changed."""
    roster = content_hash(wh, "published.patients") if wh.table_exists("published", "patients") else "-"
    reference = wh.scalar("select reference_hash from ref.meta") if wh.table_exists("ref", "meta") else "-"
    return f"{roster}:{reference}"


def outstanding(wh: Warehouse, settings: Settings, contract: Contract, as_of: date, new_files: bool = True) -> dict:
    """What there is to do for a feed, read from durable state alone.

    new          files in the drop not seen before
    landed       files in the lake not loaded yet
    unfinished   loads an earlier run left part-way, and stopped loads a steward has since accepted
    publishable  merged loads that waited behind a stopped file now re-delivered, accepted or waived
    recheck      held, retryable rows, when the roster or reference data changed since their last check
    """
    feed = contract.feed
    work = {"new": len(new_deliveries(wh, settings, contract, as_of)) if new_files else 0}
    work["landed"] = wh.scalar("select count(*) from audit.files where feed = ? and status = 'landed'", [feed])
    work["unfinished"] = wh.scalar(
        "select count(*) from audit.loads where feed = ? and status in ('raw_loaded', 'staged', 'validated')", [feed]
    )
    work["publishable"] = wh.scalar(
        "select count(*) from audit.loads where feed = ? and status = 'merged' "
        "and not exists (select 1 from audit.loads b where b.feed = ? and b.status = 'blocked')",
        [feed, feed],
    )
    work["recheck"] = 0
    if not contract.snapshot and wh.table_exists("quarantine", feed):
        held = wh.scalar(f"select count(*) from quarantine.{feed} where _q_status = 'held' and _q_retryable")
        checked = wh.scalar("select basis from audit.recheck_basis where feed = ?", [feed])
        if held and checked != recheck_basis(wh):
            work["recheck"] = held
    return work


def land(wh: Warehouse, settings: Settings, contract: Contract, as_of: date, run_id: str) -> dict:
    """Copy each new file into the lake (never overwritten) and register it with its checksum."""
    landed, duplicates = [], []
    for d in new_deliveries(wh, settings, contract, as_of):
        digest = sha256(d.path)
        target = settings.lake / contract.feed / d.source / d.extract.isoformat() / d.name
        if target.exists() and sha256(target) != digest:
            target = target.with_name(f"{target.stem}.{digest[:8]}{target.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(d.path, target)
        same = wh.scalar("select name from audit.files where feed = ? and sha256 = ?", [contract.feed, digest])
        status, reason = ("duplicate", f"same content as {same}") if same else ("landed", None)
        wh.execute(
            "insert into audit.files values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                f"{d.name}@{digest[:12]}",
                contract.feed,
                d.source,
                d.name,
                d.arrival,
                d.extract,
                d.redelivery,
                digest,
                d.path.stat().st_size,
                str(target),
                run_id,
                status,
                reason,
            ],
        )
        (duplicates if same else landed).append(d.name)
    return {"landed": landed, "duplicates": duplicates}


# --------------------------------------------------------------------------------------------- raw


def read_supplier_csv(path: Path) -> tuple[list[str], list[tuple[int, list[str]]], list[tuple[int, int]]]:
    """Header, well-formed rows (1-based data row number, values) and malformed rows (number, width)."""
    good, bad = [], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader, [])]
        for number, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) != len(header):
                bad.append((number, len(row)))
            else:
                good.append((number, row))
    return header, good, bad


def text_relation(contract: Contract, load: dict) -> str:
    """The load's raw rows under canonical text column names, with the load's metadata."""
    source = contract.sources[load["source"]]
    layout = next(lay for lay in source.layouts if lay.version == load["layout_version"])
    inverse = {canonical: raw for raw, canonical in layout.columns.items()}
    parts = [
        "r._load_id",
        "r._row_num",
        f"{lit(load['name'])} as _file",
        f"{lit(load['source'])} as _source",
        f"{int(load['layout_version'])} as _layout",
        f"date {lit(str(load['extract_date']))} as _extract_date",
        f"date {lit(str(load['arrival_date']))} as _arrival_date",
    ]
    for column in contract.text_columns():
        parts.append(
            f"r.{q(inverse[column])} as {column}" if column in inverse else f"cast(null as varchar) as {column}"
        )
    return f"select {', '.join(parts)} from raw.{q(load['raw_table'])} r where r._load_id = {lit(load['load_id'])}"


def load_raw(wh: Warehouse, settings: Settings, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    """Match each landed file to a registered layout and load it, as text, into its raw table."""
    feed = contract.feed
    loaded, blocked, schema_changes = [], [], []
    tmp = settings.lake.parent / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for f in wh.rows(
        "select * from audit.files where feed = ? and status = 'landed' "
        "order by extract_date, arrival_date, redelivery, name",
        [feed],
    ):
        path = Path(f["lake_path"])
        header, rows, malformed = read_supplier_csv(path)
        load_id = f"{Path(f['name']).stem}.{f['sha256'][:8]}"
        source = contract.sources[f["source"]]
        layout = source.match_layout(header)
        base = [load_id, f["file_id"], run_id, feed, f["source"], f["name"], f["extract_date"], f["arrival_date"]]
        if layout is None:
            best, missing, unexpected = source.closest_layout(header)
            reason = (
                f"header matches no registered layout; closest is v{best.version} "
                f"(missing: {', '.join(missing) or 'none'}; unexpected: {', '.join(unexpected) or 'none'})"
            )
            wh.execute(
                "insert into audit.loads (load_id, file_id, run_id, feed, source, name, extract_date, "
                "arrival_date, rows_raw, status, status_reason, blocked_rule, loaded_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'blocked', ?, 'LAYOUT', ?)",
                base + [len(rows) + len(malformed), reason, now()],
            )
            wh.execute(
                "update audit.files set status = 'blocked', status_reason = ? where file_id = ?", [reason, f["file_id"]]
            )
            blocked.append(
                {
                    "file": f["name"],
                    "rule": "LAYOUT",
                    "reason": reason,
                    "closest": best.version,
                    "missing": missing,
                    "unexpected": unexpected,
                    "header": header,
                }
            )
            continue

        seen = [
            r["layout_version"]
            for r in wh.rows(
                "select distinct layout_version from audit.loads where feed = ? and source = ? "
                "and layout_version is not null",
                [feed, f["source"]],
            )
        ]
        if seen and layout.version not in seen:
            archived = None
            if wh.table_exists("clean", spec.clean_table):
                archived = f"{spec.clean_table}__before_{source.name}_v{layout.version}"
                wh.execute(f"create or replace table archive.{q(archived)} as select * from clean.{spec.clean_table}")
            wh.execute(
                "insert into audit.schema_changes values (?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, feed, source.name, max(seen), layout.version, archived, layout.change_record, now()],
            )
            schema_changes.append(
                {
                    "file": f["name"],
                    "from": max(seen),
                    "to": layout.version,
                    "change_record": layout.change_record,
                    "archived": archived,
                }
            )

        raw_table = f"{feed}__{source.name}__v{layout.version}"
        scratch = tmp / f"{load_id}.csv"
        with open(scratch, "w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(["_row_num"] + header)
            for number, row in rows:
                writer.writerow([number] + row)
        columns = "{" + ", ".join([f"{lit('_row_num')}: 'INTEGER'"] + [f"{lit(h)}: 'VARCHAR'" for h in header]) + "}"
        reader = (
            f"read_csv({lit(str(scratch))}, header = true, delim = ',', quote = '\"', escape = '\"', "
            f"auto_detect = false, columns = {columns})"
        )
        if not wh.table_exists("raw", raw_table):
            wh.execute(
                f"create table raw.{q(raw_table)} as select *, cast(null as varchar) as _load_id "
                f"from {reader} where false"
            )
        wh.execute(f"insert into raw.{q(raw_table)} by name select *, {lit(load_id)} as _load_id from {reader}")
        scratch.unlink()
        for number, width in malformed:
            wh.execute(
                "insert into audit.row_findings values (?, ?, ?, ?, 'new', 'ROW-SHAPE', 'quarantine', ?)",
                [run_id, feed, load_id, number, f"{width} fields where the header has {len(header)}"],
            )
        wh.execute(
            "insert into audit.loads (load_id, file_id, run_id, feed, source, name, extract_date, arrival_date, "
            "layout_version, raw_table, rows_raw, rows_malformed, status, loaded_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'raw_loaded', ?)",
            base + [layout.version, raw_table, len(rows) + len(malformed), len(malformed), now()],
        )
        wh.execute("update audit.files set status = 'loaded' where file_id = ?", [f["file_id"]])
        loaded.append({"file": f["name"], "load_id": load_id, "layout": layout.version, "rows": len(rows)})
    return {"loaded": loaded, "blocked": blocked, "schema_changes": schema_changes}


# --------------------------------------------------------------------------------------------- file rules


def detect_date_format(wh: Warehouse, relation: str, contract: Contract) -> tuple[str, dict[str, int]]:
    columns = [c for c in contract.date_columns if c in contract.text_columns()]
    counts = {}
    for fmt in contract.date_formats:
        parts = " + ".join(f"count(try_strptime(nullif(trim(t.{c}), ''), {lit(fmt)}))" for c in columns) or "0"
        counts[fmt] = int(wh.scalar(f"select {parts} from ({relation}) t") or 0)
    best = max(contract.date_formats, key=lambda fmt: (counts[fmt], -contract.date_formats.index(fmt)))
    return (best if counts[best] else contract.date_formats[0]), counts


def _volume_baseline(wh: Warehouse, contract: Contract, load: dict) -> float | None:
    history = wh.rows(
        """
        select load_id, rows_raw, status from audit.loads
        where feed = ? and source = ? and load_id <> ? and rows_raw is not null
        order by extract_date, arrival_date, load_id""",
        [contract.feed, load["source"], load["load_id"]],
    )
    if not contract.snapshot and history:
        history = history[1:]  # the first load of an incremental feed is the historical backfill
    usable = [h["rows_raw"] for h in history if h["status"] in DONE + ("validated", "staged")][-4:]
    return statistics.median(usable) if usable else None


def accepted_loads(wh: Warehouse) -> set[str]:
    """Loads a steward accepted after a rule stopped them (`pophealth accept`): they load regardless."""
    return {r["target"] for r in wh.rows("select target from audit.decisions where action = 'accept'")}


def validate_files(wh: Warehouse, settings: Settings, contract: Contract, run_id: str) -> dict:
    """File-level rules on the raw text: the date format in use, and whether the volume is plausible."""
    results = []
    accepted = accepted_loads(wh)
    for load in wh.rows(
        "select * from audit.loads where feed = ? and status = 'raw_loaded' "
        "order by extract_date, arrival_date, load_id",
        [contract.feed],
    ):
        relation = text_relation(contract, load)
        fmt, counts = detect_date_format(wh, relation, contract)
        latest = None
        if contract.time_column:
            latest = wh.scalar(
                f"select max(try_strptime(nullif(trim(t.{contract.time_column}), ''), {lit(fmt)}))::date "
                f"from ({relation}) t"
            )
            cutoff = load["extract_date"] - timedelta(days=1)  # bookings ahead of the extract are not events yet
            latest = min(latest, cutoff) if latest else None
        wh.execute(
            "update audit.loads set date_format = ?, max_event_date = ? where load_id = ?",
            [fmt, latest, load["load_id"]],
        )
        blocked_by = None
        for rule in (r for r in contract.rules if r.type in ("date_format", "volume")):
            outcome, detail, failed = "pass", None, 0
            if rule.type == "date_format":
                if fmt != contract.date_formats[0]:
                    outcome, failed = "warn", load["rows_raw"]
                    detail = f"file uses {fmt} ({counts[fmt]} dates parsed); parsed consistently with it"
            else:
                baseline = _volume_baseline(wh, contract, load)
                minimum = rule.params.get("min_baseline_rows", 0)
                if baseline is None or baseline < minimum:
                    outcome, detail = "skipped", "no comparable earlier delivery"
                else:
                    ratio = load["rows_raw"] / baseline
                    detail = f"{load['rows_raw']} rows against a usual {baseline:g} ({ratio:.2f}x)"
                    low = ratio < rule.params.get("min_ratio", 0)
                    high = "max_ratio" in rule.params and ratio > rule.params["max_ratio"]
                    if (low or high) and rule.action == "block" and load["load_id"] in accepted:
                        outcome, failed = "overridden", load["rows_raw"]
                        detail += "; accepted by a steward"
                    elif low or high:
                        outcome, failed = ("blocked" if rule.action == "block" else "warn"), load["rows_raw"]
                        if rule.action == "block":
                            blocked_by = blocked_by or rule.id
            wh.execute(
                "insert into audit.rule_results values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    contract.feed,
                    load["load_id"],
                    rule.id,
                    rule.type,
                    rule.action,
                    rule.dimension,
                    load["rows_raw"],
                    failed,
                    outcome,
                    detail,
                ],
            )
        if blocked_by:
            reason = next(
                r["detail"]
                for r in wh.rows(
                    "select detail from audit.rule_results where run_id = ? and load_id = ? and rule_id = ?",
                    [run_id, load["load_id"], blocked_by],
                )
            )
            wh.execute(
                "update audit.loads set status = 'blocked', blocked_rule = ?, status_reason = ? where load_id = ?",
                [blocked_by, reason, load["load_id"]],
            )
            wh.execute(
                "update audit.files set status = 'blocked', status_reason = ? where file_id = ?",
                [f"rule {blocked_by}: {reason}", load["file_id"]],
            )
        results.append({"load_id": load["load_id"], "date_format": fmt, "blocked_by": blocked_by})
    return {"files": results}


# --------------------------------------------------------------------------------------------- staging


def _align(wh: Warehouse, source: str, target: str) -> None:
    """Add to `target` any column `source` has and it lacks (schema evolution never drops data)."""
    s_schema, s_table = source.split(".")
    t_schema, t_table = target.split(".")
    have = set(wh.columns(t_schema, t_table))
    for row in wh.rows(
        "select column_name, data_type from information_schema.columns "
        "where table_schema = ? and table_name = ? order by ordinal_position",
        [s_schema, s_table],
    ):
        if row["column_name"] not in have:
            wh.execute(f"alter table {target} add column {q(row['column_name'])} {row['data_type']}")


def stage(wh: Warehouse, settings: Settings, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    """Type and standardise every row of every load waiting to be staged. No row is dropped here."""
    feed, table = contract.feed, f"staging.{contract.feed}"
    wh.execute(f"drop table if exists {table}")
    staged = {}
    for load in wh.rows(
        "select * from audit.loads where feed = ? and status in ('raw_loaded', 'staged', 'validated') "
        "order by extract_date, arrival_date, load_id",
        [feed],
    ):
        sql = spec.stage_select(text_relation(contract, load), load["date_format"] or contract.date_formats[0])
        if wh.table_exists("staging", feed):
            wh.execute(f"insert into {table} by name {sql}")
        else:
            wh.execute(f"create table {table} as {sql}")
        n = wh.scalar(f"select count(*) from {table} where _load_id = ?", [load["load_id"]])
        wh.execute("update audit.loads set status = 'staged', rows_staged = ? where load_id = ?", [n, load["load_id"]])
        staged[load["load_id"]] = n
    if not wh.table_exists("staging", feed) and wh.table_exists("quarantine", feed):
        keep = [c for c in wh.columns("quarantine", feed) if not c.startswith("_q_")]
        wh.execute(f"create table {table} as select {', '.join(q(c) for c in keep)} from quarantine.{feed} where false")
    return {"staged": staged}


def recheck_quarantine(wh: Warehouse, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    """Put held, retryable rows back in front of the rules, beside this run's new rows.

    Held rows are re-staged from their raw text, not copied from quarantine, so whatever has changed
    since - a patient added to the roster, a code mapping added to config/reference - applies to them.
    Only rows of merged loads are re-checked: a load an earlier run left part-way is being staged
    again now, and its rows are evaluated once, as new rows.
    """
    feed = contract.feed
    if not (wh.table_exists("quarantine", feed) and wh.table_exists("staging", feed)):
        return {"candidates": 0}
    held = f"quarantine.{feed}"
    loads = wh.rows(
        f"select * from audit.loads where status in {DONE} and load_id in (select distinct _load_id from {held} "
        f"where _q_status = 'held' and _q_retryable) order by extract_date, load_id"
    )
    for load in loads:
        rows = (
            f"select _row_num from {held} where _load_id = {lit(load['load_id'])} "
            f"and _q_status = 'held' and _q_retryable"
        )
        relation = f"{text_relation(contract, load)} and r._row_num in ({rows})"
        restaged = spec.stage_select(relation, load["date_format"] or contract.date_formats[0])
        wh.execute(f"insert into staging.{feed} by name select * replace ('retry' as _candidate) from ({restaged})")
    n = wh.scalar(f"select count(*) from staging.{feed} where _candidate = 'retry'")
    return {"candidates": n}


def validate_rows(wh: Warehouse, settings: Settings, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    feed = contract.feed
    if not wh.table_exists("staging", feed):
        return {}
    evaluate_rows(wh, settings, contract, spec, run_id)
    for rule in (r for r in contract.rules if r.type == "completeness"):
        c, missing = rule.params["column"], rule.params.get("missing_values", [])
        excluded = ", ".join(lit(v) for v in missing) or "''"
        for row in wh.rows(f"""
                select _load_id, count(*) as n, count(*) filter (where {c} is not null and {c} not in ({excluded}))
                       as present
                from staging.{feed} where _candidate = 'new' group by 1"""):
            share = row["present"] / max(1, row["n"])
            ok = share >= float(rule.params["min_share"])
            wh.execute(
                "insert into audit.rule_results values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    feed,
                    row["_load_id"],
                    rule.id,
                    rule.type,
                    rule.action,
                    rule.dimension,
                    row["n"],
                    row["n"] - row["present"],
                    "pass" if ok else "warn",
                    f"{share:.1%} recorded (minimum {float(rule.params['min_share']):.0%})",
                ],
            )
    result = _maintain_quarantine(wh, contract, run_id)
    # Held rows have now been checked against today's roster and reference data (see outstanding()).
    wh.execute(
        "insert or replace into audit.recheck_basis values (?, ?, ?, ?)", [feed, recheck_basis(wh), run_id, now()]
    )
    return result


def _maintain_quarantine(wh: Warehouse, contract: Contract, run_id: str) -> dict:
    feed = contract.feed
    st, qt = f"staging.{feed}", f"quarantine.{feed}"
    if not wh.table_exists("quarantine", feed):
        wh.execute(f"""create table {qt} as select *, cast(null as varchar) as _q_rule_ids,
            cast(null as boolean) as _q_retryable, cast(null as varchar) as _q_status,
            cast(null as varchar) as _q_first_run, cast(null as varchar) as _q_last_run,
            cast(null as varchar) as _q_released_run, cast(null as varchar) as _q_detail from {st} where false""")
    else:
        _align(wh, st, qt)
    # A run that stopped after this step left held rows for loads that are being evaluated again now:
    # they are replaced, never duplicated.
    wh.execute(f"delete from {qt} where _load_id in (select distinct _load_id from {st} where _candidate = 'new')")
    per_row = f"""
        select rf.load_id, rf.row_num, rf.candidate, bool_and(r.retryable) as retryable,
               string_agg(rf.rule_id || coalesce(': ' || nullif(rf.detail, ''), ''), '; ' order by rf.rule_id) as detail
        from audit.row_findings rf join _rules r on r.rule_id = rf.rule_id and r.action = 'quarantine'
        where rf.run_id = {lit(run_id)} and rf.feed = {lit(feed)}
        group by 1, 2, 3"""
    wh.execute(f"""
        insert into {qt} by name
        select s.*, s._rule_ids as _q_rule_ids, f.retryable as _q_retryable, 'held' as _q_status,
               {lit(run_id)} as _q_first_run, {lit(run_id)} as _q_last_run, cast(null as varchar) as _q_released_run,
               f.detail as _q_detail
        from {st} s join ({per_row}) f on f.load_id = s._load_id and f.row_num = s._row_num and f.candidate = 'new'
        where s._candidate = 'new' and s._dq_status = 'quarantine'""")
    held_new = wh.scalar(f"select count(*) from {st} where _candidate = 'new' and _dq_status = 'quarantine'")
    released = wh.scalar(f"select count(*) from {st} where _candidate = 'retry' and _dq_status = 'pass'")
    wh.execute(f"""
        update {qt} set _q_status = 'released', _q_released_run = {lit(run_id)}, _q_last_run = {lit(run_id)}
        from {st} s
        where s._candidate = 'retry' and s._dq_status = 'pass' and {qt}._load_id = s._load_id
          and {qt}._row_num = s._row_num and {qt}._q_status = 'held'""")
    wh.execute(f"""
        update {qt} set _q_last_run = {lit(run_id)}, _q_rule_ids = s._rule_ids
        from {st} s
        where s._candidate = 'retry' and s._dq_status = 'quarantine' and {qt}._load_id = s._load_id
          and {qt}._row_num = s._row_num and {qt}._q_status = 'held'""")
    superseded = 0
    if contract.snapshot and wh.scalar(
        f"select count(*) from {st} where _candidate = 'new' and _dq_status <> 'blocked'"
    ):
        current = f"select distinct _load_id from {st} where _candidate = 'new'"
        superseded = wh.scalar(f"select count(*) from {qt} where _q_status = 'held' and _load_id not in ({current})")
        wh.execute(
            f"update {qt} set _q_status = 'superseded', _q_last_run = {lit(run_id)} "
            f"where _q_status = 'held' and _load_id not in ({current})"
        )
    return {"held": held_new, "released": released, "superseded": superseded}


# --------------------------------------------------------------------------------------------- merge


def _key(contract: Contract, alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    parts = ", ".join(f"coalesce(cast({prefix}{k} as varchar), '~')" for k in contract.natural_key)
    return f"concat_ws('|', {parts})"


def _finish_merge(wh: Warehouse, contract: Contract, run_id: str, counts: dict[str, dict]) -> None:
    for load_id, c in counts.items():
        wh.execute(
            """update audit.loads set rows_inserted = ?, rows_updated = ?, rows_unchanged = ?, rows_deduped = ?,
                      rows_removed = ?, status = 'merged', merged_run = ? where load_id = ?""",
            [
                c.get("inserted", 0),
                c.get("updated", 0),
                c.get("unchanged", 0),
                c.get("deduped", 0),
                c.get("removed", 0),
                run_id,
                load_id,
            ],
        )
    # A blocked file is resolved once a later file for the same extract has been merged (a re-delivery).
    wh.execute(
        """
        update audit.loads set status = 'superseded', status_reason = coalesce(status_reason, '') || ' (re-delivered)'
        where feed = ? and status = 'blocked' and (source, extract_date) in (
            select source, extract_date from audit.loads where feed = ? and status = 'merged' and merged_run = ?)""",
        [contract.feed, contract.feed, run_id],
    )


def merge_clean(wh: Warehouse, settings: Settings, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    if not wh.table_exists("staging", contract.feed):
        return {}
    if spec.scd2:
        return _merge_snapshot(wh, contract, spec, run_id)
    return _merge_incremental(wh, contract, spec, run_id)


def _merge_incremental(wh: Warehouse, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    feed, st, clean = contract.feed, f"staging.{contract.feed}", f"clean.{spec.clean_table}"
    cols = ", ".join(spec.clean_columns + LOAD_META)
    if not wh.table_exists("clean", spec.clean_table):
        wh.execute(f"create table {clean} as select {cols}, cast(null as varchar) as _merged_run from {st} where false")
    wh.execute(f"""
        create or replace temp table _ranked as
        select {cols}, _candidate, _arrival_date, {_key(contract, "s")} as _key,
               row_number() over (partition by {_key(contract, "s")}
                                  order by _extract_date desc, _arrival_date desc, _load_id desc,
                                           _row_num desc) as _rn
        from {st} s where _dq_status = 'pass'""")
    wh.execute("create or replace temp table _batch as select * exclude (_rn) from _ranked where _rn = 1")
    wh.execute(f"""
        create or replace temp table _classified as
        select b._key, b._load_id, b._candidate,
               case when c._key is null then 'inserted' when c._row_hash <> b._row_hash then 'updated'
                    else 'unchanged' end as change
        from _batch b
        left join (select {_key(contract, "c")} as _key, _row_hash from {clean} c) c on c._key = b._key""")
    counts: dict[str, dict] = {}
    for r in wh.rows("select _load_id, change, count(*) as n from _classified where _candidate = 'new' group by 1, 2"):
        counts.setdefault(r["_load_id"], {})[r["change"]] = r["n"]
    # Rows a later version of the same key replaced within this batch (an exact duplicate inside a
    # file, or an older file's copy), counted on their own - not as a remainder - so the gate's
    # reconciliation of passed rows is a real check.
    for r in wh.rows("select _load_id, count(*) as n from _ranked where _candidate = 'new' and _rn > 1 group by 1"):
        counts.setdefault(r["_load_id"], {})["deduped"] = r["n"]
    for load_id in [
        r["load_id"] for r in wh.rows("select load_id from audit.loads where feed = ? and status = 'validated'", [feed])
    ]:
        counts.setdefault(load_id, {})
    released = wh.rows("select change, count(*) as n from _classified where _candidate = 'retry' group by 1")
    with wh.transaction():
        wh.execute(
            f"delete from {clean} where {_key(contract)} in (select _key from _classified where change = 'updated')"
        )
        wh.execute(f"""insert into {clean} by name
            select {cols}, {lit(run_id)} as _merged_run from _batch
            where _key in (select _key from _classified where change in ('inserted', 'updated'))""")
        _finish_merge(wh, contract, run_id, counts)
    totals = {k: sum(c.get(k, 0) for c in counts.values()) for k in ("inserted", "updated", "unchanged", "deduped")}
    totals["released_merged"] = sum(r["n"] for r in released)
    return totals


def _merge_snapshot(wh: Warehouse, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    """Type 2 history: a changed patient gets a new version; one missing from the snapshot is closed."""
    feed, st, hist = contract.feed, f"staging.{contract.feed}", f"clean.{spec.clean_table}"
    cols = ", ".join(spec.clean_columns + LOAD_META)
    if not wh.table_exists("clean", spec.clean_table):
        wh.execute(f"""create table {hist} as select {cols}, cast(null as date) as _valid_from,
            cast(null as date) as _valid_to, cast(null as boolean) as _is_current, cast(null as boolean) as _removed,
            cast(null as varchar) as _merged_run from {st} where false""")
    totals = {"inserted": 0, "updated": 0, "unchanged": 0, "deduped": 0, "removed": 0}
    loads = wh.rows(
        "select load_id, extract_date from audit.loads where feed = ? and status = 'validated' "
        "order by extract_date, arrival_date, load_id",
        [feed],
    )
    for load in loads:
        load_id, extract = load["load_id"], load["extract_date"]
        wh.execute(f"""
            create or replace temp table _ranked as
            select {cols}, {_key(contract, "s")} as _key,
                   row_number() over (partition by {_key(contract, "s")} order by _row_num desc) as _rn
            from {st} s where _candidate = 'new' and _load_id = {lit(load_id)} and _dq_status = 'pass'""")
        wh.execute("create or replace temp table _batch as select * exclude (_rn) from _ranked where _rn = 1")
        current = f"(select {_key(contract, 'h')} as _key, _row_hash from {hist} h where _is_current)"
        wh.execute(f"""
            create or replace temp table _classified as
            select b._key, case when c._key is null then 'inserted' when c._row_hash <> b._row_hash then 'updated'
                               else 'unchanged' end as change
            from _batch b left join {current} c on c._key = b._key
            union all
            select c._key, 'removed' from {current} c
            where c._key not in (select {_key(contract, "s")} from {st} s
                                 where _candidate = 'new' and _load_id = {lit(load_id)})""")
        c = {r["change"]: r["n"] for r in wh.rows("select change, count(*) as n from _classified group by 1")}
        c["deduped"] = wh.scalar("select count(*) from _ranked where _rn > 1")
        day_before = extract - timedelta(days=1)
        with wh.transaction():
            wh.execute(
                f"""update {hist} set _valid_to = ?, _is_current = false
                where _is_current and {_key(contract)} in (select _key from _classified where change = 'updated')""",
                [day_before],
            )
            wh.execute(
                f"""update {hist} set _valid_to = ?, _is_current = false, _removed = true
                where _is_current and {_key(contract)} in (select _key from _classified where change = 'removed')""",
                [day_before],
            )
            wh.execute(f"""insert into {hist} by name
                select {cols}, date {lit(str(extract))} as _valid_from, cast(null as date) as _valid_to,
                       true as _is_current, false as _removed, {lit(run_id)} as _merged_run
                from _batch where _key in (select _key from _classified where change in ('inserted', 'updated'))""")
            _finish_merge(wh, contract, run_id, {load_id: c})
        for k in totals:
            totals[k] += c.get(k, 0)
    return totals


# --------------------------------------------------------------------------------------------- curate, gate, publish


def curate(wh: Warehouse, settings: Settings, contract: Contract, spec: FeedSpec, run_id: str) -> dict:
    if not wh.table_exists("clean", spec.clean_table):
        return {}
    return spec.curate(wh, {"run_id": run_id, "settings": settings})


def reconciliation(wh: Warehouse, feed: str, run_id: str) -> list[dict]:
    """Every row of every file merged in this run is accounted for exactly once."""
    out = []
    for load in wh.rows("select * from audit.loads where feed = ? and merged_run = ?", [feed, run_id]):
        checks = {
            "raw = staged + malformed": load["rows_raw"] == load["rows_staged"] + load["rows_malformed"],
            "staged = passed + quarantined": load["rows_staged"] == load["rows_passed"] + load["rows_quarantined"],
            "passed = inserted + updated + unchanged + deduped": load["rows_passed"]
            == (load["rows_inserted"] + load["rows_updated"] + load["rows_unchanged"] + load["rows_deduped"]),
        }
        out.append(
            {
                "load_id": load["load_id"],
                "name": load["name"],
                **{k: bool(v) for k, v in checks.items()},
                "rows_raw": load["rows_raw"],
                "rows_staged": load["rows_staged"],
                "rows_malformed": load["rows_malformed"],
                "rows_passed": load["rows_passed"],
                "rows_quarantined": load["rows_quarantined"],
                "rows_inserted": load["rows_inserted"],
                "rows_updated": load["rows_updated"],
                "rows_unchanged": load["rows_unchanged"],
                "rows_deduped": load["rows_deduped"],
                "rows_removed": load["rows_removed"],
            }
        )
    return out


def qa_gate(wh: Warehouse, settings: Settings, contract: Contract, run_id: str) -> dict:
    feed = contract.feed
    reasons = []
    for load in wh.rows(
        "select name, blocked_rule, status_reason from audit.loads where feed = ? and status = 'blocked' "
        "order by extract_date, name",
        [feed],
    ):
        reasons.append(
            f"{load['name']} is blocked by {load['blocked_rule']} ({load['status_reason']}); "
            f"the feed does not publish past a gap until it is re-delivered, accepted or waived"
        )
    recon = reconciliation(wh, feed, run_id)
    for r in recon:
        broken = [k for k, v in r.items() if isinstance(v, bool) and not v]
        if broken:
            reasons.append(f"{r['name']}: row counts do not reconcile ({'; '.join(broken)})")
    decision = "HOLD" if reasons else "PUBLISH"
    wh.execute(
        "insert into audit.gate_decisions values (?, ?, ?, ?, ?)",
        [run_id, feed, decision, " | ".join(reasons) or None, now()],
    )
    return {"decision": decision, "reasons": reasons, "reconciliation": recon}


def content_hash(wh: Warehouse, table: str) -> str:
    return wh.scalar(
        f"select md5(coalesce(string_agg(h, '' order by h), '')) from (select md5(t::varchar) as h from {table} t)"
    )


def publish(wh: Warehouse, settings: Settings, contract: Contract, run_id: str) -> dict:
    """Replace the feed's published tables with its curated tables, all together or not at all."""
    feed, changed = contract.feed, {}
    with wh.transaction():
        for table in contract.published:
            new_hash = content_hash(wh, f"curated.{table}")
            old_hash = wh.scalar(
                "select content_hash from audit.publish_log where feed = ? and table_name = ? "
                "order by published_at desc, run_id desc limit 1",
                [feed, table],
            )
            wh.execute(f"create or replace table published.{table} as select * from curated.{table}")
            rows = wh.scalar(f"select count(*) from published.{table}")
            changed[table] = new_hash != old_hash
            wh.execute(
                "insert into audit.publish_log values (?, ?, ?, ?, ?, ?, ?)",
                [run_id, feed, table, rows, new_hash, changed[table], now()],
            )
        wh.execute(
            "update audit.loads set status = 'published', published_run = ? where feed = ? and status = 'merged'",
            [run_id, feed],
        )
    return {"changed": changed}
