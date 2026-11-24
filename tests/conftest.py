"""Shared pytest fixtures."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from src.config_loader import load_config, parse_config
from src.models.dto import BuildingConfig

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "simulation_config.json"


@pytest.fixture(scope="session")
def raw_config() -> Dict[str, Any]:
    """The project's simulation_config.json as a decoded dict (do not mutate; use ``raw_config_copy``)."""
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def raw_config_copy(raw_config) -> Dict[str, Any]:
    return copy.deepcopy(raw_config)


@pytest.fixture(scope="session")
def building_config() -> BuildingConfig:
    return load_config(CONFIG_PATH)


@pytest.fixture
def make_config(raw_config):
    """Factory: deep-copy the project config, apply ``mutator(raw)`` and parse it."""

    def _make(mutator=None) -> BuildingConfig:
        raw = copy.deepcopy(raw_config)
        if mutator is not None:
            mutator(raw)
        return parse_config(raw)

    return _make
