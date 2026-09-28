"""The Airflow DAG file parses and reproduces the definitions exactly (tasks, dependencies, rules).

Skipped when Airflow is not installed, unless POPHEALTH_REQUIRE_AIRFLOW is set (the CI Airflow job).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pophealth.orchestration.dags import all_dags
from pophealth.settings import load_settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def dagbag():
    try:
        import airflow

        # The repository's own airflow/ folder would import as an empty namespace package.
        installed = bool(getattr(airflow, "__version__", None))
    except ImportError:
        installed = False
    if not installed:
        if os.environ.get("POPHEALTH_REQUIRE_AIRFLOW"):
            pytest.fail("Airflow is required (POPHEALTH_REQUIRE_AIRFLOW is set) but is not installed")
        pytest.skip("Airflow not installed")
    try:
        from airflow.models.dagbag import DagBag
    except ImportError:
        from airflow.dag_processing.dagbag import DagBag
    folder = str(REPO / "airflow" / "dags")
    try:
        return DagBag(dag_folder=folder, include_examples=False)
    except TypeError:  # Airflow 3.3 dropped the argument (examples are off through the environment)
        return DagBag(dag_folder=folder)


def test_dag_file_parses_without_errors(dagbag):
    assert not dagbag.import_errors, dagbag.import_errors


def test_airflow_dags_match_the_definitions(dagbag):
    for spec in all_dags(load_settings().feeds_order):
        dag = dagbag.dags[spec.dag_id]
        assert set(dag.task_ids) == set(spec.tasks), spec.dag_id
        for task_id, t in spec.tasks.items():
            op = dag.get_task(task_id)
            assert set(op.upstream_task_ids) == set(t.upstream), (spec.dag_id, task_id)
            assert str(op.trigger_rule).split(".")[-1].lower() == t.trigger_rule, (spec.dag_id, task_id)
