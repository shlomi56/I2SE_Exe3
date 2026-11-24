"""Phase 1 smoke test: package layout is importable and config file exists."""
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_packages_importable():
    for pkg in ("src", "src.models", "src.simulation", "src.dispatchers", "src.gui", "src.comms"):
        importlib.import_module(pkg)


def test_config_is_valid_json():
    cfg = json.loads((ROOT / "simulation_config.json").read_text())
    assert cfg["schema_version"] == "1.0"
