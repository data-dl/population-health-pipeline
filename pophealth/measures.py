"""Run the dbt project (models and tests) against the warehouse and record what it did.

dbt runs in its own process - as it would under an orchestrator - reading the published schema and
writing the marts. Its run_results.json is loaded into audit.dbt_results, so a failed test is a row
in the warehouse, not only a line in a log.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date

from .settings import Settings
from .warehouse import Warehouse


def dbt_command(settings: Settings, as_of: date, target) -> list[str]:
    variables = {
        "measurement_year": settings.measurement_year,
        "as_of_date": as_of.isoformat(),
        "min_cell_size": settings.min_cell_size,
    }
    project = str(settings.dbt_project)
    return [
        sys.executable,
        "-m",
        "dbt.cli.main",
        "build",
        "--project-dir",
        project,
        "--profiles-dir",
        project,
        "--target-path",
        str(target),
        "--log-path",
        str(target / "logs"),
        "--vars",
        json.dumps(variables),
        "--no-version-check",
        "--no-use-colors",
    ]


def run_dbt(settings: Settings, run_id: str, as_of: date) -> dict:
    target = settings.lake.parent / "dbt_target"
    target.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ, POPHEALTH_DB=str(settings.warehouse_path), DO_NOT_TRACK="1", DBT_SEND_ANONYMOUS_USAGE_STATS="false"
    )
    results_path = target / "run_results.json"
    results_path.unlink(missing_ok=True)  # a build that dies early must not report the last one's results
    proc = subprocess.run(
        dbt_command(settings, as_of, target),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    results = json.loads(results_path.read_text(encoding="utf-8"))["results"] if results_path.exists() else []
    # unique_id is <resource type>.<project>.<name>[.<hash>]: a generic test's last part is a hash.
    rows = [
        [
            run_id,
            r["unique_id"],
            r["unique_id"].split(".")[0],
            r["unique_id"].split(".")[2],
            r["status"],
            (r.get("message") or "")[:500],
            float(r.get("execution_time") or 0),
        ]
        for r in results
    ]
    if rows:
        with Warehouse(settings.warehouse_path) as wh:
            wh.con.executemany("insert into audit.dbt_results values (?, ?, ?, ?, ?, ?, ?)", rows)
    failures = [
        f"{r['unique_id']}: {r['status']} {r.get('message') or ''}".strip()
        for r in results
        if r["status"] not in ("success", "pass")
    ]
    return {
        "ok": proc.returncode == 0,
        "models": sum(1 for r in results if r["unique_id"].startswith("model.")),
        "tests": sum(1 for r in results if r["unique_id"].startswith("test.")),
        "seeds": sum(1 for r in results if r["unique_id"].startswith("seed.")),
        "failures": failures,
        "log_tail": proc.stdout[-3000:] if proc.returncode else "",
    }
