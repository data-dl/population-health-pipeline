"""python -m synth --out demo [--seed N]"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from .generate import generate
from .world import SEED


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic supplier inbox and its answer key.")
    parser.add_argument("--out", default="demo", help="output folder (default: demo)")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    key = generate(Path(args.out), args.seed)
    rows = sum(f["rows"] for f in key["files"])
    kinds = Counter(e["kind"] for e in key["planted"])
    print(
        f"{len(key['files'])} files, {rows:,} rows, {len(key['planted'])} planted findings "
        f"of {len(kinds)} kinds -> {args.out}/inbox, {args.out}/answer_key.json"
    )


if __name__ == "__main__":
    main()
