"""Run evidence: what a person reads after a run (summary.md), what a machine can check
(manifest.json), and the CSVs behind every number in the summary.

Everything written here is deterministic for a given run - no wall-clock times, identifiers masked -
so a sample run can be committed and diffed.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

from . import __version__
from .ingest import reconciliation
from .monitoring import mask, measure_changes
from .settings import REPO, Settings
from .warehouse import Warehouse, lit


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or (list(rows[0].keys()) if rows else [])
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})


def _tree_hash(*paths: Path) -> str:
    digest = hashlib.sha256()
    for root in paths:
        for p in sorted(root.rglob("*")) if root.is_dir() else [root]:
            if p.is_file():
                digest.update(str(p.relative_to(root.parent)).replace("\\", "/").encode())
                digest.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


# --------------------------------------------------------------------------------------------- gathering


def gather(wh: Warehouse, settings: Settings, run_id: str, as_of: date, evidence_dir: Path) -> dict:
    loads = wh.rows(
        """
        select * from audit.loads where run_id = ? or merged_run = ? or published_run = ?
        order by feed, extract_date, name""",
        [run_id, run_id, run_id],
    )
    gates = {r["feed"]: r for r in wh.rows("select * from audit.gate_decisions where run_id = ?", [run_id])}
    published = wh.rows("select * from audit.publish_log where run_id = ? order by feed, table_name", [run_id])
    rule_titles = {(f, r.id): r for f, c in settings.contracts.items() for r in c.rules}
    fired = wh.rows(
        """
        select feed, rule_id, action, count(*) filter (where candidate = 'new') as rows_new,
               count(*) filter (where candidate = 'retry') as rows_rechecked
        from audit.row_findings where run_id = ? group by all order by feed, rule_id""",
        [run_id],
    )
    file_rules = wh.rows(
        """
        select r.feed, l.name, r.rule_id, r.outcome, r.detail from audit.rule_results r
        join audit.loads l using (load_id)
        where r.run_id = ? and r.rule_type in ('volume', 'date_format', 'completeness')
          and r.outcome not in ('pass', 'skipped')
        order by r.feed, l.name, r.rule_id""",
        [run_id],
    )
    freshness = wh.rows("select * from audit.freshness where run_id = ? order by feed, source", [run_id])
    schema_changes = wh.rows("select * from audit.schema_changes where run_id = ?", [run_id])
    issues = wh.rows("select * from audit.issues order by status desc, first_seen, issue_id")
    measures = []
    if wh.scalar("select count(*) from audit.measure_history where run_id = ?", [run_id]):
        measures = measure_changes(wh, run_id)
    dbt = wh.rows("select status, count(*) as n from audit.dbt_results where run_id = ? group by 1", [run_id])
    held = []
    for feed in settings.feeds_order:
        if wh.table_exists("quarantine", feed):
            held += wh.rows(
                f"""
                select {lit(feed)} as feed, _file as file, _row_num as row, _q_rule_ids as rules, _q_status as status,
                       _q_first_run as first_held, _q_released_run as released, _q_detail as detail
                from quarantine.{feed}
                where _q_first_run = ? or _q_last_run = ?
                order by _file, _row_num""",
                [run_id, run_id],
            )
    for h in held:
        h["detail"] = mask(h["detail"])
    recon = [dict(feed=f, **r) for f in settings.feeds_order for r in reconciliation(wh, f, run_id)]
    deid = None
    deid_path = evidence_dir / "deid_verification.json"
    if deid_path.exists():
        deid = json.loads(deid_path.read_text(encoding="utf-8"))
    alerts = []
    alerts_path = evidence_dir / "alerts.jsonl"
    if alerts_path.exists():
        alerts = [json.loads(x) for x in alerts_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {
        "run_id": run_id,
        "as_of": as_of,
        "loads": loads,
        "gates": gates,
        "published": published,
        "fired": fired,
        "file_rules": file_rules,
        "rule_titles": rule_titles,
        "freshness": freshness,
        "schema_changes": schema_changes,
        "issues": issues,
        "measures": measures,
        "dbt": dbt,
        "held": held,
        "reconciliation": recon,
        "deid": deid,
        "alerts": alerts,
    }


def attention(data: dict) -> tuple[list[str], list[str]]:
    """(needs action, watch) - the digest's two lists, in plain sentences."""
    action, watch = [], []
    for feed, gate in sorted(data["gates"].items()):
        if gate["decision"] == "HOLD":
            action.append(f"**{feed}**: publication held - {mask(gate['reasons'])}")
    for f in data["freshness"]:
        if f["delivery_status"] == "LATE":
            action.append(f"**{f['feed']}** ({f['source']}): delivery late - {f['detail']}")
        elif f["delivery_status"] == "DUE":
            watch.append(f"{f['feed']} ({f['source']}): delivery expected today, not yet arrived")
        if f["pipeline_status"] == "STUCK" and not any(f["feed"] in a and "publication held" in a for a in action):
            action.append(f"**{f['feed']}** ({f['source']}): delivered but not published - {f['detail']}")
    if data["deid"] and not data["deid"]["passed"]:
        action.append(
            "**de-identified copy** held: " + "; ".join(c["check"] for c in data["deid"]["checks"] if not c["passed"])
        )
    if any(d["status"] not in ("success", "pass") for d in data["dbt"]):
        action.append("**dbt**: build failed - measures not refreshed")
    for c in data["schema_changes"]:
        watch.append(
            f"{c['feed']}: layout v{c['from_version']} -> v{c['to_version']} recognised "
            f"({c['change_record'] or 'no change record'}); clean table archived before loading"
        )
    for r in data["file_rules"]:
        if r["outcome"] in ("warn", "overridden"):
            watch.append(f"{r['feed']}: {r['name']} - {r['rule_id']}: {r['detail']}")
    late = {}
    for f in data["fired"]:
        rule = data["rule_titles"].get((f["feed"], f["rule_id"]))
        if rule and rule.type == "closed_period" and f["rows_new"]:
            late[f["feed"]] = late.get(f["feed"], 0) + f["rows_new"]
    for feed, n in sorted(late.items()):
        watch.append(f"{feed}: {n} late row(s) for the closed measurement year; its results are restated")
    return action, watch


# --------------------------------------------------------------------------------------------- summary


def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return out


def _pct(x) -> str:
    return "" if x is None else f"{float(x) * 100:.1f}%"


def summary_markdown(data: dict, settings: Settings) -> str:
    action, watch = attention(data)
    loads = data["loads"]
    new_loads = [x for x in loads if x["run_id"] == data["run_id"]]
    stopped = [x for x in new_loads if x["status"] in ("blocked", "superseded") and x["blocked_rule"]]
    measured = bool(data["measures"])
    head = [f"# Run {data['run_id']} - as of {data['as_of'].isoformat()}", ""]
    verdict = f"**{len(action)} item(s) need action**" if action else "**All clear**"
    facts = [
        f"{len(settings.feeds_order)} feeds checked",
        f"{len(new_loads) - len(stopped)} file(s) loaded",
        f"{len(stopped)} stopped" if stopped else None,
        "measures refreshed" if measured else "measures unchanged (nothing new was published)",
        ("de-identified copy released" if data["deid"]["passed"] else "de-identified copy held")
        if data["deid"]
        else None,
    ]
    head.append(" - ".join([verdict] + [f for f in facts if f]))
    lines = head + [""]
    lines += ["## Needs action", ""] + ([f"- {a}" for a in action] or ["Nothing."]) + [""]
    if watch:
        lines += ["## Watch", ""] + [f"- {w}" for w in watch] + [""]

    lines += ["## Feeds", ""]
    rows = []
    for feed in settings.feeds_order:
        mine = [x for x in new_loads if x["feed"] == feed]
        gate = data["gates"].get(feed)
        changed = [p["table_name"] for p in data["published"] if p["feed"] == feed and p["changed"]]
        published = (
            "not run"
            if feed not in data["gates"]
            else ("held" if gate["decision"] == "HOLD" else (", ".join(changed) if changed else "unchanged"))
        )
        rows.append(
            [
                feed,
                ", ".join(f"{x['name']} ({x['status']})" for x in mine) or "nothing new",
                sum(x["rows_raw"] or 0 for x in mine),
                sum(x["rows_quarantined"] or 0 for x in mine),
                sum(x["rows_repaired"] or 0 for x in mine),
                sum(x["rows_warned"] or 0 for x in mine),
                gate["decision"] if gate else "-",
                published,
            ]
        )
    lines += _table(["feed", "files", "rows in", "held", "repaired", "warned", "gate", "published"], rows) + [""]

    if data["freshness"]:
        lines += ["## Freshness", ""]
        lines += _table(
            ["feed", "source", "delivery", "pipeline", "newest delivered", "newest published", "days behind", "detail"],
            [
                [
                    f["feed"],
                    f["source"],
                    f["delivery_status"],
                    f["pipeline_status"],
                    f["latest_delivered_event"],
                    f["latest_published_event"],
                    f["days_behind"],
                    f["detail"] or "",
                ]
                for f in data["freshness"]
            ],
        ) + [""]

    if data["fired"] or data["file_rules"]:
        lines += ["## Rules that fired", ""]
        rows = []
        for f in data["fired"]:
            rule = data["rule_titles"].get((f["feed"], f["rule_id"]))
            rows.append(
                [
                    f["feed"],
                    f["rule_id"],
                    f["action"],
                    f["rows_new"],
                    f["rows_rechecked"],
                    rule.title if rule else "row does not match the header",
                ]
            )
        for r in data["file_rules"]:
            rule = data["rule_titles"].get((r["feed"], r["rule_id"]))
            rows.append(
                [
                    r["feed"],
                    r["rule_id"],
                    r["outcome"],
                    "file",
                    "",
                    (rule.title if rule else "") + (f" - {r['detail']}" if r["detail"] else ""),
                ]
            )
        lines += _table(["feed", "rule", "action", "rows (new)", "rows re-checked", "what it checks"], rows) + [""]

    issues = data["issues"]
    opened = [i for i in issues if i["first_seen_run"] == data["run_id"]]
    resolved = [i for i in issues if i["resolved_run"] == data["run_id"]]
    open_now = [i for i in issues if i["status"] == "open"]
    lines += [
        "## Issue register",
        "",
        f"{len(opened)} opened, {len(resolved)} resolved this run; {len(open_now)} open.",
        "",
    ]
    if open_now:
        lines += _table(
            ["issue", "since", "age (days)", "count", "detail"],
            [
                [
                    i["issue_id"],
                    i["first_seen"],
                    (data["as_of"] - i["first_seen"]).days,
                    i["current_count"],
                    mask(i["detail"]),
                ]
                for i in open_now
            ],
        ) + [""]
    if resolved:
        lines += ["Resolved:", ""] + [f"- {i['issue_id']} - {i['resolution']}" for i in resolved] + [""]

    if measured:
        lines += ["## Measures (network)", ""]
        lines += _table(
            ["measure", "rate", "numerator / denominator", "previous run"],
            [
                [
                    m["measure_id"],
                    _pct(m["rate"]),
                    f"{m['numerator']} / {m['denominator']}",
                    (
                        _pct(m["previous_rate"])
                        + (
                            " (restated)"
                            if m["previous_rate"] is not None
                            and (m["numerator"], m["denominator"])
                            != (m["previous_numerator"], m["previous_denominator"])
                            else ""
                        )
                    )
                    if m["previous_rate"] is not None
                    else "-",
                ]
                for m in data["measures"]
            ],
        ) + [""]

    if data["deid"]:
        d = data["deid"]
        lines += [
            "## De-identification",
            "",
            f"Leak gate: {'passed' if d['passed'] else 'FAILED'} "
            f"({sum(c['passed'] for c in d['checks'])} of {len(d['checks'])} checks).",
            "",
        ]
        lines += _table(
            ["check", "result", "detail"],
            [[c["check"], "pass" if c["passed"] else "FAIL", c["detail"]] for c in d["checks"]],
        ) + [""]
        threshold = d["group_sizes"][0]["threshold"] if d["group_sizes"] else 11
        lines += ["Quasi-identifier group sizes in the extract (reported, not a pass/fail):", ""]
        lines += _table(
            ["quasi-identifiers", "groups", "smallest", f"records in groups below {threshold}"],
            [
                [
                    ", ".join(g["quasi_identifiers"]),
                    g["groups"],
                    g["smallest_group"],
                    f"{g['records_in_groups_below']} ({g['share_below']:.0%})",
                ]
                for g in d["group_sizes"]
            ],
        ) + [""]

    if data["alerts"]:
        lines += ["## Messages sent", ""]
        lines += [f"- `{a['channel']}` {a['title']}" for a in data["alerts"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


def manifest(data: dict, settings: Settings, wh: Warehouse) -> dict:
    files = wh.rows(
        "select name, feed, sha256, status from audit.files where registered_run = ? order by name", [data["run_id"]]
    )
    return {
        "run_id": data["run_id"],
        "as_of": data["as_of"].isoformat(),
        "pophealth": __version__,
        "config_hash": _tree_hash(settings.config_dir),
        "dbt_project_hash": _tree_hash(REPO / "dbt" / "models"),
        "files_registered": files,
        "published": [
            {
                "feed": p["feed"],
                "table": p["table_name"],
                "rows": p["rows"],
                "content_hash": p["content_hash"],
                "changed": p["changed"],
            }
            for p in data["published"]
        ],
        "gates": {f: g["decision"] for f, g in sorted(data["gates"].items())},
        "deidentification_passed": data["deid"]["passed"] if data["deid"] else None,
    }


def write_evidence(settings: Settings, run_id: str, as_of: date, evidence_dir: Path) -> dict:
    with Warehouse(settings.warehouse_path) as wh:
        data = gather(wh, settings, run_id, as_of, evidence_dir)
        man = manifest(data, settings, wh)
        scorecard = wh.rows(
            """select feed, load_id, rule_id, rule_type, action, dimension, rows_evaluated,
                                      rows_failed, outcome, detail
                               from audit.rule_results where run_id = ? order by feed, load_id, rule_id""",
            [run_id],
        )
    write_csv(evidence_dir / "dq_scorecard.csv", scorecard)
    write_csv(
        evidence_dir / "reconciliation.csv",
        data["reconciliation"],
        [
            "feed",
            "name",
            "rows_raw",
            "rows_malformed",
            "rows_staged",
            "rows_passed",
            "rows_quarantined",
            "rows_inserted",
            "rows_updated",
            "rows_unchanged",
            "rows_deduped",
            "rows_removed",
            "raw = staged + malformed",
            "staged = passed + quarantined",
            "passed = inserted + updated + unchanged + deduped",
        ],
    )
    write_csv(
        evidence_dir / "quarantine.csv",
        data["held"],
        ["feed", "file", "row", "rules", "status", "first_held", "released", "detail"],
    )
    write_csv(
        evidence_dir / "issues.csv",
        data["issues"],
        [
            "issue_id",
            "feed",
            "rule_id",
            "scope",
            "title",
            "status",
            "first_seen",
            "last_seen",
            "current_count",
            "peak_count",
            "resolution",
            "resolved_on",
            "detail",
        ],
    )
    (evidence_dir / "manifest.json").write_text(
        json.dumps(man, indent=1, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    text = summary_markdown(data, settings)
    (evidence_dir / "summary.md").write_text(text, encoding="utf-8", newline="\n")
    action, watch = attention(data)
    return {"action": action, "watch": watch, "summary": text}
