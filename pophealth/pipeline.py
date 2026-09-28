"""One scheduled run, executed locally: every feed DAG, then measures and documents if any feed
published changed data, then monitoring. Each run gets an id and an evidence folder."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .context import TaskContext
from .notify import Notifier
from .orchestration.dag import SUCCESS, DagRun, Runner, TaskRun
from .orchestration.dags import feed_dag, measures_dag, monitoring_dag
from .settings import Settings
from .warehouse import Warehouse, ensure_schemas


@dataclass
class RunResult:
    run_id: str
    as_of: date
    dag_runs: list[DagRun]
    evidence_dir: Path

    @property
    def status(self) -> str:
        return SUCCESS if all(d.status == SUCCESS for d in self.dag_runs) else "failed"

    def dag(self, dag_id: str) -> DagRun | None:
        return next((d for d in self.dag_runs if d.dag_id == dag_id), None)


def next_run_id(wh: Warehouse, as_of: date) -> str:
    n = wh.scalar("select count(*) from audit.runs where as_of = ?", [as_of]) or 0
    return f"{as_of.isoformat()}_{n + 1:02d}"


def run_pipeline(settings: Settings, as_of: date, feeds: list[str] | None = None, downstream: bool = True) -> RunResult:
    """Run the feed DAGs; then, when `downstream`, measures and documents (if triggered) and monitoring."""
    with Warehouse(settings.warehouse_path) as wh:
        ensure_schemas(wh)
        run_id = next_run_id(wh, as_of)
        wh.execute(
            "insert into audit.runs values (?, ?, ?, null, 'running', null)",
            [run_id, as_of, datetime.now().replace(microsecond=0)],
        )
    evidence = settings.runs / run_id
    evidence.mkdir(parents=True, exist_ok=True)
    notifier = Notifier(settings.config, run_id, evidence)

    def make_context(dag, task) -> TaskContext:
        return TaskContext(settings, run_id, as_of, dag.dag_id, task.task_id, notifier, evidence, dict(dag.params))

    pending: list[TaskRun] = []

    def record(task_run: TaskRun) -> None:
        with open(evidence / "task_results.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"run_id": run_id, **task_run.as_record()}) + "\n")
        pending.append(task_run)

    def flush() -> None:
        rows = []
        for tr in pending:
            rec = tr.as_record()
            rows.append(
                [
                    run_id,
                    rec["dag_id"],
                    rec["task_id"],
                    rec["status"],
                    rec["attempts"],
                    tr.started_at,
                    tr.ended_at,
                    rec["duration_s"],
                    rec["message"],
                    json.dumps(rec["errors"]),
                    json.dumps(rec["metrics"]),
                ]
            )
        if rows:
            with Warehouse(settings.warehouse_path) as wh:
                wh.con.executemany("insert into audit.task_results values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        pending.clear()

    runner = Runner(make_context, record)
    dag_runs: list[DagRun] = []
    triggered: list[str] = []

    def run(dag) -> DagRun:
        result = runner.run(dag)
        flush()
        dag_runs.append(result)
        return result

    for feed in feeds or settings.feeds_order:
        triggered += run(feed_dag(feed)).triggers
    if downstream:
        if triggered:
            run(measures_dag())
        run(monitoring_dag())

    status = SUCCESS if all(d.status == SUCCESS for d in dag_runs) else "failed"
    with Warehouse(settings.warehouse_path) as wh:
        wh.execute(
            "update audit.runs set ended_at = ?, status = ? where run_id = ?",
            [datetime.now().replace(microsecond=0), status, run_id],
        )
    return RunResult(run_id, as_of, dag_runs, evidence)
