"""The pipeline's DAGs. One per feed (identical shape, generated from the contract list), one for
measures and documents (triggered when a feed publishes changed data), one for monitoring.

These definitions are the only place the task graph is written down: the local runner executes them
in-process, `airflow/dags/pophealth_dags.py` turns them into Airflow DAGs, and `pophealth dags` prints
them.
"""

from __future__ import annotations

from .dag import ALL_DONE, NONE_FAILED_MIN_ONE_SUCCESS, Dag, DagBuilder

FEED_TASKS = "pophealth.tasks.feed"
DOWNSTREAM_TASKS = "pophealth.tasks.downstream"
MONITOR_TASKS = "pophealth.tasks.monitoring"
MEASURES_DAG = "measures_and_documents"
MONITORING_DAG = "monitoring"


def feed_dag(feed: str) -> Dag:
    b = DagBuilder(
        f"feed_{feed}",
        f"Ingest the {feed} feed: land, load, validate, merge, curate, gate, publish.",
        schedule="0 7 * * MON",
        feed=feed,
    )
    t = FEED_TASKS
    b.add("start", kind="empty")
    b.add(
        "prepare_warehouse",
        after="start",
        callable=f"{t}:prepare_warehouse",
        retries=2,
        retry_delay_s=1,
        doc="Create schemas if needed and refresh reference data from config/reference.",
    )
    b.add(
        "wait_for_delivery",
        after="prepare_warehouse",
        kind="sensor",
        callable=f"{t}:wait_for_delivery",
        poke_interval_s=900,
        timeout_s=0,
        doc=(
            "Satisfied when there is work: a file not seen before, or held rows to re-check because the "
            "roster or reference data changed (or a load an earlier run left part-way, or one free to publish)."
        ),
    )
    b.add(
        "capture_deliveries",
        after="wait_for_delivery",
        callable=f"{t}:capture_deliveries",
        doc="List files not seen before, with their extract and arrival dates.",
    )
    b.add(
        "land_to_lake",
        after="capture_deliveries",
        callable=f"{t}:land_to_lake",
        retries=2,
        retry_delay_s=1,
        doc="Copy new files into the lake and register them by checksum.",
    )
    b.add(
        "branch_load",
        after="land_to_lake",
        kind="branch",
        callable=f"{t}:branch_load",
        doc="Load when there is a file to load, a load to finish or publish, or held rows to re-check; else skip.",
    )
    b.add("skip_load", after="branch_load", kind="empty")
    b.add(
        "load_raw",
        after="branch_load",
        callable=f"{t}:load_raw",
        doc="Match each file to a registered layout and load it as text.",
    )
    b.add(
        "validate_files",
        after="load_raw",
        callable=f"{t}:validate_files",
        doc="File-level rules: date format in use, delivery volume.",
    )
    b.add("stage", after="validate_files", callable=f"{t}:stage", doc="Type and standardise every row.")
    b.add(
        "recheck_quarantine",
        after="stage",
        callable=f"{t}:recheck_quarantine",
        doc="Bring held, retryable rows back in front of the rules.",
    )
    b.add(
        "validate_rows",
        after="recheck_quarantine",
        callable=f"{t}:validate_rows",
        doc="Repairs, row rules, escalation; quarantine what fails.",
    )
    b.add(
        "merge_clean",
        after="validate_rows",
        callable=f"{t}:merge_clean",
        doc="Upsert passing rows into clean by natural key (history for snapshots).",
    )
    b.add("curate", after="merge_clean", callable=f"{t}:curate", doc="Rebuild the feed's curated tables.")
    b.add(
        "qa_gate",
        after="curate",
        callable=f"{t}:qa_gate",
        doc="Blocked files outstanding? Row counts reconcile? Decide PUBLISH or HOLD.",
    )
    b.add("branch_publish", after="qa_gate", kind="branch", callable=f"{t}:branch_publish")
    b.add(
        "publish",
        after="branch_publish",
        callable=f"{t}:publish",
        doc="Replace published tables with curated, atomically.",
    )
    b.add(
        "hold_publication",
        after="branch_publish",
        callable=f"{t}:hold_publication",
        doc="Keep the last good publication and raise the reasons.",
    )
    b.add(
        "branch_trigger",
        after="publish",
        kind="branch",
        callable=f"{t}:branch_trigger",
        doc="Trigger measures only when published content changed.",
    )
    b.add("trigger_measures", after="branch_trigger", kind="trigger", trigger_dag_id=MEASURES_DAG)
    b.add("skip_trigger", after="branch_trigger", kind="empty")
    b.add(
        "end",
        after=["skip_load", "hold_publication", "trigger_measures", "skip_trigger"],
        kind="empty",
        trigger_rule=NONE_FAILED_MIN_ONE_SUCCESS,
    )
    return b.build(tags=["ingest", feed])


def measures_dag() -> Dag:
    b = DagBuilder(MEASURES_DAG, "dbt measures, longitudinal documents, and the de-identified demo copy.")
    t = DOWNSTREAM_TASKS
    b.add("start", kind="empty")
    b.add("dbt_build", after="start", callable=f"{t}:dbt_build", doc="dbt build: marts and their tests.")
    b.add(
        "build_documents",
        after="dbt_build",
        callable=f"{t}:build_documents",
        doc="Patient, clinic and care-manager documents, built in parallel by clinic.",
    )
    b.add(
        "deidentify",
        after="build_documents",
        callable=f"{t}:deidentify",
        doc="Demo-site copy of the documents and a Safe Harbor-style extract.",
    )
    b.add(
        "verify_deidentified",
        after="deidentify",
        callable=f"{t}:verify_deidentified",
        doc="Leak scan, policy coverage, referential integrity, group sizes.",
    )
    b.add("branch_demo", after="verify_deidentified", kind="branch", callable=f"{t}:branch_demo")
    b.add("publish_demo", after="branch_demo", callable=f"{t}:publish_demo")
    b.add("hold_demo", after="branch_demo", callable=f"{t}:hold_demo")
    b.add("end", after=["publish_demo", "hold_demo"], kind="empty", trigger_rule=NONE_FAILED_MIN_ONE_SUCCESS)
    return b.build(tags=["measures", "documents"])


def monitoring_dag() -> Dag:
    b = DagBuilder(MONITORING_DAG, "Freshness, run health, issue register and the daily digest.", "0 8 * * *")
    t = MONITOR_TASKS
    b.add("start", kind="empty")
    b.add("freshness_report", after="start", callable=f"{t}:freshness_report")
    b.add("run_monitor", after="freshness_report", callable=f"{t}:run_monitor", trigger_rule=ALL_DONE)
    b.add("issue_register", after="run_monitor", callable=f"{t}:issue_register", trigger_rule=ALL_DONE)
    b.add("notify", after="issue_register", callable=f"{t}:notify", trigger_rule=ALL_DONE)
    b.add("end", after="notify", kind="empty", trigger_rule=ALL_DONE)
    return b.build(tags=["monitoring"])


def all_dags(feeds: list[str]) -> list[Dag]:
    return [feed_dag(f) for f in feeds] + [measures_dag(), monitoring_dag()]
