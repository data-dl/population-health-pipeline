"""Command line.

    pophealth run --as-of 2026-03-02            one scheduled run (every feed, then measures, then monitoring)
    pophealth task feed_labs validate_rows --as-of 2026-03-02   one task on its own (troubleshooting)
    pophealth dags                              the task graphs
    pophealth documents --collection clinics    rebuild one document collection
    pophealth freshness --as-of 2026-03-03      the freshness report
    pophealth issues                            the open issue register
    pophealth waive <load_id> --reason "..."    let a feed publish without a blocked file (recorded)
    pophealth accept <load_id> --reason "..."   load a file a rule stopped, overriding it (recorded)
    pophealth docs [--check]                    regenerate (or verify) the generated documentation
    pophealth demo [--docs docs]                synthetic inbox + the whole five-run scenario

Global options --workspace and --inbox point the pipeline at another folder (the default is the
repository, with the synthetic inbox in demo/inbox).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .settings import load_settings


def _date(text: str) -> date:
    return date.fromisoformat(text)


def _print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len("" if r.get(c) is None else str(r.get(c))) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for r in rows:
        print("  ".join(("" if r.get(c) is None else str(r.get(c))).ljust(widths[c]) for c in columns))


def cmd_run(settings, args) -> int:
    from .pipeline import run_pipeline

    result = run_pipeline(settings, args.as_of, args.feeds.split(",") if args.feeds else None)
    for dag_run in result.dag_runs:
        notes = [f"{t.task_id}: {t.message}" for t in dag_run.tasks.values() if t.status == "failed"]
        print(f"{dag_run.dag_id:<24} {dag_run.status:<8} {'; '.join(notes)}")
    print(f"\nrun {result.run_id}: {result.status}. Evidence: {result.evidence_dir}")
    summary = result.evidence_dir / "summary.md"
    if summary.exists() and not args.quiet:
        print("\n" + summary.read_text(encoding="utf-8").split("## Feeds")[0].strip())
    return 0 if result.status == "success" else 1


def cmd_task(settings, args) -> int:
    from .context import TaskContext
    from .notify import Notifier
    from .orchestration.dags import all_dags

    dag = next((d for d in all_dags(settings.feeds_order) if d.dag_id == args.dag_id), None)
    if dag is None or args.task_id not in dag.tasks:
        print(f"unknown task {args.dag_id}.{args.task_id}", file=sys.stderr)
        return 2
    task = dag.tasks[args.task_id]
    if task.callable is None:
        print(f"{args.dag_id}.{args.task_id} is a {task.kind} task; nothing to run")
        return 0
    run_id = args.run_id or f"{args.as_of.isoformat()}_manual_{datetime.now():%H%M%S}"
    evidence = settings.runs / run_id
    evidence.mkdir(parents=True, exist_ok=True)
    ctx = TaskContext(
        settings,
        run_id,
        args.as_of,
        dag.dag_id,
        task.task_id,
        Notifier(settings.config, run_id, evidence),
        evidence,
        dict(dag.params),
    )
    outcome = task.resolve()(ctx)
    print(outcome.message)
    for key, value in outcome.metrics.items():
        print(f"  {key}: {value}")
    if outcome.follow is not None:
        print(f"  follow: {outcome.follow}")
    return 0


def cmd_dags(settings, args) -> int:
    from .orchestration.dags import all_dags

    for dag in all_dags(settings.feeds_order):
        print(f"{dag.dag_id}  ({dag.schedule or 'triggered'})  {dag.description}")
        for task_id in dag.order():
            t = dag.tasks[task_id]
            after = f"  <- {', '.join(t.upstream)}" if t.upstream else ""
            rule = f"  [{t.trigger_rule}]" if t.trigger_rule != "all_success" else ""
            print(f"    {task_id:<22} {t.kind:<8}{after}{rule}")
        print()
    return 0


def cmd_documents(settings, args) -> int:
    from .documents.build import build_documents

    report = build_documents(
        settings,
        args.as_of,
        collection=args.collection,
        sites=args.sites.split(",") if args.sites else None,
        workers=args.workers,
        dry_run=args.dry_run,
    )
    for name, counts in report.items():
        print(f"{name:<12} " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


def cmd_freshness(settings, args) -> int:
    from .monitoring import freshness
    from .warehouse import Warehouse

    with Warehouse(settings.warehouse_path) as wh:
        rows = freshness(wh, settings, f"adhoc_{args.as_of.isoformat()}", args.as_of)
        wh.execute("delete from audit.freshness where run_id = ?", [f"adhoc_{args.as_of.isoformat()}"])
    _print_table(
        rows,
        [
            "feed",
            "source",
            "delivery_status",
            "pipeline_status",
            "latest_delivered_event",
            "latest_published_event",
            "days_behind",
            "detail",
        ],
    )
    return 0


def cmd_issues(settings, args) -> int:
    from .warehouse import Warehouse

    with Warehouse(settings.warehouse_path) as wh:
        where = "" if args.all else "where status = 'open'"
        rows = wh.rows(f"select * from audit.issues {where} order by status desc, first_seen, issue_id")
    _print_table(rows, ["issue_id", "status", "first_seen", "last_seen", "current_count", "detail", "resolution"])
    return 0


def cmd_waive(settings, args) -> int:
    from .warehouse import Warehouse

    with Warehouse(settings.warehouse_path) as wh:
        load = wh.rows("select * from audit.loads where load_id = ?", [args.load_id])
        if not load or load[0]["status"] != "blocked":
            print(f"{args.load_id} is not a blocked load", file=sys.stderr)
            return 2
        wh.execute(
            "insert into audit.decisions values (?, null, 'waive', ?, ?, ?, ?)",
            [f"waive:{args.load_id}", args.load_id, args.reason, args.by, datetime.now().replace(microsecond=0)],
        )
        wh.execute(
            "update audit.loads set status = 'waived', status_reason = status_reason || ' (waived: ' || ? || ')' "
            "where load_id = ?",
            [args.reason, args.load_id],
        )
    print(f"{args.load_id} waived: the feed publishes without it on its next run, unless another file is stopped.")
    return 0


def cmd_accept(settings, args) -> int:
    from .warehouse import Warehouse

    with Warehouse(settings.warehouse_path) as wh:
        load = wh.rows("select * from audit.loads where load_id = ?", [args.load_id])
        if not load or load[0]["status"] != "blocked":
            print(f"{args.load_id} is not a blocked load", file=sys.stderr)
            return 2
        if load[0]["raw_table"] is None:
            print(
                f"{args.load_id} never loaded (its layout is not registered): register it, or waive the file",
                file=sys.stderr,
            )
            return 2
        wh.execute(
            "insert into audit.decisions values (?, null, 'accept', ?, ?, ?, ?)",
            [f"accept:{args.load_id}", args.load_id, args.reason, args.by, datetime.now().replace(microsecond=0)],
        )
        wh.execute(
            "update audit.loads set status = 'raw_loaded', blocked_rule = null, "
            "status_reason = status_reason || ' (accepted: ' || ? || ')' where load_id = ?",
            [args.reason, args.load_id],
        )
        wh.execute(
            "update audit.files set status = 'loaded', status_reason = null where file_id = ?", [load[0]["file_id"]]
        )
    print(
        f"{args.load_id} accepted: it loads on the feed's next run, {load[0]['blocked_rule']} recorded as overridden."
    )
    return 0


def cmd_docs(settings, args) -> int:
    from .docsgen import generate_docs

    stale = generate_docs(settings, check=args.check)
    if args.check and stale:
        print("out of date: " + ", ".join(stale) + " (run: pophealth docs)", file=sys.stderr)
        return 1
    print("docs are current" if args.check else "docs regenerated")
    return 0


def cmd_demo(settings, args) -> int:
    from .demo import run_demo

    return run_demo(Path(args.out), docs=Path(args.docs) if args.docs else None, quiet=args.quiet)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pophealth", description="Population-health data pipeline.")
    parser.add_argument("--workspace", type=Path, help="workspace root (default: the repository)")
    parser.add_argument("--inbox", type=Path, help="supplier drop folder (default: from config/pipeline.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="one scheduled run")
    p.add_argument("--as-of", type=_date, required=True)
    p.add_argument("--feeds", help="comma-separated subset of feeds")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("task", help="run one task on its own")
    p.add_argument("dag_id")
    p.add_argument("task_id")
    p.add_argument("--as-of", type=_date, required=True)
    p.add_argument("--run-id")
    p.set_defaults(fn=cmd_task)

    sub.add_parser("dags", help="list the task graphs").set_defaults(fn=cmd_dags)

    p = sub.add_parser("documents", help="build document collections")
    p.add_argument("--as-of", type=_date, default=date.today())
    p.add_argument("--collection")
    p.add_argument("--sites")
    p.add_argument("--workers", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_documents)

    p = sub.add_parser("freshness", help="the freshness report")
    p.add_argument("--as-of", type=_date, required=True)
    p.set_defaults(fn=cmd_freshness)

    p = sub.add_parser("issues", help="the issue register")
    p.add_argument("--all", action="store_true", help="include resolved issues")
    p.set_defaults(fn=cmd_issues)

    p = sub.add_parser("waive", help="let a feed publish without a blocked file")
    p.add_argument("load_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default="data steward")
    p.set_defaults(fn=cmd_waive)

    p = sub.add_parser("accept", help="load a file a rule stopped, overriding the rule")
    p.add_argument("load_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default="data steward")
    p.set_defaults(fn=cmd_accept)

    p = sub.add_parser("docs", help="regenerate the generated documentation")
    p.add_argument("--check", action="store_true", help="fail if the committed docs are out of date")
    p.set_defaults(fn=cmd_docs)

    p = sub.add_parser("demo", help="synthetic inbox and the full five-run scenario")
    p.add_argument("--out", default="build/demo", help="workspace for the demo (default: build/demo)")
    p.add_argument("--docs", help="also refresh docs/sample_run and docs/index.html in this folder")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_demo)

    args = parser.parse_args(argv)
    settings = load_settings(workspace=args.workspace, inbox=args.inbox)
    return args.fn(settings, args)


if __name__ == "__main__":
    sys.exit(main())
