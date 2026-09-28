"""The rule engine: every contract rule compiled to SQL over the staging table.

Each rule has an action. `repair` fixes what is provably fixable and records the fix. `quarantine`
holds the row back with the rule's id (and, if every failed rule is retryable, re-checks it on later
runs). `warn` records and lets the row through. `block` stops the whole file. A quarantine rule with
`block_if_rate_over` escalates: a few bad rows are held, too many stop the file - something upstream
is wrong, and loading the rest would hide it. A steward who judges otherwise accepts the file
(`pophealth accept`): it loads on the next run, the stop recorded as overridden and the failing rows
held one by one.

Every finding is written to audit.row_findings (run, feed, load, row, rule, action, detail), so any
number in a report can be traced back to the rows behind it.
"""

from __future__ import annotations

from .contracts import Contract, Rule
from .feeds.base import FeedSpec, row_hash_sql
from .settings import Settings
from .warehouse import Warehouse, lit


def predicate(rule: Rule, contract: Contract, settings: Settings, wh: Warehouse) -> tuple[str, str]:
    """SQL that is true for a failing staging row (alias s), and SQL for the finding's detail."""
    p, kind, feed = rule.params, rule.type, contract.feed
    if kind == "required":
        cols = p["columns"]
        blank = {c: f"(s.{c} is null or trim(cast(s.{c} as varchar)) = '')" for c in cols}
        return " or ".join(blank.values()), (
            "concat_ws(',', " + ", ".join(f"case when {b} then {lit(c)} end" for c, b in blank.items()) + ")"
        )
    if kind in ("parse", "mapped"):
        c = p["column"]
        return f"s.{c} is null and nullif(trim(s.{c}_raw), '') is not null", f"s.{c}_raw"
    if kind == "not_future":
        c = p["column"]
        return f"s.{c} > s._extract_date", f"cast(s.{c} as varchar)"
    if kind == "in_reference":
        c = p["column"]
        return (
            f"s.{c} is not null and s.{c} not in "
            f"(select value from ref.value_sets where domain = {lit(p['reference'])})"
        ), f"cast(s.{c} as varchar)"
    if kind == "on_roster":
        c = p["column"]
        if wh.table_exists("published", "patients"):
            return f"s.{c} is not null and s.{c} not in (select patient_id from published.patients)", f"s.{c}"
        return f"s.{c} is not null", f"s.{c}"
    if kind == "key_conflict":
        key = contract.natural_key
        same = " and ".join(f"o.{k} is not distinct from s.{k}" for k in key)
        return (
            f"s._candidate = 'new' and exists (select 1 from staging.{feed} o where o._candidate = 'new' "
            f"and o._load_id = s._load_id and {same} and o._row_hash <> s._row_hash)"
        ), ("concat_ws(':', " + ", ".join(f"cast(s.{k} as varchar)" for k in key) + ")")
    if kind == "closed_period":
        c = p["column"]
        return (
            f"year(s.{c}) = {settings.measurement_year} "
            f"and s._extract_date > date {lit(settings.closed_after.isoformat())}"
        ), f"cast(s.{c} as varchar)"
    if kind == "expression":
        return p["fail_when"], p.get("detail") or "null"
    raise ValueError(f"{rule.id}: {kind} is not a row-level rule")


def _row_rules(contract: Contract) -> list[Rule]:
    return [r for r in contract.rules if not r.file_level and r.type != "repair"]


def apply_repairs(wh: Warehouse, contract: Contract, run_id: str) -> None:
    table = f"staging.{contract.feed}"
    for rule in (r for r in contract.rules if r.type == "repair"):
        when = rule.params["when"]
        sets: dict = rule.params.get("set") or {}
        detail = rule.params.get("detail") or (next(iter(sets)) if sets else "null")
        wh.execute(
            f"""
            insert into audit.row_findings
            select ?, ?, _load_id, _row_num, _candidate, ?, 'repair', cast({detail} as varchar)
            from {table} where coalesce(({when}), false)
        """,
            [run_id, contract.feed, rule.id],
        )
        if sets:
            assignments = ", ".join(f"{col} = {expr}" for col, expr in sets.items())
            wh.execute(f"update {table} set {assignments} where coalesce(({when}), false)")


def evaluate_rows(wh: Warehouse, settings: Settings, contract: Contract, spec: FeedSpec, run_id: str) -> None:
    """Repairs, row hashes, every row rule, escalation, row status and load counters."""
    feed, table = contract.feed, f"staging.{contract.feed}"
    apply_repairs(wh, contract, run_id)
    wh.execute(f"update {table} set _row_hash = {row_hash_sql(spec.hash_columns)}")

    for rule in _row_rules(contract):
        pred, detail = predicate(rule, contract, settings, wh)
        wh.execute(
            f"""
            insert into audit.row_findings
            select ?, ?, s._load_id, s._row_num, s._candidate, ?, ?, cast({detail} as varchar)
            from {table} s where coalesce(({pred}), false)
        """,
            [run_id, feed, rule.id, rule.action],
        )

    loads = [
        r["_load_id"] for r in wh.rows(f"select distinct _load_id from {table} where _candidate = 'new' order by 1")
    ]
    new_rows = {
        r["_load_id"]: r["n"]
        for r in wh.rows(f"select _load_id, count(*) as n from {table} where _candidate = 'new' group by 1")
    }
    failed = {
        (r["load_id"], r["rule_id"]): r["n"]
        for r in wh.rows(
            "select load_id, rule_id, count(distinct row_num) as n from audit.row_findings "
            "where run_id = ? and feed = ? and candidate = 'new' group by 1, 2",
            [run_id, feed],
        )
    }

    blocked: dict[str, str] = {}
    accepted = {r["target"] for r in wh.rows("select target from audit.decisions where action = 'accept'")}
    for load in loads:
        for rule in contract.rules:
            if rule.file_level:
                continue
            n = failed.get((load, rule.id), 0)
            outcome = (
                "pass"
                if n == 0
                else {"repair": "repaired", "warn": "warn", "quarantine": "quarantined", "block": "blocked"}[
                    rule.action
                ]
            )
            limit = rule.params.get("block_if_rate_over")
            stops = n and (rule.action == "block" or (limit is not None and n / max(1, new_rows[load]) > float(limit)))
            if stops and load in accepted:
                outcome = "overridden"  # a steward accepted the file; its failing rows are still held one by one
            elif stops:
                outcome = "blocked"
                blocked.setdefault(load, rule.id)
            wh.execute(
                "insert into audit.rule_results values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, feed, load, rule.id, rule.type, rule.action, rule.dimension, new_rows[load], n, outcome, None],
            )

    # Row status: blocked (whole file stopped), quarantine (a quarantine rule failed), or pass.
    wh.execute("create or replace temp table _rules (rule_id varchar, action varchar, retryable boolean)")
    wh.con.executemany(
        "insert into _rules values (?, ?, ?)",
        [(r.id, r.action, r.retryable) for r in contract.rules] or [("-", "warn", False)],
    )
    blocked_list = ", ".join(lit(x) for x in blocked) or "''"
    wh.execute(f"""
        update {table} set _rule_ids = null,
            _dq_status = case when _candidate = 'new' and _load_id in ({blocked_list}) then 'blocked' else 'pass' end
    """)
    wh.execute(f"""
        update {table} s set _rule_ids = f.rule_ids,
            _dq_status = case when s._dq_status = 'blocked' then 'blocked' else 'quarantine' end
        from (select load_id, row_num, candidate, string_agg(rule_id, ',' order by rule_id) as rule_ids
              from (select distinct rf.load_id, rf.row_num, rf.candidate, rf.rule_id
                    from audit.row_findings rf join _rules r on r.rule_id = rf.rule_id and r.action = 'quarantine'
                    where rf.run_id = {lit(run_id)} and rf.feed = {lit(feed)})
              group by 1, 2, 3) f
        where f.load_id = s._load_id and f.row_num = s._row_num and f.candidate = s._candidate
    """)

    for load in loads:
        counts = wh.rows(
            f"""
            select count(*) as staged,
                   count(*) filter (where _dq_status = 'pass') as passed,
                   count(*) filter (where _dq_status = 'quarantine') as quarantined
            from {table} where _candidate = 'new' and _load_id = ?""",
            [load],
        )[0]
        touched = {
            r["action"]: r["n"]
            for r in wh.rows(
                """
            select action, count(distinct row_num) as n from audit.row_findings
            where run_id = ? and feed = ? and load_id = ? and candidate = 'new' and action in ('repair', 'warn')
            group by 1""",
                [run_id, feed, load],
            )
        }
        status = "blocked" if load in blocked else "validated"
        wh.execute(
            """
            update audit.loads set rows_staged = ?, rows_passed = ?, rows_quarantined = ?, rows_repaired = ?,
                   rows_warned = ?, status = ?, blocked_rule = coalesce(?, blocked_rule),
                   status_reason = case when ? then 'stopped by a row rule over its threshold' else status_reason end
            where load_id = ?""",
            [
                counts["staged"],
                counts["passed"] if status != "blocked" else 0,
                counts["quarantined"] if status != "blocked" else 0,
                touched.get("repair", 0),
                touched.get("warn", 0),
                status,
                blocked.get(load),
                load in blocked,
                load,
            ],
        )
        if load in blocked:
            wh.execute(
                "update audit.files set status = 'blocked', status_reason = ? where file_id = "
                "(select file_id from audit.loads where load_id = ?)",
                [f"rule {blocked[load]}", load],
            )
