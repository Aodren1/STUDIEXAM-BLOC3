"""Fixtures partagées : un jeu de sources réduit (1/20e) généré une fois par session de tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from fusion.sources.generator import generate

REF_DATE = date(2026, 10, 31)


@pytest.fixture(scope="session")
def small_sources(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("sources")
    generate(out, scale=0.05, ref_date=REF_DATE)
    return out
