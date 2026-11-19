"""Configuration par variables d'environnement (12-factor) ; valeurs par défaut adaptées au poste local."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("FUSION_DATA_DIR", str(ROOT / "data"))))
    ref_date: date = field(default_factory=lambda: date.fromisoformat(_env("FUSION_REF_DATE", "2026-10-31")))
    seed_scale: float = field(default_factory=lambda: float(_env("FUSION_SEED_SCALE", "1")))
    bus: str = field(default_factory=lambda: _env("FUSION_BUS", "file"))  # file | kafka
    kafka_bootstrap: str = field(default_factory=lambda: _env("KAFKA_BOOTSTRAP", "localhost:9092"))
    lake: str = field(default_factory=lambda: _env("FUSION_LAKE", "local"))  # local | webhdfs
    webhdfs_url: str = field(default_factory=lambda: _env("WEBHDFS_URL", "http://localhost:9870"))
    hdfs_user: str = field(default_factory=lambda: _env("HDFS_USER", "fusion"))
    hdfs_root: str = field(default_factory=lambda: _env("HDFS_ROOT", "/datalake/fusion"))

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def lake_dir(self) -> Path:
        return self.data_dir / "lake"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse.sqlite"

    @property
    def gold_path(self) -> Path:
        return self.data_dir / "lake" / "gold" / "dashboard.json"

    @property
    def run_report_path(self) -> Path:
        return self.data_dir / "rapport_execution.json"
