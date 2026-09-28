"""Pipeline settings: config/pipeline.yaml plus the contracts, resolved against a workspace root."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .contracts import Contract, load_contracts

REPO = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    workspace: Path
    config_dir: Path
    config: dict
    contracts: dict[str, Contract]
    inbox_override: Path | None = None

    def _path(self, key: str) -> Path:
        return (self.workspace / self.config["paths"][key]).resolve()

    @property
    def inbox(self) -> Path:
        return self.inbox_override.resolve() if self.inbox_override else self._path("inbox")

    @property
    def lake(self) -> Path:
        return self._path("lake")

    @property
    def warehouse_path(self) -> Path:
        return self._path("warehouse")

    @property
    def docstore(self) -> Path:
        return self._path("docstore")

    @property
    def extracts(self) -> Path:
        return self._path("extracts")

    @property
    def runs(self) -> Path:
        return self._path("runs")

    @property
    def dbt_project(self) -> Path:
        return REPO / "dbt"

    @property
    def feeds_order(self) -> list[str]:
        return list(self.config["feeds_order"])

    @property
    def measurement_year(self) -> int:
        return int(self.config["measurement"]["year"])

    @property
    def closed_after(self) -> date:
        value = self.config["measurement"]["closed_after"]
        return value if isinstance(value, date) else date.fromisoformat(str(value))

    @property
    def min_cell_size(self) -> int:
        return int(self.config["reporting"]["min_cell_size"])

    def reference(self, name: str) -> dict:
        return yaml.safe_load((self.config_dir / "reference" / name).read_text(encoding="utf-8"))


def load_settings(workspace: Path | None = None, config_dir: Path | None = None, inbox: Path | None = None) -> Settings:
    config_dir = (config_dir or REPO / "config").resolve()
    config = yaml.safe_load((config_dir / "pipeline.yaml").read_text(encoding="utf-8"))
    return Settings(
        workspace=(workspace or REPO).resolve(),
        config_dir=config_dir,
        config=config,
        contracts=load_contracts(config_dir),
        inbox_override=inbox,
    )
