"""DAGs as plain data, and a runner with Airflow's semantics.

A `Dag` is a set of `Task`s with dependencies. A task names its callable as "module:function", so
defining (or parsing) a DAG imports nothing heavy - the same definitions drive the local runner here
and the Airflow adapter (`airflow_adapter.py`) without a line of duplication.

Semantics implemented, as Airflow defines them:
- trigger rules: all_success (default), none_failed_min_one_success (joins after a branch), all_done
- a branch task returns the task ids to follow; every other direct downstream task is skipped, and the
  skip propagates through trigger rules
- a sensor pokes until satisfied or timed out; with soft_fail a timeout is a skip, not a failure
- a task may be retried a set number of times with a delay
- a trigger task asks for another DAG to run after this one
"""

from __future__ import annotations

import importlib
import json
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SUCCESS, FAILED, SKIPPED, UPSTREAM_FAILED = "success", "failed", "skipped", "upstream_failed"
ALL_SUCCESS, NONE_FAILED_MIN_ONE_SUCCESS, ALL_DONE = "all_success", "none_failed_min_one_success", "all_done"


@dataclass
class Outcome:
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    follow: list[str] | None = None  # branch tasks: which downstream tasks to follow
    satisfied: bool = True  # sensors
    errors: list[str] = field(default_factory=list)


@dataclass
class Task:
    task_id: str
    kind: str = "task"  # task | sensor | branch | trigger | empty
    callable: str | None = None
    upstream: list[str] = field(default_factory=list)
    trigger_rule: str = ALL_SUCCESS
    retries: int = 0
    retry_delay_s: float = 0.0
    poke_interval_s: float = 300.0
    timeout_s: float = 0.0
    soft_fail: bool = True
    trigger_dag_id: str | None = None
    doc: str = ""

    def resolve(self) -> Callable:
        module, name = self.callable.split(":")
        return getattr(importlib.import_module(module), name)


@dataclass
class Dag:
    dag_id: str
    description: str
    tasks: dict[str, Task]
    schedule: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def downstream(self, task_id: str) -> list[str]:
        return [t.task_id for t in self.tasks.values() if task_id in t.upstream]

    def order(self) -> list[str]:
        """Topological order, stable in definition order."""
        done: list[str] = []
        pending = list(self.tasks)
        while pending:
            ready = [t for t in pending if all(u in done for u in self.tasks[t].upstream)]
            if not ready:
                raise ValueError(f"{self.dag_id}: cycle or unknown upstream among {pending}")
            done.append(ready[0])
            pending.remove(ready[0])
        return done


class DagBuilder:
    def __init__(self, dag_id: str, description: str, schedule: str | None = None, **params: Any):
        self.dag = Dag(dag_id, description, {}, schedule, params)

    def add(self, task_id: str, *, after: list[str] | str | None = None, **kwargs: Any) -> DagBuilder:
        upstream = [after] if isinstance(after, str) else list(after or [])
        for u in upstream:
            if u not in self.dag.tasks:
                raise ValueError(f"{self.dag.dag_id}.{task_id}: unknown upstream {u}")
        self.dag.tasks[task_id] = Task(task_id, upstream=upstream, **kwargs)
        return self

    def build(self, tags: list[str] | None = None) -> Dag:
        self.dag.tags = tags or []
        self.dag.order()
        return self.dag


@dataclass
class TaskRun:
    dag_id: str
    task_id: str
    status: str
    attempts: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        if self.started_at and self.ended_at:
            return round((self.ended_at - self.started_at).total_seconds(), 3)
        return 0.0

    def as_record(self) -> dict:
        return {
            "dag_id": self.dag_id,
            "task_id": self.task_id,
            "status": self.status,
            "attempts": self.attempts,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_s": self.duration_s,
            "message": self.message,
            "errors": self.errors,
            "metrics": json.loads(json.dumps(self.metrics, default=str)),
        }


@dataclass
class DagRun:
    dag_id: str
    tasks: dict[str, TaskRun]
    triggers: list[str]

    @property
    def status(self) -> str:
        statuses = {t.status for t in self.tasks.values()}
        if statuses & {FAILED, UPSTREAM_FAILED}:
            return FAILED
        return SUCCESS

    def outcome(self, task_id: str) -> TaskRun | None:
        return self.tasks.get(task_id)


def _decide(rule: str, upstream: list[str]) -> str | None:
    """None means run; otherwise the state the task takes without running."""
    if not upstream or rule == ALL_DONE:
        return None
    failed = any(s in (FAILED, UPSTREAM_FAILED) for s in upstream)
    if rule == ALL_SUCCESS:
        if failed:
            return UPSTREAM_FAILED
        return SKIPPED if SKIPPED in upstream else None
    if rule == NONE_FAILED_MIN_ONE_SUCCESS:
        if failed:
            return UPSTREAM_FAILED
        return None if SUCCESS in upstream else SKIPPED
    raise ValueError(f"unknown trigger rule {rule}")


class Runner:
    """Runs a Dag in-process. `make_context(dag, task)` builds what each callable receives;
    `record(task_run)` is called once per task (evidence, audit table)."""

    def __init__(
        self,
        make_context: Callable,
        record: Callable[[TaskRun], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.make_context = make_context
        self.record = record or (lambda _: None)
        self.sleep = sleep

    def run(self, dag: Dag) -> DagRun:
        runs: dict[str, TaskRun] = {}
        branch_skipped: set[str] = set()
        triggers: list[str] = []
        for task_id in dag.order():
            task = dag.tasks[task_id]
            if task_id in branch_skipped:
                state = SKIPPED
            else:
                state = _decide(task.trigger_rule, [runs[u].status for u in task.upstream])
            if state is not None:
                runs[task_id] = TaskRun(dag.dag_id, task_id, state, message="not run")
                self.record(runs[task_id])
                continue
            run = self._execute(dag, task)
            runs[task_id] = run
            self.record(run)
            if run.status == SUCCESS and task.kind == "branch":
                follow = set(run.metrics.get("follow") or [])
                branch_skipped |= {d for d in dag.downstream(task_id) if d not in follow}
            if run.status == SUCCESS and task.kind == "trigger" and task.trigger_dag_id:
                triggers.append(task.trigger_dag_id)
        return DagRun(dag.dag_id, runs, triggers)

    def _execute(self, dag: Dag, task: Task) -> TaskRun:
        run = TaskRun(dag.dag_id, task.task_id, SUCCESS, started_at=datetime.now())
        if task.kind == "empty" or task.callable is None:
            run.ended_at = run.started_at
            if task.kind == "trigger":
                run.message = f"triggered {task.trigger_dag_id}"
            return run
        fn = task.resolve()
        deadline = time.monotonic() + task.timeout_s
        while True:
            run.attempts += 1
            try:
                outcome = fn(self.make_context(dag, task)) or Outcome()
            except Exception as exc:  # noqa: BLE001 - every failure is recorded, not raised
                run.errors = [f"{type(exc).__name__}: {exc}", traceback.format_exc(limit=6)]
                if run.attempts <= task.retries:
                    self.sleep(task.retry_delay_s)
                    continue
                run.status, run.message = FAILED, f"{type(exc).__name__}: {exc}"
                break
            if task.kind == "sensor" and not outcome.satisfied:
                if time.monotonic() + task.poke_interval_s <= deadline:
                    self.sleep(task.poke_interval_s)
                    continue
                run.status = SKIPPED if task.soft_fail else FAILED
                run.message = outcome.message or "sensor timed out"
                run.metrics = outcome.metrics
                break
            run.message, run.metrics, run.errors = outcome.message, dict(outcome.metrics), outcome.errors
            if task.kind == "branch":
                run.metrics["follow"] = list(outcome.follow or [])
            break
        run.ended_at = datetime.now()
        return run
