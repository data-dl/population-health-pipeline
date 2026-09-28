"""Monitoring: freshness, run health, the issue register.

Freshness answers the two questions a data team asks every morning, per supplier:
  - did the delivery arrive when it should? (ON_TIME, DUE today, LATE)
  - did what arrived make it all the way to published? (OK, or STUCK - and why)
alongside the newest event delivered against the newest event published, so a feed that arrives but
stalls shows how far behind it is.

The issue register turns findings into a steward's backlog: one issue per feed and rule for held
rows, per blocked file, per late or stuck delivery. Issues open, age, and resolve themselves when the
condition clears (rows released or superseded, file re-delivered, accepted or waived, delivery arrived).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .settings import Settings
from .warehouse import Warehouse, lit

# Record numbers (8 digits) and phone numbers (10) - but not the extract date inside a file name.
MASK = re.compile(r"(?<![0-9_])[0-9]{7,}(?![0-9])")
BRACKETS = re.compile(r"\[[^\]]*\]")


def mask(text: str | None) -> str:
    """Identifiers never reach run summaries: long digit runs are masked."""
    return MASK.sub(lambda m: "#" * len(m.group()), text or "")


def normalise_error(message: str) -> str:
    """Group messages that differ only in the identifiers inside them: anything in square brackets, and
    long digit runs."""
    return " ".join(mask(BRACKETS.sub("[]", message or "")).split())


# --------------------------------------------------------------------------------------------- freshness


def freshness(wh: Warehouse, settings: Settings, run_id: str, as_of: date) -> list[dict]:
    stale_after = settings.config.get("freshness", {}).get("stale_after_days", {})
    rows = []
    for feed in settings.feeds_order:
        contract = settings.contracts[feed]
        spec = contract.freshness
        published_latest = None
        if spec and wh.table_exists("published", spec["table"]):
            where = f"where {spec['where']}" if spec.get("where") else ""
            published_latest = wh.scalar(f"select max({spec['column']}) from published.{spec['table']} {where}")
        elif not spec:
            published_latest = wh.scalar(
                "select max(extract_date) from audit.loads where feed = ? and status = 'published'", [feed]
            )
        for source in contract.sources:
            files = wh.rows(
                "select * from audit.files where feed = ? and source = ? and status <> 'duplicate'", [feed, source]
            )
            if not files:
                continue
            last_arrival = max(f["arrival_date"] for f in files)
            last_extract = max(f["extract_date"] for f in files)
            expected = last_extract + timedelta(days=contract.cadence_days)
            if as_of < expected:
                delivery = "ON_TIME"
            elif as_of <= expected + timedelta(days=contract.grace_days):
                delivery = "DUE"
            else:
                delivery = "LATE"
            loads = wh.rows("select * from audit.loads where feed = ? and source = ?", [feed, source])
            if spec:
                delivered = [x["max_event_date"] for x in loads if x["max_event_date"]]
                delivered_latest = max(delivered) if delivered else None
            else:
                delivered_latest = last_extract
            stuck = [x for x in loads if x["status"] in ("blocked", "raw_loaded", "staged", "validated", "merged")]
            pipeline = "STUCK" if stuck else "OK"
            details = []
            if delivery == "LATE":
                details.append(f"expected by {expected.isoformat()}, {(as_of - expected).days} day(s) late")
            elif delivery == "DUE":
                details.append(f"expected today ({expected.isoformat()}), not yet arrived")
            for x in stuck:
                why = f"blocked by {x['blocked_rule']}" if x["status"] == "blocked" else f"waiting at {x['status']}"
                details.append(f"{x['name']} {why}")
            stale = (
                published_latest is not None
                and feed in stale_after
                and (as_of - published_latest).days > int(stale_after[feed])
            )
            if stale:
                details.append(f"newest published event is {(as_of - published_latest).days} days old")
            behind = (delivered_latest - published_latest).days if delivered_latest and published_latest else None
            overall = (
                "ACTION"
                if delivery == "LATE" or pipeline == "STUCK"
                else ("WATCH" if delivery == "DUE" or stale else "OK")
            )
            rows.append(
                {
                    "run_id": run_id,
                    "as_of": as_of,
                    "feed": feed,
                    "source": source,
                    "published_table": f"published.{spec['table']}" if spec else "audit.loads (snapshot extract)",
                    "event_column": spec["column"] if spec else "extract_date",
                    "last_arrival": last_arrival,
                    "last_extract": last_extract,
                    "expected_next": expected,
                    "delivery_status": delivery,
                    "latest_delivered_event": delivered_latest,
                    "latest_published_event": published_latest,
                    "days_behind": behind,
                    "pipeline_status": pipeline,
                    "overall": overall,
                    "detail": "; ".join(details) or None,
                }
            )
    for r in rows:
        wh.execute(
            "insert into audit.freshness values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                r[k]
                for k in (
                    "run_id",
                    "as_of",
                    "feed",
                    "source",
                    "published_table",
                    "event_column",
                    "last_arrival",
                    "last_extract",
                    "expected_next",
                    "delivery_status",
                    "latest_delivered_event",
                    "latest_published_event",
                    "days_behind",
                    "pipeline_status",
                    "overall",
                    "detail",
                )
            ],
        )
    return rows


# --------------------------------------------------------------------------------------------- run health


def run_summary(wh: Warehouse, run_id: str) -> dict:
    tasks = wh.rows(
        "select dag_id, task_id, status, message, errors from audit.task_results where run_id = ?", [run_id]
    )
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    failed = [
        {"dag": t["dag_id"], "task": t["task_id"], "message": mask(t["message"])}
        for t in tasks
        if t["status"] == "failed"
    ]
    errors: dict[str, int] = {}
    for f in failed:
        key = normalise_error(f["message"])
        errors[key] = errors.get(key, 0) + 1
    findings = wh.rows(
        """
        select feed, rule_id, action, candidate, count(*) as rows, min(detail) as example
        from audit.row_findings where run_id = ? group by all order by feed, rule_id, candidate""",
        [run_id],
    )
    for f in findings:
        f["example"] = mask(f["example"])
    return {
        "tasks": len(tasks),
        "by_status": by_status,
        "failed": failed,
        "distinct_errors": [{"message": k, "count": v} for k, v in sorted(errors.items())],
        "findings": findings,
    }


# --------------------------------------------------------------------------------------------- issues


def _current_conditions(wh: Warehouse, settings: Settings, run_id: str) -> dict[str, dict]:
    current: dict[str, dict] = {}
    for feed, contract in settings.contracts.items():
        if not wh.table_exists("quarantine", feed):
            continue
        for r in wh.rows(f"""
                select rule_id, count(*) as n from (
                    select unnest(string_split(_q_rule_ids, ',')) as rule_id
                    from quarantine.{feed} where _q_status = 'held')
                group by rule_id order by rule_id"""):
            rule = contract.rule(r["rule_id"]) if any(x.id == r["rule_id"] for x in contract.rules) else None
            current[f"{feed}:{r['rule_id']}"] = {
                "feed": feed,
                "rule_id": r["rule_id"],
                "scope": "rows",
                "title": rule.title if rule else r["rule_id"],
                "count": r["n"],
                "severity": "action",
                "detail": f"{r['n']} row(s) held; re-checked when the roster or reference data changes"
                if rule and rule.retryable
                else f"{r['n']} row(s) held for a steward",
            }
    for x in wh.rows("select * from audit.loads where status = 'blocked' order by feed, name"):
        current[f"{x['feed']}:{x['blocked_rule']}:{x['name']}"] = {
            "feed": x["feed"],
            "rule_id": x["blocked_rule"],
            "scope": "file",
            "count": 1,
            "severity": "action",
            "title": f"{x['name']} stopped",
            "detail": mask(x["status_reason"]),
        }
    for f in wh.rows("select * from audit.freshness where run_id = ? and delivery_status = 'LATE'", [run_id]):
        current[f"{f['feed']}:{f['source']}:late"] = {
            "feed": f["feed"],
            "rule_id": "SLA",
            "scope": "delivery",
            "count": 1,
            "severity": "action",
            "title": f"{f['source']} delivery late",
            "detail": f["detail"],
        }
    return current


def update_issues(wh: Warehouse, settings: Settings, run_id: str, as_of: date) -> dict:
    current = _current_conditions(wh, settings, run_id)
    existing = {r["issue_id"]: r for r in wh.rows("select * from audit.issues")}
    opened, resolved = [], []
    for issue_id, c in current.items():
        e = existing.get(issue_id)
        if e and e["status"] == "open":
            wh.execute(
                "update audit.issues set last_seen_run = ?, last_seen = ?, current_count = ?, "
                "peak_count = greatest(peak_count, ?), detail = ? where issue_id = ?",
                [run_id, as_of, c["count"], c["count"], c["detail"], issue_id],
            )
        else:
            if e:
                wh.execute("delete from audit.issues where issue_id = ?", [issue_id])
            wh.execute(
                "insert into audit.issues values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', null, null, null, ?)",
                [
                    issue_id,
                    c["feed"],
                    c["rule_id"],
                    c["scope"],
                    c["title"],
                    c["severity"],
                    run_id,
                    as_of,
                    run_id,
                    as_of,
                    c["count"],
                    c["count"],
                    c["detail"],
                ],
            )
            opened.append(issue_id)
    for issue_id, e in existing.items():
        if e["status"] == "open" and issue_id not in current:
            resolution = {
                "rows": "no rows held any more (released or superseded)",
                "file": "re-delivered, accepted or waived",
                "delivery": "delivery arrived",
            }[e["scope"]]
            wh.execute(
                "update audit.issues set status = 'resolved', resolution = ?, resolved_run = ?, resolved_on = ?, "
                "current_count = 0 where issue_id = ?",
                [resolution, run_id, as_of, issue_id],
            )
            resolved.append(issue_id)
    open_now = wh.rows("select * from audit.issues where status = 'open' order by first_seen, issue_id")
    for r in open_now:
        r["age_days"] = (as_of - r["first_seen"]).days
    return {"opened": opened, "resolved": resolved, "open": open_now}


def measure_changes(wh: Warehouse, run_id: str) -> list[dict]:
    """Network rates this run against the previous run that computed them (restatements show up here)."""
    rows = wh.rows(f"""
        with runs as (select distinct run_id from audit.measure_history where run_id <= {lit(run_id)}),
             ordered as (select run_id, row_number() over (order by run_id desc) as n from runs)
        select cur.measure_id, cur.numerator, cur.denominator, cur.rate, prev.rate as previous_rate,
               prev.numerator as previous_numerator, prev.denominator as previous_denominator
        from audit.measure_history cur
        left join audit.measure_history prev
          on prev.measure_id = cur.measure_id and prev.site_id = 'ALL'
         and prev.run_id = (select run_id from ordered where n = 2)
        where cur.run_id = {lit(run_id)} and cur.site_id = 'ALL'
        order by cur.measure_id""")
    return rows
