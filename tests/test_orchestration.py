"""The runner follows Airflow's semantics, and every task in every DAG resolves to a callable."""

from __future__ import annotations

import pytest

from pophealth.orchestration.dag import NONE_FAILED_MIN_ONE_SUCCESS, Dag, DagBuilder, Outcome, Runner, Task
from pophealth.orchestration.dags import all_dags
from pophealth.settings import load_settings

CALLS: list[str] = []
ATTEMPTS = {"n": 0}


def ok(ctx):
    CALLS.append(ctx)
    return Outcome("ok")


def boom(ctx):
    CALLS.append(ctx)
    raise ValueError("broken [id 123]")


def flaky(ctx):
    ATTEMPTS["n"] += 1
    if ATTEMPTS["n"] < 3:
        raise ConnectionError("try again")
    return Outcome("third time")


def pick_b(ctx):
    return Outcome(follow=["b"])


def never(ctx):
    return Outcome(satisfied=False, message="nothing yet")


def _runner():
    CALLS.clear()
    return Runner(lambda dag, task: task.task_id, sleep=lambda s: None)


def test_branch_skips_the_other_path_and_the_join_still_runs():
    m = __name__
    dag = (
        DagBuilder("t", "branch")
        .add("start", kind="empty")
        .add("choose", after="start", kind="branch", callable=f"{m}:pick_b")
        .add("a", after="choose", callable=f"{m}:ok")
        .add("a2", after="a", callable=f"{m}:ok")
        .add("b", after="choose", callable=f"{m}:ok")
        .add("end", after=["a2", "b"], kind="empty", trigger_rule=NONE_FAILED_MIN_ONE_SUCCESS)
        .build()
    )
    run = _runner().run(dag)
    assert {k: v.status for k, v in run.tasks.items()} == {
        "start": "success",
        "choose": "success",
        "a": "skipped",
        "a2": "skipped",
        "b": "success",
        "end": "success",
    }
    assert CALLS == ["b"]


def test_failure_propagates_and_is_recorded():
    m = __name__
    dag = DagBuilder("t", "fail").add("x", callable=f"{m}:boom").add("y", after="x", callable=f"{m}:ok").build()
    run = _runner().run(dag)
    assert run.tasks["x"].status == "failed" and "broken" in run.tasks["x"].message
    assert run.tasks["y"].status == "upstream_failed" and run.status == "failed"


def test_retries_until_success():
    ATTEMPTS["n"] = 0
    dag = DagBuilder("t", "retry").add("x", callable=f"{__name__}:flaky", retries=3).build()
    run = _runner().run(dag)
    assert run.tasks["x"].status == "success" and run.tasks["x"].attempts == 3


def test_sensor_soft_fails_into_a_skip():
    m = __name__
    dag = (
        DagBuilder("t", "sensor")
        .add("wait", kind="sensor", callable=f"{m}:never")
        .add("after", after="wait", callable=f"{m}:ok")
        .build()
    )
    run = _runner().run(dag)
    assert run.tasks["wait"].status == "skipped" and run.tasks["after"].status == "skipped"
    assert run.status == "success"


def test_trigger_tasks_request_their_dag():
    dag = DagBuilder("t", "trigger").add("go", kind="trigger", trigger_dag_id="other").build()
    assert _runner().run(dag).triggers == ["other"]


def test_cycles_and_unknown_upstreams_are_rejected():
    with pytest.raises(ValueError, match="unknown upstream"):
        DagBuilder("t", "bad").add("x", after="missing")
    cyclic = Dag("t", "cycle", {"a": Task("a", upstream=["b"]), "b": Task("b", upstream=["a"])})
    with pytest.raises(ValueError, match="cycle"):
        cyclic.order()


def test_every_pipeline_task_resolves():
    dags = all_dags(load_settings().feeds_order)
    assert {d.dag_id for d in dags} == {
        "feed_roster",
        "feed_screenings",
        "feed_labs",
        "feed_appointments",
        "feed_pharmacy",
        "measures_and_documents",
        "monitoring",
    }
    for dag in dags:
        dag.order()
        for task in dag.tasks.values():
            if task.callable:
                assert callable(task.resolve()), f"{dag.dag_id}.{task.task_id}"
            if task.kind == "branch":
                assert len(dag.downstream(task.task_id)) >= 2
