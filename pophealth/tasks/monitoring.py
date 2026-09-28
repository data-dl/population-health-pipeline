"""Tasks of the monitoring DAG."""

from __future__ import annotations

import json

from .. import monitoring
from ..context import TaskContext
from ..evidence import write_csv, write_evidence
from ..orchestration.dag import Outcome


def freshness_report(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        rows = monitoring.freshness(wh, ctx.settings, ctx.run_id, ctx.as_of)
    write_csv(ctx.evidence_dir / "freshness.csv", rows)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["overall"]] = counts.get(r["overall"], 0) + 1
    return Outcome(", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no feeds yet", {"overall": counts})


def run_monitor(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        summary = monitoring.run_summary(wh, ctx.run_id)
    path = ctx.evidence_dir / "run_summary.json"
    path.write_text(json.dumps(summary, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
    failed = len(summary["failed"])
    return Outcome(
        f"{summary['tasks']} task(s), {failed} failed",
        {"by_status": summary["by_status"], "distinct_errors": summary["distinct_errors"]},
    )


def issue_register(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = monitoring.update_issues(wh, ctx.settings, ctx.run_id, ctx.as_of)
    return Outcome(
        f"{len(result['opened'])} opened, {len(result['resolved'])} resolved, {len(result['open'])} open",
        {"opened": result["opened"], "resolved": result["resolved"], "open": [i["issue_id"] for i in result["open"]]},
    )


def notify(ctx: TaskContext) -> Outcome:
    result = write_evidence(ctx.settings, ctx.run_id, ctx.as_of, ctx.evidence_dir)
    if result["action"]:
        ctx.notifier.alert(
            f"Run {ctx.run_id}: {len(result['action'])} item(s) need action",
            [a.replace("**", "") for a in result["action"]] + [f"(watch) {w}" for w in result["watch"]],
        )
    else:
        ctx.notifier.status(f"Run {ctx.run_id}: all clear", [f"(watch) {w}" for w in result["watch"]])
    return Outcome(
        f"{len(result['action'])} action, {len(result['watch'])} watch",
        {"action": result["action"], "watch": result["watch"]},
    )
