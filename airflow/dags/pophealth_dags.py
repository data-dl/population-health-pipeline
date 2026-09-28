"""Airflow DAG file: one DAG per feed, measures and documents, monitoring.

The task graphs are defined once, in pophealth/orchestration/dags.py; this file only hands them to
Airflow. Parsing imports nothing heavier than the definitions themselves.
"""

from pophealth.orchestration.airflow_adapter import to_airflow
from pophealth.orchestration.dags import all_dags
from pophealth.settings import load_settings

for _spec in all_dags(load_settings().feeds_order):
    globals()[_spec.dag_id] = to_airflow(_spec)
