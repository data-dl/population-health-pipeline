"""Data contracts: one YAML file per feed (config/contracts/) describing what the supplier sends.

A contract names the steward and supplier, the delivery cadence, every file layout the supplier has
used (each maps source columns to canonical names), the classification of every column, and the
feed's rule registry. The pipeline reads nothing about a supplier from anywhere else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

RULE_TYPES = {
    "volume",
    "date_format",
    "completeness",  # file level
    "repair",
    "required",
    "parse",
    "not_future",
    "in_reference",
    "mapped",
    "on_roster",
    "key_conflict",
    "closed_period",
    "expression",  # row level
}
FILE_LEVEL = {"volume", "date_format", "completeness"}
ACTIONS = {"block", "quarantine", "repair", "warn"}
CLASSIFICATIONS = {"direct_identifier", "quasi_identifier", "clinical", "operational", "reference"}


@dataclass(frozen=True)
class Layout:
    version: int
    columns: dict[str, str]  # source column -> canonical text column
    change_record: str | None = None


@dataclass(frozen=True)
class Source:
    name: str
    pattern: re.Pattern
    layouts: tuple[Layout, ...]

    def match_layout(self, header: list[str]) -> Layout | None:
        wanted = [h.strip() for h in header]
        for layout in self.layouts:
            if sorted(layout.columns) == sorted(wanted) and len(set(wanted)) == len(wanted):
                return layout
        return None

    def closest_layout(self, header: list[str]) -> tuple[Layout, list[str], list[str]]:
        got = {h.strip() for h in header}

        def score(layout: Layout) -> float:
            cols = set(layout.columns)
            return len(cols & got) / max(1, len(cols | got))

        best = max(self.layouts, key=lambda lay: (score(lay), lay.version))
        missing = [c for c in best.columns if c not in got]
        unexpected = [h.strip() for h in header if h.strip() not in best.columns]
        return best, missing, unexpected


@dataclass(frozen=True)
class Rule:
    id: str
    type: str
    title: str
    dimension: str
    action: str
    retryable: bool = False
    message: str = ""
    params: dict = field(default_factory=dict)

    @property
    def file_level(self) -> bool:
        return self.type in FILE_LEVEL


@dataclass(frozen=True)
class FileMatch:
    source: str
    extract: date
    redelivery: int


@dataclass
class Contract:
    feed: str
    title: str
    description: str
    steward: str
    supplier: str
    snapshot: bool
    cadence_days: int
    grace_days: int
    time_column: str | None
    date_formats: list[str]
    date_columns: list[str]
    natural_key: list[str]
    sources: dict[str, Source]
    columns: list[dict]
    rules: list[Rule]
    published: list[str]
    freshness: dict | None = None
    path: Path | None = None

    def text_columns(self) -> list[str]:
        seen: list[str] = []
        for source in self.sources.values():
            for layout in source.layouts:
                for target in layout.columns.values():
                    if target not in seen:
                        seen.append(target)
        return seen

    def match_file(self, name: str) -> FileMatch | None:
        for source in self.sources.values():
            m = source.pattern.match(name)
            if m:
                extract = datetime.strptime(m.group("extract"), "%Y%m%d").date()
                redelivery = int(m.group("redelivery") or 0) if "redelivery" in m.groupdict() else 0
                return FileMatch(source.name, extract, redelivery)
        return None

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    def classification(self, column: str) -> str | None:
        for c in self.columns:
            if c["name"] == column:
                return c.get("classification")
        return None


class ContractError(ValueError):
    pass


def _rule(raw: dict, feed: str) -> Rule:
    known = {"id", "type", "title", "dimension", "action", "retryable", "message"}
    missing = [k for k in ("id", "type", "title", "dimension", "action") if k not in raw]
    if missing:
        raise ContractError(f"{feed}: rule {raw.get('id', '?')} is missing {missing}")
    if raw["type"] not in RULE_TYPES:
        raise ContractError(f"{feed}: rule {raw['id']} has unknown type {raw['type']!r}")
    if raw["action"] not in ACTIONS:
        raise ContractError(f"{feed}: rule {raw['id']} has unknown action {raw['action']!r}")
    params = {k: v for k, v in raw.items() if k not in known}
    return Rule(
        raw["id"],
        raw["type"],
        raw["title"],
        raw["dimension"],
        raw["action"],
        bool(raw.get("retryable")),
        raw.get("message", ""),
        params,
    )


def load_contract(path: Path) -> Contract:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    feed = data["feed"]
    sources = {}
    for name, spec in data["sources"].items():
        layouts = tuple(
            Layout(int(lay["version"]), dict(lay["columns"]), lay.get("change_record")) for lay in spec["layouts"]
        )
        sources[name] = Source(name, re.compile(spec["file_pattern"]), layouts)
    rules = [_rule(r, feed) for r in data.get("rules", [])]
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise ContractError(f"{feed}: duplicate rule ids")
    if data.get("snapshot") and any(r.retryable for r in rules):
        raise ContractError(f"{feed}: a snapshot's held rows are replaced by the next snapshot; no rule can retry")
    for column in data.get("columns", []):
        if column.get("classification") not in CLASSIFICATIONS:
            raise ContractError(f"{feed}: column {column.get('name')} has no valid classification")
    delivery = data.get("delivery", {})
    return Contract(
        feed=feed,
        title=data["title"],
        description=" ".join(str(data.get("description", "")).split()),
        steward=data["steward"],
        supplier=data["supplier"],
        snapshot=bool(data.get("snapshot")),
        cadence_days=int(delivery.get("cadence_days", 7)),
        grace_days=int(delivery.get("grace_days", 0)),
        time_column=delivery.get("time_column"),
        date_formats=list(data["date_formats"]),
        date_columns=list(data.get("date_columns", [])),
        natural_key=list(data["natural_key"]),
        sources=sources,
        columns=list(data.get("columns", [])),
        rules=rules,
        published=list(data.get("published", [])),
        freshness=delivery.get("freshness"),
        path=path,
    )


def load_contracts(config_dir: Path) -> dict[str, Contract]:
    contracts = {}
    for path in sorted((config_dir / "contracts").glob("*.yaml")):
        contract = load_contract(path)
        contracts[contract.feed] = contract
    return contracts
