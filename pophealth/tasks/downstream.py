"""Tasks of the measures-and-documents DAG."""

from __future__ import annotations

import json
import shutil

from ..context import TaskContext
from ..deid.engine import build_safe_harbor_extract, deidentify_documents, release_path
from ..deid.verify import verify, write_report
from ..documents.build import build_documents as build
from ..documents.docstore import DocumentStore
from ..measures import run_dbt
from ..orchestration.dag import Outcome
from ..warehouse import Warehouse, lit


def dbt_build(ctx: TaskContext) -> Outcome:
    result = run_dbt(ctx.settings, ctx.run_id, ctx.as_of)
    if not result["ok"]:
        ctx.notifier.alert(
            "dbt build failed - measures and documents not refreshed",
            result["failures"][:10] or [result["log_tail"][-800:]],
        )
        raise RuntimeError(f"dbt build failed: {len(result['failures'])} node(s) - see audit.dbt_results")
    with Warehouse(ctx.settings.warehouse_path) as wh:
        wh.execute(
            """insert into audit.measure_history
                      select ?, ?, measure_id, site_id, numerator, denominator, rate from marts.measure_summary""",
            [ctx.run_id, ctx.as_of],
        )
        for table, name in (("marts.measure_summary", "measures.csv"), ("marts.measure_equity", "equity.csv")):
            path = ctx.evidence_dir / name
            wh.execute(f"copy (select * from {table} order by all) to {lit(path.as_posix())} (header, delimiter ',')")
    return Outcome(f"{result['models']} models, {result['tests']} tests passed", result)


def build_documents(ctx: TaskContext) -> Outcome:
    report = build(ctx.settings, ctx.as_of)
    return Outcome("; ".join(f"{c}: {r['built']} built" for c, r in report.items()), report)


def deidentify(ctx: TaskContext) -> Outcome:
    documents = deidentify_documents(ctx.settings, ctx.run_id)
    extract = build_safe_harbor_extract(ctx.settings, ctx.run_id)
    return Outcome(f"demo copy staged; extract {extract['rows']} rows", {"documents": documents, "extract": extract})


def verify_deidentified(ctx: TaskContext) -> Outcome:
    report = verify(ctx.settings)
    write_report(report, ctx.evidence_dir / "deid_verification.json")
    failed = [c for c in report["checks"] if not c["passed"]]
    message = "all checks passed" if not failed else f"{len(failed)} check(s) failed"
    return Outcome(
        message,
        {"passed": report["passed"], "failed": [c["check"] for c in failed], "group_sizes": report["group_sizes"]},
    )


def branch_demo(ctx: TaskContext) -> Outcome:
    report = json.loads((ctx.evidence_dir / "deid_verification.json").read_text(encoding="utf-8"))
    return Outcome(
        "release" if report["passed"] else "hold", follow=["publish_demo" if report["passed"] else "hold_demo"]
    )


def publish_demo(ctx: TaskContext) -> Outcome:
    settings = ctx.settings
    DocumentStore(settings.docstore, "demo").replace_with(DocumentStore(settings.docstore, "demo_staging"))
    staged = settings.extracts / "staging" / "safe_harbor_patients.csv"
    shutil.copyfile(staged, release_path(settings))
    return Outcome("demo documents and extract released", {"extract": str(release_path(settings))})


def hold_demo(ctx: TaskContext) -> Outcome:
    report = json.loads((ctx.evidence_dir / "deid_verification.json").read_text(encoding="utf-8"))
    failed = [f"{c['check']}: {c['detail']}" for c in report["checks"] if not c["passed"]]
    ctx.notifier.alert("de-identified copy held - leak gate failed", failed)
    return Outcome("held", {"failed": failed})
