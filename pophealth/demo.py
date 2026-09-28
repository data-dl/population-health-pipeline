"""The demo scenario: generate the synthetic supplier inbox, then five scheduled runs.

Each run is "as of" a day; it sees only the files that had arrived by then.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from .documents.docstore import DocumentStore
from .pipeline import RunResult, run_pipeline
from .report import build_page
from .settings import Settings, load_settings
from .warehouse import Warehouse

SCENARIO = [
    (date(2026, 2, 16), "First weekly extract: a year of history from all five suppliers."),
    (
        date(2026, 2, 23),
        "Second extract: late registrations released, appointment statuses updated, late 2025 "
        "lab results and claims restate the closed year.",
    ),
    (
        date(2026, 3, 2),
        "Third extract: the laboratory's new layout (IFCC units) is recognised; the clinic "
        "appointments file arrives truncated and is stopped; the pharmacy file is due.",
    ),
    (date(2026, 3, 3), "The clinic file is re-delivered and published; the pharmacy file is now late."),
    (date(2026, 3, 3), "The same day again: nothing new, nothing changes."),
]
MARKER = ".pophealth-workspace"
EVIDENCE = [
    "summary.md",
    "freshness.csv",
    "dq_scorecard.csv",
    "issues.csv",
    "quarantine.csv",
    "reconciliation.csv",
    "manifest.json",
    "measures.csv",
    "equity.csv",
    "deid_verification.json",
]


def _fresh_workspace(out: Path) -> None:
    if out.exists():
        if not (out / MARKER).exists() and any(out.iterdir()):
            raise SystemExit(f"{out} exists and is not a pophealth demo workspace; choose an empty folder")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("created by `pophealth demo`; safe to delete\n", encoding="utf-8")


def run_scenario(settings: Settings, quiet: bool = False) -> list[tuple[RunResult, str]]:
    results = []
    for as_of, story in SCENARIO:
        result = run_pipeline(settings, as_of)
        results.append((result, story))
        if not quiet:
            print(f"{result.run_id}  {result.status:<8} {story}")
    return results


def run_demo(out: Path, docs: Path | None = None, quiet: bool = False) -> int:
    from synth.generate import generate

    out = out.resolve()
    _fresh_workspace(out)
    key = generate(out / "demo")
    if not quiet:
        print(f"generated {len(key['files'])} supplier files with {len(key['planted'])} planted findings\n")
    settings = load_settings(workspace=out, inbox=out / "demo" / "inbox")
    results = run_scenario(settings, quiet)
    if docs:
        write_sample_run(settings, results, docs.resolve())
        build_page(docs.resolve(), settings, results, key)
        if not quiet:
            print(f"\nsample run and page written to {docs}")
    return 0 if all(r.status == "success" for r, _ in results) else 1


def write_sample_run(settings: Settings, results: list[tuple[RunResult, str]], docs: Path) -> None:
    target = docs / "sample_run"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    index = [
        "# Sample run",
        "",
        "The five scheduled runs of `pophealth demo`, as committed evidence. Each folder holds "
        "what the run wrote: `summary.md` (read this first), the freshness report, the data-quality "
        "scorecard, the issue register, held rows, row-count reconciliation, and a manifest. Measures, "
        "equity tables and the de-identification report appear in runs that refreshed them.",
        "",
        "| run | as of | what happens |",
        "|---|---|---|",
    ]
    for result, story in results:
        folder = target / result.run_id
        folder.mkdir()
        for name in EVIDENCE:
            source = result.evidence_dir / name
            if source.exists():
                shutil.copyfile(source, folder / name)
        index.append(f"| [{result.run_id}]({result.run_id}/summary.md) | {result.as_of.isoformat()} | {story} |")
    (target / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8", newline="\n")
    restricted = DocumentStore(settings.docstore, "restricted").find("patients")
    demo = {d["_id"]: d for d in DocumentStore(settings.docstore, "demo").find("patients")}
    with Warehouse(settings.warehouse_path) as wh:
        pairs = {
            r["real_value"]: r["surrogate"]
            for r in wh.rows("select real_value, surrogate from restricted.deid_crosswalk where kind = 'patient'")
        }
    example = next(d for d in restricted if d["screenings"] and d["labs"] and d["medications"] and d["careGaps"])
    sample = {
        "note": "Both documents are synthetic. 'restricted' is what the care-management application reads; "
        "'demo' is the same patient after the demo_site policy.",
        "restricted": example,
        "demo": demo[pairs[example["_id"]]],
    }
    (target / "document_before_after.json").write_text(
        json.dumps(sample, indent=1, sort_keys=False) + "\n", encoding="utf-8", newline="\n"
    )
