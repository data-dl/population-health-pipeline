"""Generated documentation is current, and the CLI works."""

from __future__ import annotations

import subprocess
import sys

from pophealth.cli import main
from pophealth.docsgen import generate_docs
from pophealth.settings import load_settings


def test_generated_docs_are_current():
    assert generate_docs(load_settings(), check=True) == []


def test_cli_lists_dags(capsys):
    assert main(["dags"]) == 0
    out = capsys.readouterr().out
    assert "feed_labs" in out and "measures_and_documents" in out and "none_failed_min_one_success" in out


def test_cli_reads_the_issue_register(scenario, capsys):
    assert main(["--workspace", str(scenario.root), "--inbox", str(scenario.settings.inbox), "issues"]) == 0
    assert "pharmacy:pharmacy_claims:late" in capsys.readouterr().out


def test_module_entry_point():
    out = subprocess.run([sys.executable, "-m", "pophealth", "--help"], capture_output=True, text=True, check=True)
    assert "run" in out.stdout and "demo" in out.stdout
