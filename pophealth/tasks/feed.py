"""Tasks of the per-feed DAG."""

from __future__ import annotations

from .. import ingest
from ..context import TaskContext
from ..orchestration.dag import Outcome
from ..reference import load_reference
from ..warehouse import ensure_schemas


def prepare_warehouse(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        ensure_schemas(wh)
        digest = load_reference(wh, ctx.settings)
    return Outcome(f"reference data {digest}", {"reference_hash": digest})


WORK = {
    "new": "{} new file(s) in the drop",
    "landed": "{} file(s) to load",
    "unfinished": "{} load(s) an earlier run left part-way",
    "publishable": "{} merged load(s) free to publish",
    "recheck": "{} held row(s) to re-check: the roster or reference data changed",
}


def _describe(work: dict) -> str:
    return "; ".join(text.format(work[k]) for k, text in WORK.items() if work.get(k))


def wait_for_delivery(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        work = ingest.outstanding(wh, ctx.settings, ctx.contract, ctx.as_of)
    return Outcome(_describe(work) or "nothing new", work, satisfied=any(work.values()))


def capture_deliveries(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        new = ingest.new_deliveries(wh, ctx.settings, ctx.contract, ctx.as_of)
    listed = [
        {"file": d.name, "source": d.source, "extract": d.extract.isoformat(), "arrived": d.arrival.isoformat()}
        for d in new
    ]
    return Outcome(f"{len(new)} new file(s)", {"new": listed})


def land_to_lake(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.land(wh, ctx.settings, ctx.contract, ctx.as_of, ctx.run_id)
    for name in result["duplicates"]:
        ctx.notifier.status(f"{ctx.feed}: {name} is a byte-for-byte resend; not loaded again", feed=ctx.feed)
    return Outcome(f"{len(result['landed'])} landed, {len(result['duplicates'])} duplicate", result)


def branch_load(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        work = ingest.outstanding(wh, ctx.settings, ctx.contract, ctx.as_of, new_files=False)
    if any(work.values()):
        return Outcome(_describe(work), work, follow=["load_raw"])
    return Outcome("nothing to load (resent files only)", work, follow=["skip_load"])


def load_raw(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.load_raw(wh, ctx.settings, ctx.contract, ctx.spec, ctx.run_id)
    for b in result["blocked"]:
        _write_shape_diff(ctx, b)
        ctx.notifier.alert(
            f"{ctx.feed}: {b['file']} has an unregistered layout - not loaded",
            [
                b["reason"],
                "Register the layout in the contract (with a change record) or return the file to the supplier.",
            ],
            feed=ctx.feed,
        )
    for change in result["schema_changes"]:
        ctx.notifier.status(
            f"{ctx.feed}: layout v{change['from']} -> v{change['to']} "
            f"({change['change_record'] or 'no change record'})",
            [
                f"first file: {change['file']}",
                f"clean table archived as archive.{change['archived']}" if change["archived"] else "nothing to archive",
            ],
            feed=ctx.feed,
        )
    return Outcome(f"{len(result['loaded'])} loaded, {len(result['blocked'])} blocked", result)


def _write_shape_diff(ctx: TaskContext, blocked: dict) -> None:
    lines = [
        f"# Unregistered layout: {blocked['file']}",
        "",
        f"Closest registered layout: v{blocked['closest']}",
        "",
        "| | columns |",
        "|---|---|",
        f"| missing | {', '.join(blocked['missing']) or '-'} |",
        f"| unexpected | {', '.join(blocked['unexpected']) or '-'} |",
        "",
        "Received header:",
        "",
        "```",
        ",".join(blocked["header"]),
        "```",
        "",
        "Next step: agree the change with the supplier, write a change record, add the layout to the "
        "contract, and re-run; the file is still in the lake.",
        "",
    ]
    path = ctx.evidence_dir / f"shape_diff_{blocked['file']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def validate_files(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.validate_files(wh, ctx.settings, ctx.contract, ctx.run_id)
        for f in result["files"]:
            if f["blocked_by"]:
                load = wh.rows("select name, status_reason from audit.loads where load_id = ?", [f["load_id"]])[0]
                rule = ctx.contract.rule(f["blocked_by"])
                ctx.notifier.alert(
                    f"{ctx.feed}: {load['name']} stopped by {rule.id} ({rule.title.lower()})",
                    [load["status_reason"], rule.message] if rule.message else [load["status_reason"]],
                    feed=ctx.feed,
                )
    return Outcome(f"{len(result['files'])} file(s) checked", result)


def stage(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.stage(wh, ctx.settings, ctx.contract, ctx.spec, ctx.run_id)
    return Outcome(f"{sum(result['staged'].values())} row(s) staged", result)


def recheck_quarantine(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.recheck_quarantine(wh, ctx.contract, ctx.spec, ctx.run_id)
    return Outcome(f"{result['candidates']} held row(s) re-checked", result)


def validate_rows(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.validate_rows(wh, ctx.settings, ctx.contract, ctx.spec, ctx.run_id)
        by_rule = {
            r["rule_id"]: r["n"]
            for r in wh.rows(
                "select rule_id, count(*) as n from audit.row_findings where run_id = ? and feed = ? "
                "group by 1 order by 1",
                [ctx.run_id, ctx.feed],
            )
        }
    result["findings_by_rule"] = by_rule
    return Outcome(f"{result.get('held', 0)} held, {result.get('released', 0)} released", result)


def merge_clean(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.merge_clean(wh, ctx.settings, ctx.contract, ctx.spec, ctx.run_id)
    return Outcome(", ".join(f"{k} {v}" for k, v in result.items()), result)


def curate(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.curate(wh, ctx.settings, ctx.contract, ctx.spec, ctx.run_id)
    return Outcome(", ".join(f"{k} {v}" for k, v in result.items()), result)


def qa_gate(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.qa_gate(wh, ctx.settings, ctx.contract, ctx.run_id)
    return Outcome(result["decision"], result)


def branch_publish(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        decision = wh.scalar(
            "select decision from audit.gate_decisions where run_id = ? and feed = ? order by decided_at desc limit 1",
            [ctx.run_id, ctx.feed],
        )
    follow = "publish" if decision == "PUBLISH" else "hold_publication"
    return Outcome(f"gate says {decision}", {"decision": decision}, follow=[follow])


def publish(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        result = ingest.publish(wh, ctx.settings, ctx.contract, ctx.run_id)
    changed = [t for t, c in result["changed"].items() if c]
    return Outcome(f"published; changed: {', '.join(changed) or 'nothing'}", result)


def hold_publication(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        reasons = (
            wh.scalar(
                "select reasons from audit.gate_decisions where run_id = ? and feed = ? "
                "order by decided_at desc limit 1",
                [ctx.run_id, ctx.feed],
            )
            or ""
        )
    lines = reasons.split(" | ") + ["The previous publication stays in place; downstream reads it unchanged."]
    ctx.notifier.alert(f"{ctx.feed}: publication held", lines, feed=ctx.feed)
    return Outcome("held", {"reasons": reasons.split(" | ")})


def branch_trigger(ctx: TaskContext) -> Outcome:
    with ctx.warehouse() as wh:
        changed = bool(
            wh.scalar(
                "select coalesce(bool_or(changed), false) from audit.publish_log where run_id = ? and feed = ?",
                [ctx.run_id, ctx.feed],
            )
        )
    return Outcome(
        "published content changed" if changed else "published content unchanged",
        {"changed": changed},
        follow=["trigger_measures" if changed else "skip_trigger"],
    )
