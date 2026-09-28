"""Build the document collections: each on its own, patient documents in parallel by clinic.

pophealth documents                          every collection, default workers
pophealth documents --collection clinics     one collection (the troubleshooting path)
pophealth documents --sites S03,S07          patient documents for some clinics (the rest are kept)
pophealth documents --workers 1 --dry-run    serial, and write nothing
"""

from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import yaml

from ..settings import Settings
from ..warehouse import Warehouse
from .docstore import DocumentStore


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    description: str
    key: str
    builder: str
    partition_by: str | None = None

    def resolve(self):
        module, fn = self.builder.split(":")
        return getattr(importlib.import_module(module), fn)


def load_collections(settings: Settings) -> dict[str, CollectionSpec]:
    data = yaml.safe_load((settings.config_dir / "collections.yaml").read_text(encoding="utf-8"))["collections"]
    return {
        name: CollectionSpec(
            name, " ".join(spec["description"].split()), spec["key"], spec["builder"], spec.get("partition_by")
        )
        for name, spec in data.items()
    }


def collect(wh: Warehouse, spec: CollectionSpec, targets: list[str], as_of: date, workers: int) -> list[dict]:
    """Build one collection's documents: one clinic per worker (each with its own cursor) when the
    collection is partitioned, otherwise in one pass."""
    fn = spec.resolve()
    ctx = {"as_of": as_of.isoformat()}
    if spec.partition_by and workers > 1 and len(targets) > 1:

        def one(site: str) -> list[dict]:
            cur = wh.con.cursor()
            try:
                return fn(cur, [site], ctx)
            finally:
                cur.close()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [d for part in pool.map(one, targets) for d in part]
    return fn(wh.con, targets, ctx)


def build_documents(
    settings: Settings,
    as_of: date,
    *,
    collection: str | None = None,
    sites: list[str] | None = None,
    workers: int | None = None,
    dry_run: bool = False,
) -> dict:
    specs = load_collections(settings)
    chosen = [specs[collection]] if collection else list(specs.values())
    workers = workers or int(settings.config.get("documents", {}).get("workers", 4))
    store = DocumentStore(settings.docstore, "restricted")
    report = {}
    with Warehouse(settings.warehouse_path) as wh:
        all_sites = [r["site_id"] for r in wh.rows("select site_id from ref.sites order by site_id")]
        targets = [s for s in all_sites if not sites or s in sites]
        for spec in chosen:
            # Only a collection built clinic by clinic can be built for some clinics. The others are
            # always built whole: a care manager's worklist can span clinics, and a partial build would
            # replace it with part of itself.
            partial = bool(sites) and bool(spec.partition_by)
            docs = collect(wh, spec, targets if partial else all_sites, as_of, workers)
            counts = {"built": len(docs)}
            if not dry_run:
                counts.update(store.sync(spec.name, docs, partial=partial))
            report[spec.name] = counts
    return report
