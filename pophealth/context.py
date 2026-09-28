"""What every task receives: the run's identity, settings, a warehouse factory and the notifier."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .contracts import Contract
from .feeds import FeedSpec, feed_spec
from .notify import Notifier
from .settings import Settings
from .warehouse import Warehouse


@dataclass
class TaskContext:
    settings: Settings
    run_id: str
    as_of: date
    dag_id: str
    task_id: str
    notifier: Notifier
    evidence_dir: Path
    params: dict = field(default_factory=dict)

    def warehouse(self) -> Warehouse:
        return Warehouse(self.settings.warehouse_path)

    @property
    def feed(self) -> str:
        return self.params["feed"]

    @property
    def contract(self) -> Contract:
        return self.settings.contracts[self.feed]

    @property
    def spec(self) -> FeedSpec:
        return feed_spec(self.settings, self.feed)
