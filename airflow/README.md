# Running on Airflow

`dags/pophealth_dags.py` registers the same seven DAGs the local runner executes - one per feed,
`measures_and_documents`, and `monitoring` - by translating the definitions in
`pophealth/orchestration/dags.py` (see `pophealth/orchestration/airflow_adapter.py`). Nothing about a
task is written twice: each Airflow operator calls the same Python function the local runner calls.

| pipeline task kind | Airflow operator |
|---|---|
| `task` | `PythonOperator` (its metrics become the XCom, as plain JSON) |
| `branch` | `BranchPythonOperator` |
| `sensor` | `PythonSensor` (`mode="reschedule"`, `soft_fail=True`, pokes every 15 minutes for up to 6 hours) |
| `trigger` | `TriggerDagRunOperator` (carries an explicit `as_of` over) |
| `empty` | `EmptyOperator` |

Trigger rules, retries and retry delays carry over as declared.

## Setting up

```bash
pip install -e .                      # from the repository root, into the Airflow environment
export POPHEALTH_WORKSPACE=/data/pophealth
export POPHEALTH_INBOX=/data/sftp/incoming   # optional: the supplier drop, if not the configured one
airflow pools set pophealth_warehouse 1 "The DuckDB warehouse accepts one writer at a time"
cp airflow/dags/pophealth_dags.py "$AIRFLOW_HOME/dags/"
```

The feed DAGs run weekly on Monday mornings. Each one's sensor waits for work - a new file, a load an
earlier run left part-way, or held rows to re-check because the roster or reference data changed - and
the DAG stands down if none turns up within six hours. A feed that publishes changed data triggers
`measures_and_documents`. `monitoring` runs daily. A feed DAG can run before the roster DAG: rows for
patients the warehouse does not know yet are held, and re-checked once the roster changes, so order is
not a correctness requirement.

## What differs from a local run

- **The day a run is "as of"** is `as_of` from the run's conf if given, else the end of its data
  interval - not `ds`, which on Airflow 2 is the start of the interval, a week early for a weekly DAG.
- **Run ids.** Locally one run id covers every DAG of a scheduled day. On Airflow each DAG run has its
  own (`<as_of>_<airflow run id>`), and so its own evidence folder under `runs/`.
- **Task state** lives in Airflow's database: retries, failures and durations are in the Airflow UI,
  not in `audit.task_results`. The monitoring DAG's freshness report and issue register read the
  warehouse and work the same; its per-run summary covers what that run itself did.

## Tested

- `tests/test_airflow_adapter.py` (every CI run, no Airflow needed) drives feed DAGs through the
  adapter's entry points - by DAG and task id, with Airflow-style contexts and one run id per DAG run -
  and checks that the results are JSON, that `as_of` comes from the data interval, and that a held row
  is released when the roster changes in a separate DAG run.
- `tests/test_airflow.py` (the CI Airflow job) parses the DAG file with Airflow 3.3 and 2.11: no import
  errors, the seven DAGs present, every task, dependency and trigger rule matching the definitions.
