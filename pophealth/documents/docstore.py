"""A small document store with the upsert semantics of a document database.

One JSON Lines file per collection, one database per folder (`restricted` holds real documents,
`demo_staging` and `demo` hold de-identified ones). `sync` upserts by `_id` and removes documents no
longer produced, reporting inserted / updated / unchanged / deleted - the same counts a bulk
`replace_one(..., upsert=True)` against MongoDB would give. Files are written sorted by `_id`, so the
same input always gives the same bytes.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


class DocumentStore:
    def __init__(self, root: Path, database: str):
        self.path = Path(root) / database

    def _file(self, collection: str) -> Path:
        return self.path / f"{collection}.jsonl"

    def collections(self) -> list[str]:
        return sorted(p.stem for p in self.path.glob("*.jsonl")) if self.path.exists() else []

    def find(self, collection: str, where: dict | None = None) -> list[dict]:
        path = self._file(collection)
        if not path.exists():
            return []
        docs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if where:
            docs = [d for d in docs if all(d.get(k) == v for k, v in where.items())]
        return docs

    def get(self, collection: str, doc_id: str) -> dict | None:
        return next((d for d in self.find(collection) if d["_id"] == doc_id), None)

    def count(self, collection: str) -> int:
        return len(self.find(collection))

    def sync(self, collection: str, docs: list[dict], *, partial: bool = False) -> dict[str, int]:
        """Upsert `docs`; unless `partial`, delete documents that were not produced this time."""
        existing = {d["_id"]: d for d in self.find(collection)}
        incoming = {d["_id"]: d for d in docs}
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
        merged = dict(existing)
        for doc_id, doc in incoming.items():
            if doc_id not in existing:
                counts["inserted"] += 1
            elif _canonical(existing[doc_id]) != _canonical(doc):
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            merged[doc_id] = doc
        if not partial:
            for doc_id in set(existing) - set(incoming):
                del merged[doc_id]
                counts["deleted"] += 1
        self._write(collection, merged.values())
        return counts

    def _write(self, collection: str, docs) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        tmp = self._file(collection).with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for doc in sorted(docs, key=lambda d: d["_id"]):
                f.write(_canonical(doc) + "\n")
        os.replace(tmp, self._file(collection))

    def replace_with(self, other: DocumentStore) -> None:
        """Make this database an exact copy of `other` (used to release a verified demo copy)."""
        if self.path.exists():
            shutil.rmtree(self.path)
        shutil.copytree(other.path, self.path)


def _canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)
