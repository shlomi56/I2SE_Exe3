"""Phase 5 — decoupled GUI: replay capture, HTML rendering, charts, the Streamlit app (headless AppTest)
and the guarantee that core modules never import GUI code."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.benchmark import build_scenario
from src.gui.replay import STATUSES, STATUS_FAULT, record_simulation
from src.gui.render import render_building, render_events, render_kpi_strip, status_legend
from src.request_generator import RequestGenerator
from tests.conftest import ROOT

CORE_PACKAGES = ("src/models", "src/simulation", "src/dispatchers")
GUI_MODULES = {"streamlit", "altair", "pandas", "tkinter", "pygame", "matplotlib", "plotly"}


@pytest.fixture(scope="module")
def raw_config():
    return json.loads((ROOT / "simulation_config.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def full_replay(raw_config):
    cfg = build_scenario(raw_config, "full", 42)
    return record_simulation(cfg, RequestGenerator().generate(cfg), "MC_DDI", scenario="full", sample_dt=0.5)


# ============================================================================ decoupling


def test_core_packages_have_no_gui_imports_static():
    offenders = []
    for pkg in CORE_PACKAGES:
        for path in (ROOT / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for n in names:
                    if n.split(".")[0] in GUI_MODULES or n.startswith("src.gui"):
                        offenders.append(f"{path.relative_to(ROOT)}: {n}")
    assert offenders == []


def test_headless_run_loads_no_gui_modules():
    code = (
        "import sys, json;"
        "from src.config_loader import load_config;"
        "from src.request_generator import RequestGenerator;"
        "from src.dispatchers import create_dispatcher;"
        "from src.simulation import SimulationEngine;"
        "cfg = load_config('simulation_config.json');"
        "s = SimulationEngine(cfg, create_dispatcher('MC_DDI'), RequestGenerator().generate(cfg)).run();"
        f"bad = [m for m in sys.modules if m.split('.')[0] in {sorted(GUI_MODULES)!r} or m.startswith('src.gui')];"
        "print(json.dumps({'done': s['completed_requests'], 'bad': bad}))"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out == {"done": 200, "bad": []}


def test_replay_and_render_modules_do_not_need_streamlit():
    code = "import sys, src.gui.replay, src.gui.render; print('streamlit' in sys.modules)"
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "False", proc.stderr


# ============================================================================ replay capture


def test_replay_frames_cover_the_run(full_replay):
    r = full_replay
    assert r.frames[0].time == 0.0
    # the engine finishes once the last passenger's doors have opened (alighting), within one tick
    last_arrival = max(c[0] for c in r.completions)
    assert last_arrival <= r.end_time <= last_arrival + 2.0 + 0.1 + 1e-6
    times = [f.time for f in r.frames]
    assert times == sorted(times)
    assert all(len(f.cars) == 3 for f in r.frames)
    assert {c.status for f in r.frames for c in f.cars} <= set(STATUSES)


def test_replay_final_kpis_match_engine_summary(full_replay):
    k = full_replay.kpis_at(full_replay.end_time + 1)
    s = full_replay.summary
    assert k["completed"] == s["completed_requests"] == 200
    assert round(k["avg_journey"], 2) == s["avg_journey_time_sec"]
    assert round(k["avg_wait"], 2) == s["avg_hall_wait_time_sec"]
    assert round(k["max_wait"], 2) == s["max_hall_wait_time_sec"]
    assert k["stops"] == s["total_elevator_stops"]
    assert k["overloads"] == s["overload_events"]


def test_replay_shows_vibration_fault_window(full_replay):
    inside = full_replay.frame_at(1250.0)
    outside = full_replay.frame_at(1400.0)
    car1_in = next(c for c in inside.cars if c.elevator_id == 1)
    car1_out = next(c for c in outside.cars if c.elevator_id == 1)
    assert car1_in.status == STATUS_FAULT and not car1_in.operational and car1_in.vibration == 7.5
    assert car1_out.status != STATUS_FAULT
    assert [w[0] for w in full_replay.active_faults(1250.0)] == [1]
    kinds = {e.kind for e in full_replay.events}
    assert {"ALLOC", "FAULT_START", "FAULT_END"} <= kinds


def test_replay_frame_at_and_events_between(full_replay):
    f = full_replay.frame_at(100.3)
    assert f.time <= 100.3 < f.time + full_replay.sample_dt + 1e-9
    ev = full_replay.events_between(0.0, 60.0)
    assert all(0.0 <= e.time <= 60.0 for e in ev)


def test_replay_records_overloads_and_door_obstructions(make_config):
    def mut(raw):
        raw["sensors"]["injected_faults"] = []
        raw["sensors"]["door_obstruction_probability"] = 1.0

    from src.models.dto import Request

    cfg = make_config(mut)
    heavy = [Request(i, 0.0, 0, 10, 140.0) for i in range(12)]
    r = record_simulation(cfg, heavy, "FCFS", sample_dt=0.25)
    kinds = [e.kind for e in r.events]
    assert "OVERLOAD" in kinds and "REALLOC" in kinds and "DOOR_OBSTRUCTION" in kinds
    assert r.kpis_at(r.end_time)["overloads"] == r.summary["overload_events"] > 0
    assert any(c.door_obstructed for f in r.frames for c in f.cars)
    assert any(c.status == "OVERLOADED" for f in r.frames for c in f.cars)


# ============================================================================ rendering


def test_render_building_contains_floors_cars_and_queues(full_replay):
    html = render_building(full_replay.frame_at(1250.0), full_replay)
    for label in ["15", "L 0", "B1", "B2"]:
        assert f">{label}</div>" in html
    for eid in range(3):
        assert f'<span class="cid">E{eid}</span>' in html
    assert "VIBRATION 7.5" in html and "Fault" in html
    compact = render_building(full_replay.frame_at(1250.0), full_replay, compact=True, title="MC-DDI")
    assert "MC-DDI" in compact and 'class="eb compact"' in compact


def test_render_waiting_chips(full_replay):
    frame = next(f for f in full_replay.frames if f.waiting)
    html = render_building(frame, full_replay)
    assert "eb-chip" in html


def test_render_events_and_kpis_and_legend(full_replay):
    html = render_events(full_replay.events_between(1199, 1201))
    assert "Vibration fault on E1" in html
    assert "No active alerts" in render_events([])
    strip = render_kpi_strip(full_replay.kpis_at(600.0))
    assert "Avg journey" in strip
    legend = status_legend()
    for label in ["Moving", "Doors open", "Overloaded", "Fault", "Idle"]:
        assert label in legend


# ============================================================================ charts


def test_charts_build_valid_vega_specs(full_replay):
    from src.gui.charts import ALGO_COLORS, journey_over_time_chart, kpi_comparison_chart

    summaries = {"FCFS": dict(full_replay.summary), "SCAN": dict(full_replay.summary), "MC_DDI": full_replay.summary}
    spec = kpi_comparison_chart(summaries).to_dict()
    assert spec["resolve"]["scale"]["y"] == "independent"  # small multiples, never a dual axis
    text = json.dumps(spec)
    for color in ALGO_COLORS.values():
        assert color in text
    line = journey_over_time_chart({"MC_DDI": full_replay}).to_dict()
    assert "layer" in line


# ============================================================================ Streamlit app (headless)


@pytest.fixture(scope="module")
def app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "src" / "gui" / "app.py"), default_timeout=180)
    at.run()
    return at


def test_app_renders_without_exceptions(app):
    assert not app.exception
    assert app.title[0].value == "Smart Elevator Dispatch"
    labels = [m.label for m in app.metric]
    assert labels[:6] == ["Delivered", "Avg journey", "Avg hall wait", "Max hall wait", "Stops", "Overload bumps"]


def test_app_scrub_compare_and_algorithm_switch(app):
    app.slider(key="t").set_value(1250.0).run()
    assert not app.exception
    assert app.metric[0].value.endswith("/200") and app.metric[0].value != "0/200"
    app.sidebar.segmented_control(key="algorithm").set_value("SCAN").run()
    assert not app.exception and "SCAN dispatcher" in app.caption[0].value
    app.sidebar.segmented_control(key="mode").set_value("Compare all").run()
    assert not app.exception
    assert app.slider(key="t").value == 1250.0  # timeline position survives the view switch


def test_app_play_pause_and_jump(app):
    app.sidebar.button[0].click().run()  # Play
    assert app.session_state["playing"] is True and not app.exception
    app.sidebar.button[0].click().run()  # Pause
    assert app.session_state["playing"] is False
    target = next(k for k in app.session_state["_jump_targets"] if "vibration fault" in k)
    app.sidebar.selectbox(key="jump").set_value(target).run()
    assert not app.exception and app.session_state["t"] == pytest.approx(1205.0)


def test_streamlit_theme_is_light():
    import tomllib

    theme = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())["theme"]
    assert theme["base"] == "light"
