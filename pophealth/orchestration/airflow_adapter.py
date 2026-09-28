"""Run the pipeline's DAGs on Apache Airflow (2.x or 3.x) without writing them twice.

`to_airflow(spec)` turns a `Dag` from dags.py into an Airflow DAG: empty tasks become EmptyOperator,
branches BranchPythonOperator, sensors PythonSensor (rescheduling, soft-failing), triggers
TriggerDagRunOperator, everything else PythonOperator. Every operator calls the same task callable the
local runner calls, with a TaskContext built the same way, so the tasks behave as they do locally
(tests/test_airflow_adapter.py drives a feed DAG through these entry points without Airflow). Airflow is
imported only here, and only when a DAG file is parsed.

What a deployment needs, described in airflow/README.md: POPHEALTH_WORKSPACE (where the lake, warehouse
and evidence live), optionally POPHEALTH_INBOX (the supplier drop, if not the configured one), and a
one-slot pool, `pophealth_warehouse`, because the warehouse is a single DuckDB file with one writer at a
time.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .dag import Dag

POOL = "pophealth_warehouse"


def as_of_date(airflow_context: dict) -> date:
    """The day a run is "as of": `as_of` from the run's conf if given, else the end of its data interval.

    Not `ds`: Airflow 2 runs a scheduled DAG at the end of its interval with `ds` set to the interval's
    start, a week early for a weekly DAG. The interval's end is the run's day on 2.x and 3.x alike; a
    manual run on Airflow 3 may have neither, and then it is today.
    """
    run = airflow_context.get("dag_run")
    conf = (getattr(run, "conf", None) or {}) if run is not None else {}
    if conf.get("as_of"):
        return date.fromisoformat(str(conf["as_of"]))
    for key in ("data_interval_end", "logical_date"):
        value = airflow_context.get(key)
        if value is not None:
            return value.date()
    return date.today()


def _airflow():
    try:
        from airflow.sdk import DAG
    except ImportError:  # Airflow 2
        from airflow import DAG
    try:
        from airflow.providers.standard.operators.empty import EmptyOperator
        from airflow.providers.standard.operators.python import BranchPythonOperator, PythonOperator
        from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
        from airflow.providers.standard.sensors.python import PythonSensor
    except ImportError:  # Airflow 2 without the standard provider
        from airflow.operators.empty import EmptyOperator
        from airflow.operators.python import BranchPythonOperator, PythonOperator
        from airflow.operators.trigger_dagrun import TriggerDagRunOperator
        from airflow.sensors.python import PythonSensor
    return DAG, EmptyOperator, BranchPythonOperator, PythonOperator, TriggerDagRunOperator, PythonSensor


def _context(dag_id: str, task_id: str, airflow_context: dict):
    from ..context import TaskContext
    from ..notify import Notifier
    from ..settings import load_settings
    from .dags import all_dags

    workspace, inbox = os.environ.get("POPHEALTH_WORKSPACE"), os.environ.get("POPHEALTH_INBOX")
    settings = load_settings(workspace=Path(workspace) if workspace else None, inbox=Path(inbox) if inbox else None)
    as_of = as_of_date(airflow_context)
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{as_of.isoformat()}_{airflow_context.get('run_id', 'airflow')}")
    evidence = settings.runs / run_id
    evidence.mkdir(parents=True, exist_ok=True)
    spec = next(d for d in all_dags(settings.feeds_order) if d.dag_id == dag_id)
    task = spec.tasks[task_id]
    ctx = TaskContext(
        settings,
        run_id,
        as_of,
        dag_id,
        task_id,
        Notifier(settings.config, run_id, evidence),
        evidence,
        dict(spec.params),
    )
    return ctx, task


def run_task(dag_id: str, task_id: str, **airflow_context):
    ctx, task = _context(dag_id, task_id, airflow_context)
    outcome = task.resolve()(ctx)
    # The return value becomes the task's XCom, so it is plain JSON whatever the metrics hold.
    return json.loads(json.dumps(outcome.metrics, default=str)) if outcome else None


def choose_branch(dag_id: str, task_id: str, **airflow_context) -> list[str]:
    ctx, task = _context(dag_id, task_id, airflow_context)
    return list(task.resolve()(ctx).follow or [])


def poke(dag_id: str, task_id: str, **airflow_context) -> bool:
    ctx, task = _context(dag_id, task_id, airflow_context)
    return bool(task.resolve()(ctx).satisfied)


def to_airflow(spec: Dag):
    DAG, Empty, Branch, Python, Trigger, Sensor = _airflow()
    dag = DAG(
        dag_id=spec.dag_id,
        description=spec.description,
        schedule=spec.schedule,
        start_date=datetime(2026, 1, 1),
        catchup=False,
        max_active_runs=1,
        tags=["pophealth", *spec.tags],
        default_args={"owner": "data-ops", "retries": 0},
    )
    operators = {}
    for task_id in spec.order():
        t = spec.tasks[task_id]
        common = {"task_id": task_id, "trigger_rule": t.trigger_rule, "dag": dag}
        if t.retries:
            common.update(retries=t.retries, retry_delay=timedelta(seconds=max(t.retry_delay_s, 1)))
        if t.doc:
            common["doc_md"] = t.doc
        kwargs = {"op_kwargs": {"dag_id": spec.dag_id, "task_id": task_id}, "pool": POOL}
        if t.kind == "empty":
            op = Empty(**common)
        elif t.kind == "trigger":
            # An explicit as_of (a replayed day) carries over; otherwise the triggered run uses its own day.
            conf = {"as_of": "{{ (dag_run.conf or {}).get('as_of', '') }}"}
            op = Trigger(trigger_dag_id=t.trigger_dag_id, conf=conf, **common)
        elif t.kind == "sensor":
            op = Sensor(
                python_callable=poke,
                poke_interval=t.poke_interval_s,
                timeout=max(t.timeout_s, 6 * 3600),
                mode="reschedule",
                soft_fail=t.soft_fail,
                **kwargs,
                **common,
            )
        elif t.kind == "branch":
            op = Branch(python_callable=choose_branch, **kwargs, **common)
        else:
            op = Python(python_callable=run_task, **kwargs, **common)
        operators[task_id] = op
    for task_id, t in spec.tasks.items():
        for upstream in t.upstream:
            operators[upstream] >> operators[task_id]
    return dag
