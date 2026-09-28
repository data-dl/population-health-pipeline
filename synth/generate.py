"""Write the supplier inbox, committed samples and the answer key.

    python -m synth --out demo

`demo/inbox/<arrival date>/<file>` - every supplier file, organised by the day it arrives, so a run
"as of" a date sees exactly what had arrived by then. `demo/samples/` - the first rows of each
file layout, committed so a reader can see the shapes without generating anything.
`demo/answer_key.json` - every planted defect by file and row, the expected outcome of every file,
and the expected measure results.
"""

from __future__ import annotations

import csv
import json
import random
import shutil
from pathlib import Path

from .deliveries import DeliveryFile, Renderer
from .oracle import expected_measures
from .world import EXTRACTS, REDELIVERY_ARRIVAL, SEED, World, build_world

SAMPLE_ROWS = 8


def write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(column, "") for column in header])


def answer_key(world: World, files: list[DeliveryFile]) -> dict:
    planted = []
    file_entries = []
    for f in files:
        file_entries.append(
            {
                "name": f.name,
                "feed": f.feed,
                "source": f.source,
                "arrival": f.arrival.isoformat(),
                "extract": f.extract.isoformat(),
                "rows": len(f.rows),
                "layout": f.layout,
                "outcome": f.outcome,
                **({"rule": f.outcome_rule} if f.outcome_rule else {}),
                **({"counts": f.counts} if f.counts else {}),
            }
        )
        for number, row in enumerate(f.rows, start=1):
            for e in row.expect:
                planted.append({"feed": f.feed, "file": f.name, "row": number, "key": row.key, **e})
    planted.sort(key=lambda e: (e["feed"], e["file"], e["row"], e["rule"]))
    for i, entry in enumerate(planted, start=1):
        entry["id"] = f"P{i:04d}"
    return {
        "seed": world.seed,
        "extracts": [e.isoformat() for e in EXTRACTS],
        "redelivery_arrival": REDELIVERY_ARRIVAL.isoformat(),
        "missing_files": [
            {
                "feed": "pharmacy",
                "source": "pharmacy_claims",
                "extract": EXTRACTS[-1].isoformat(),
                "name": f"rx_claims_{EXTRACTS[-1].strftime('%Y%m%d')}.csv",
            }
        ],
        "files": file_entries,
        "planted": planted,
        "measures": expected_measures(world),
    }


def generate(out: Path, seed: int = SEED) -> dict:
    world = build_world(seed)
    files = Renderer(world, random.Random(seed + 1)).render()
    inbox = out / "inbox"
    if inbox.exists():
        shutil.rmtree(inbox)
    samples = out / "samples"
    if samples.exists():
        shutil.rmtree(samples)
    seen_layouts: set[tuple[str, str, int]] = set()
    for f in files:
        rows = [r.values for r in f.rows]
        write_csv(inbox / f.arrival.isoformat() / f.name, f.header, rows)
        layout = (f.feed, f.source, f.layout)
        if layout not in seen_layouts:
            seen_layouts.add(layout)
            write_csv(samples / f.name, f.header, rows[:SAMPLE_ROWS])
    key = answer_key(world, files)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "answer_key.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(key, fh, indent=1, sort_keys=False)
        fh.write("\n")
    return key
