"""Smart Elevator Dispatch — Streamlit dashboard.

Run from the project root:

    .venv/bin/streamlit run src/gui/app.py
    .venv/bin/streamlit run src/gui/app.py -- --config path/to/simulation_config.json

The dashboard never drives the simulation: each (scenario, seed, algorithm) is simulated headlessly once,
recorded through ``ISimulationObserver`` into a :class:`Replay`, cached, and then scrubbed/played back.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` puts only the script's folder on sys.path
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.benchmark import build_scenario  # noqa: E402
from src.gui.charts import ALGO_COLORS, ALGO_ORDER, journey_over_time_chart, kpi_comparison_chart  # noqa: E402
from src.gui.render import render_building, render_events, render_kpi_strip, status_legend  # noqa: E402
from src.gui.replay import Replay, SimEvent, record_simulation  # noqa: E402
from src.request_generator import RequestGenerator  # noqa: E402

SCENARIOS = {
    "full": "Full day (3 phases + fault)",
    "up_peak": "Morning up-peak",
    "midday": "Midday mixed",
    "down_peak": "Evening down-peak",
}
SPEEDS = [1, 2, 5, 10, 20, 30, 60, 120]
SAMPLE_DT = 0.25
TICK_SEC = 0.25
ALGO_LABEL = {"FCFS": "FCFS", "SCAN": "SCAN", "MC_DDI": "MC-DDI"}


def _config_path() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=str(ROOT / "simulation_config.json"))
    args, _ = parser.parse_known_args(sys.argv[1:])
    return Path(args.config)


@st.cache_resource(show_spinner=False, max_entries=24)
def get_replay(config_text: str, scenario: str, seed: int, algorithm: str) -> Replay:
    cfg = build_scenario(json.loads(config_text), scenario, seed)
    requests = RequestGenerator().generate(cfg)
    return record_simulation(cfg, requests, algorithm, scenario=scenario, sample_dt=SAMPLE_DT)


def _fmt_s(x: float) -> str:
    return f"{x:.1f} s"


def kpi_metrics(replay: Replay, t: float) -> None:
    k = replay.kpis_at(t)
    items = [
        ("Delivered", f"{k['completed']}/{k['total']}"),
        ("Avg journey", _fmt_s(k["avg_journey"])),
        ("Avg hall wait", _fmt_s(k["avg_wait"])),
        ("Max hall wait", _fmt_s(k["max_wait"])),
        ("Stops", f"{k['stops']}"),
        ("Overload bumps", f"{k['overloads']}"),
    ]
    per_row = 2
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col, (label, value) in zip(cols, items[i : i + per_row]):
            col.metric(label, value, border=True)


def active_alerts(replay: Replay, t: float) -> list:
    frame = replay.frame_at(t)
    alerts = [
        SimEvent(start, "FAULT_START", eid, f"E{eid} vibration fault active ({sev:g} > threshold) — no new calls until {end:.0f} s")
        for eid, ftype, start, end, sev in replay.active_faults(t)
    ]
    alerts += [SimEvent(t, "DOOR_OBSTRUCTION", c.elevator_id, f"E{c.elevator_id} door obstructed — closing delayed")
               for c in frame.cars if c.door_obstructed]
    alerts += [SimEvent(t, "OVERLOAD", c.elevator_id, f"E{c.elevator_id} overloaded — doors held open")
               for c in frame.cars if c.status == "OVERLOADED"]
    alerts += [e for e in replay.events_between(t - 30.0, t) if e.kind in ("OVERLOAD", "REALLOC")]
    return alerts


def _jump() -> None:
    target = st.session_state.get("_jump_targets", {}).get(st.session_state.jump)
    if target is not None:
        st.session_state.t = target
        st.session_state.playing = False


def notable_moments(replay: Replay) -> dict:
    """Label -> time for the 'Jump to' picker (fault windows, overload bumps, first busy minute)."""
    out = {}
    for eid, ftype, start, end, sev in replay.fault_windows:
        out[f"E{eid} vibration fault ({start:.0f}–{end:.0f} s)"] = start + 5.0
    for e in [e for e in replay.events if e.kind == "OVERLOAD"][:5]:
        out[f"Overload bump on E{e.elevator_id} at {e.time:.0f} s"] = round(e.time / SAMPLE_DT) * SAMPLE_DT
    for e in [e for e in replay.events if e.kind == "DOOR_OBSTRUCTION"][:3]:
        out[f"Door obstruction on E{e.elevator_id} at {e.time:.0f} s"] = round(e.time / SAMPLE_DT) * SAMPLE_DT
    if replay.completions:
        out["Morning rush (5 min in)"] = 300.0
    return dict(sorted(out.items(), key=lambda kv: kv[1]))


def main() -> None:
    st.set_page_config(page_title="Smart Elevator Dispatch", page_icon="🛗", layout="wide")
    st.html(
        "<style>"
        ".block-container{padding-top:1.6rem;padding-bottom:2rem;max-width:1500px}"
        "[data-testid='stMetricValue']{font-size:1.35rem;font-weight:800}"
        "[data-testid='stMetricLabel'] p{font-size:.78rem;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}"
        "h1{font-weight:850!important;letter-spacing:-.02em}"
        "</style>"
    )
    config_path = _config_path()
    config_text = config_path.read_text(encoding="utf-8")
    default_seed = json.loads(config_text)["simulation"]["random_seed"]

    ss = st.session_state
    ss.setdefault("playing", False)
    ss.setdefault("t", 0.0)

    # ------------------------------------------------------------------ sidebar
    with st.sidebar:
        st.markdown("### 🛗 Scenario")
        scenario = st.selectbox("Traffic profile", list(SCENARIOS), format_func=SCENARIOS.get, key="scenario")
        seed = int(st.number_input("Random seed", min_value=0, max_value=10_000_000, value=default_seed, step=1, key="seed"))
        mode = st.segmented_control("View", ["Single", "Compare all"], default="Single", key="mode") or "Single"
        algorithm = "MC_DDI"
        if mode == "Single":
            algorithm = st.segmented_control("Algorithm", ALGO_ORDER, default="MC_DDI",
                                             format_func=ALGO_LABEL.get, key="algorithm") or "MC_DDI"
        st.markdown("### ▶ Playback")
        speed = st.select_slider("Speed (sim seconds per second)", SPEEDS, value=10, format_func=lambda v: f"{v}×", key="speed")
        c1, c2 = st.columns(2)
        if c1.button("⏸ Pause" if ss.playing else "▶ Play", type="primary", width="stretch"):
            ss.playing = not ss.playing
            st.rerun()
        if c2.button("⟲ Restart", width="stretch"):
            ss.t = 0.0
            ss.playing = False
            st.rerun()

    with st.spinner("Simulating FCFS, SCAN and MC-DDI…"):
        replays = {a: get_replay(config_text, scenario, seed, a) for a in ALGO_ORDER}
    shown = replays if mode == "Compare all" else {algorithm: replays[algorithm]}
    ss["_jump_targets"] = notable_moments(next(iter(shown.values())))
    with st.sidebar:
        st.markdown("### ⤵ Jump to")
        st.selectbox("Notable moment", ["—"] + list(ss["_jump_targets"]), key="jump",
                     on_change=_jump, label_visibility="collapsed")
        st.markdown("### Legend")
        st.html(status_legend())
        st.caption(f"Config: `{config_path.name}` · frames every {SAMPLE_DT} s · dispatch is simulated headlessly, then replayed.")
    end = max(r.end_time for r in replays.values())  # same range in every view keeps the scrubber stable
    if ss.t > end:
        ss.t = end
        ss.playing = False

    # ------------------------------------------------------------------ header
    head_l, head_r = st.columns([3, 2], vertical_alignment="bottom")
    head_l.title("Smart Elevator Dispatch")
    subtitle = "all three algorithms side by side" if mode == "Compare all" else f"{ALGO_LABEL[algorithm]} dispatcher"
    head_l.caption(f"{SCENARIOS[scenario]} · seed {seed} · {subtitle}")
    status = "Playing" if ss.playing else "Paused"
    head_r.html(
        f'<div style="text-align:right;font-family:Inter,system-ui;font-size:13px;color:#6b7280">'
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;font-weight:700;'
        f'background:{"#e9f8e9" if ss.playing else "#f1f5f9"};color:{"#0a7f0a" if ss.playing else "#475569"}">'
        f'{"●" if ss.playing else "❚❚"} {status} · {speed}×</span></div>'
    )

    # ------------------------------------------------------------------ live view (fragment = smooth playback)
    @st.fragment(run_every=TICK_SEC if ss.playing else None)
    def live_view() -> None:
        if ss.playing:
            nxt = round(ss.t + speed * TICK_SEC, 2)
            if nxt >= end:
                ss.t = end
                ss.playing = False
                st.rerun()
            ss.t = nxt
        st.slider("Timeline", 0.0, float(end), key="t", step=SAMPLE_DT, format="%.2f s")
        t = ss.t

        if mode == "Compare all":
            cols = st.columns(len(shown))
            for col, (algo, rep) in zip(cols, shown.items()):
                with col:
                    with st.container(border=True):
                        st.html(render_building(rep.frame_at(t), rep, compact=True, title=ALGO_LABEL[algo]))
                        st.html(render_kpi_strip(rep.kpis_at(t)))
            rep0 = next(iter(shown.values()))
            st.markdown("##### Active alerts")
            st.html(render_events(active_alerts(rep0, t)[-6:]))
        else:
            rep = shown[algorithm]
            left, right = st.columns([2.3, 1], gap="medium")
            with left:
                with st.container(border=True):
                    st.html(render_building(rep.frame_at(t), rep))
            with right:
                kpi_metrics(rep, t)
                st.markdown("##### Active alerts")
                st.html(render_events(active_alerts(rep, t)))
                st.markdown("##### Recent activity")
                recent = [e for e in rep.events_between(t - 60.0, t) if e.kind not in ("FAULT_END",)][-8:]
                st.html(render_events(reversed(recent), empty_text="Waiting for calls…"))

    live_view()

    # ------------------------------------------------------------------ final comparison
    st.divider()
    st.subheader("Algorithm comparison")
    st.caption("Final KPIs for the same request stream (lower is better). Bars are labelled; a table view follows.")
    summaries = {a: replays[a].summary for a in ALGO_ORDER}
    st.altair_chart(kpi_comparison_chart(summaries), width="content")
    st.altair_chart(journey_over_time_chart(replays), width="stretch")
    table = pd.DataFrame(
        [
            {
                "Algorithm": ALGO_LABEL[a],
                "Avg journey (s)": s["avg_journey_time_sec"],
                "Avg hall wait (s)": s["avg_hall_wait_time_sec"],
                "Max hall wait (s)": s["max_hall_wait_time_sec"],
                "Total stops": s["total_elevator_stops"],
                "Overload bumps": s["overload_events"],
                "Delivered": f"{s['completed_requests']}/{s['total_requests']}",
                "Dispatch latency (ms)": s["avg_dispatch_latency_ms"],
            }
            for a, s in summaries.items()
        ]
    )
    st.dataframe(table, hide_index=True, width="stretch")
    best = min(summaries, key=lambda a: summaries[a]["avg_journey_time_sec"])
    st.caption(
        f"Lowest average journey time: **{ALGO_LABEL[best]}**. "
        + " · ".join(f"<span style='color:{ALGO_COLORS[a]}'>■</span> {ALGO_LABEL[a]}" for a in ALGO_ORDER),
        unsafe_allow_html=True,
    )


main()
