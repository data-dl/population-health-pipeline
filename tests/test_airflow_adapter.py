"""The Airflow adapter's entry points (run_task, choose_branch, poke), exercised without Airflow.

Each task of a feed DAG is called the way an Airflow operator calls it - by DAG and task id, with an
Airflow-style context and a separate run id per DAG run - and what it returns must be plain JSON, as an
XCom is. The DAG file itself is parsed under real Airflow in tests/test_airflow.py.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

from pophealth.orchestration.airflow_adapter import as_of_date, choose_branch, poke, run_task
from pophealth.orchestration.dag import Outcome, Runner
from pophealth.orchestration.dags import feed_dag

from .conftest import ROSTER_HEADER, query, roster_row, write_file
from .test_feeds_unit import LAB_V2, _lab

KINDS: dict[tuple[str, str], str] = {}
CONTEXT: dict = {}


def through_airflow(ctx) -> Outcome:
    """Stands in for an Airflow operator: calls the adapter by ids, as the operator would."""
    kind = KINDS[(ctx.dag_id, ctx.task_id)]
    if kind == "sensor":
        return Outcome(satisfied=poke(ctx.dag_id, ctx.task_id, **CONTEXT))
    if kind == "branch":
        return Outcome(follow=choose_branch(ctx.dag_id, ctx.task_id, **CONTEXT))
    xcom = run_task(ctx.dag_id, ctx.task_id, **CONTEXT)
    assert json.loads(json.dumps(xcom)) == xcom  # what Airflow stores must be plain JSON
    return Outcome(metrics=xcom or {})


def run_on_airflow(feed: str, day: date, run_id: str):
    spec = feed_dag(feed)
    for task in spec.tasks.values():
        KINDS[(spec.dag_id, task.task_id)] = task.kind
        if task.callable:
            task.callable = f"{__name__}:through_airflow"
    CONTEXT.clear()
    CONTEXT.update(
        dag_run=SimpleNamespace(conf={}),
        data_interval_end=datetime(day.year, day.month, day.day, 7),
        run_id=run_id,
    )
    return Runner(lambda dag, task: SimpleNamespace(dag_id=dag.dag_id, task_id=task.task_id)).run(spec)


def test_as_of_is_the_end_of_the_data_interval_not_ds():
    weekly = {  # Airflow 2 runs the week of 9-16 February on the 16th, with ds on the 9th
        "ds": "2026-02-09",
        "data_interval_start": datetime(2026, 2, 9, 7),
        "data_interval_end": datetime(2026, 2, 16, 7),
        "dag_run": SimpleNamespace(conf={}),
    }
    assert as_of_date(weekly) == date(2026, 2, 16)
    assert as_of_date({**weekly, "dag_run": SimpleNamespace(conf={"as_of": "2026-03-02"})}) == date(2026, 3, 2)
    assert as_of_date({"dag_run": SimpleNamespace(conf={"as_of": ""}), "logical_date": None}) == date.today()


def test_feed_dags_run_through_the_airflow_entry_points(tmp_path, monkeypatch):
    inbox = tmp_path / "drop"
    monkeypatch.setenv("POPHEALTH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("POPHEALTH_INBOX", str(inbox))
    first, second = date(2026, 2, 16), date(2026, 2, 23)
    write_file(inbox / "2026-02-16", "roster_20260216.csv", ROSTER_HEADER, [roster_row("00000101")])
    rows = [
        _lab("A1", "00000101", "2026-02-02", "4548-4", "6.5", "%"),
        _lab("A2", "00000102", "2026-02-03", "4548-4", "7.5", "%"),
    ]
    write_file(inbox / "2026-02-16", "labs_20260216.csv", LAB_V2, rows)

    assert run_on_airflow("roster", first, "scheduled__2026-02-16T07:00:00").status == "success"
    labs = run_on_airflow("labs", first, "scheduled__2026-02-16T07:00:00+00:00")
    assert labs.status == "success" and labs.tasks["publish"].status == "success"
    settings_like = SimpleNamespace(warehouse_path=tmp_path / "workspace" / "warehouse.duckdb")
    assert [r["lab_result_id"] for r in query(settings_like, "select * from published.lab_results")] == ["A1"]
    run_ids = {r["run_id"] for r in query(settings_like, "select distinct run_id from audit.gate_decisions")}
    assert run_ids == {"2026-02-16_scheduled__2026-02-16T07_00_00", "2026-02-16_scheduled__2026-02-16T07_00_00_00_00"}

    # A week later the roster registers patient 102 in its own DAG run. The labs DAG, a separate run
    # with no new file, still re-checks the held row against the new roster and publishes it.
    write_file(
        inbox / "2026-02-23", "roster_20260223.csv", ROSTER_HEADER, [roster_row(i) for i in ("00000101", "00000102")]
    )
    assert run_on_airflow("roster", second, "scheduled__2026-02-23").status == "success"
    labs = run_on_airflow("labs", second, "scheduled__2026-02-23b")
    assert labs.tasks["wait_for_delivery"].status == "success"
    published = [r["lab_result_id"] for r in query(settings_like, "select * from published.lab_results order by 1")]
    assert published == ["A1", "A2"]

    # And a third run with nothing new stands down at the sensor.
    assert run_on_airflow("labs", second, "manual__again").tasks["wait_for_delivery"].status == "skipped"
