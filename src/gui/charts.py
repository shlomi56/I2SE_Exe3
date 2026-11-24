"""Altair charts for the dashboard.

Encoding rules followed (dataviz skill): algorithm identity uses fixed categorical slots 1-3
(FCFS blue, SCAN orange, MC_DDI aqua) that never change with filters; metrics of different scale
are small multiples (never a dual axis); thin bars with 4px rounded ends, direct value labels,
hover tooltips; a single legend for >= 2 series; recessive grid/axes.
"""
from __future__ import annotations

from typing import Dict, List

import altair as alt
import pandas as pd

from src.gui.replay import Replay

ALGO_ORDER = ["FCFS", "SCAN", "MC_DDI"]
ALGO_COLORS = {"FCFS": "#2a78d6", "SCAN": "#eb6834", "MC_DDI": "#1baf7a"}
TEXT_PRIMARY = "#111827"
TEXT_SECONDARY = "#52514e"
GRID = "#eceef2"
FONT = "Inter, 'Source Sans Pro', 'Segoe UI', system-ui, -apple-system, sans-serif"

KPI_METRICS = [
    ("avg_journey_time_sec", "Avg journey time (s)"),
    ("avg_hall_wait_time_sec", "Avg hall wait (s)"),
    ("max_hall_wait_time_sec", "Max hall wait (s)"),
    ("total_elevator_stops", "Total stops"),
]


def _color_scale(algos: List[str]) -> alt.Scale:
    present = [a for a in ALGO_ORDER if a in algos]
    return alt.Scale(domain=present, range=[ALGO_COLORS[a] for a in present])


def summary_frame(summaries: Dict[str, dict]) -> pd.DataFrame:
    rows = []
    for algo in ALGO_ORDER:
        if algo not in summaries:
            continue
        s = summaries[algo]
        for key, label in KPI_METRICS:
            rows.append({"algorithm": algo, "metric": label, "value": float(s[key])})
    return pd.DataFrame(rows)


def _style(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
    return (
        chart.configure(font=FONT)
        .configure_view(stroke=None)
        .configure_axis(gridColor=GRID, domainColor="#d1d5db", tickColor="#d1d5db", labelColor=TEXT_SECONDARY,
                        titleColor=TEXT_SECONDARY, labelFont=FONT, titleFont=FONT)
        .configure_legend(labelColor=TEXT_PRIMARY, titleColor=TEXT_SECONDARY, orient="top", labelFont=FONT)
        .configure_header(labelColor=TEXT_PRIMARY, labelFontWeight="bold", labelFontSize=12, titleColor=TEXT_SECONDARY)
    )


def kpi_comparison_chart(summaries: Dict[str, dict]) -> alt.TopLevelMixin:
    """One small bar chart per KPI (independent y scales), algorithms in fixed color order."""
    df = summary_frame(summaries)
    algos = list(df["algorithm"].unique())
    base = alt.Chart(df).encode(
        x=alt.X("algorithm:N", sort=ALGO_ORDER, title=None, axis=alt.Axis(labelAngle=0, ticks=False)),
        y=alt.Y("value:Q", title=None, axis=alt.Axis(tickCount=4, grid=True)),
    )
    bars = base.mark_bar(size=26, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        color=alt.Color("algorithm:N", scale=_color_scale(algos), legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip("algorithm:N", title="Algorithm"), alt.Tooltip("metric:N", title="Metric"),
                 alt.Tooltip("value:Q", title="Value", format=",.2f")],
    )
    labels = base.mark_text(dy=-7, fontSize=11, fontWeight="bold", color=TEXT_PRIMARY).encode(
        text=alt.Text("value:Q", format=",.4~f")
    )
    chart = (
        alt.layer(bars, labels, data=df)
        .properties(width=150, height=190)
        .facet(column=alt.Column("metric:N", sort=[m for _, m in KPI_METRICS], title=None))
        .resolve_scale(y="independent")
    )
    return _style(chart)


def journey_over_time_chart(replays: Dict[str, Replay], step_sec: float = 60.0) -> alt.TopLevelMixin:
    """Running average journey time vs. simulation time, one 2px line per algorithm with a hover rule."""
    rows = []
    for algo in ALGO_ORDER:
        if algo in replays:
            for t, v in replays[algo].journey_curve(step_sec):
                rows.append({"minute": t / 60.0, "algorithm": algo, "avg_journey": v})
    df = pd.DataFrame(rows)
    algos = list(df["algorithm"].unique()) if not df.empty else []
    color = alt.Color("algorithm:N", scale=_color_scale(algos), legend=alt.Legend(title=None))
    base = alt.Chart(df).encode(
        x=alt.X("minute:Q", title="Simulation time (min)", axis=alt.Axis(tickCount=8)),
        y=alt.Y("avg_journey:Q", title="Running avg journey (s)", scale=alt.Scale(zero=False)),
    )
    lines = base.mark_line(strokeWidth=2).encode(color=color)
    hover = alt.selection_point(fields=["minute"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    rule = (
        alt.Chart(df)
        .mark_rule(color="#9ca3af", strokeWidth=1)
        .encode(
            x="minute:Q",
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("minute:Q", title="Minute", format=".0f")]
            + [alt.Tooltip(f"{a}:Q", title=a, format=".2f") for a in algos],
        )
        .transform_pivot("algorithm", value="avg_journey", groupby=["minute"])
        .add_params(hover)
    )
    points = base.mark_point(size=60, filled=True, stroke="#ffffff", strokeWidth=2).encode(
        color=color, opacity=alt.condition(hover, alt.value(1), alt.value(0))
    ).transform_filter(hover)
    last = df.sort_values("minute").groupby("algorithm").tail(1).copy() if not df.empty else df.copy()
    if not last.empty:  # nudge end labels apart so close lines never get overlapping names
        span = max(df["avg_journey"].max() - df["avg_journey"].min(), 1e-6)
        gap = span * 0.13  # ~1 text line at the rendered plot height
        last = last.sort_values("avg_journey")
        ys = list(last["avg_journey"])
        for i in range(1, len(ys)):
            ys[i] = max(ys[i], ys[i - 1] + gap)
        last["label_y"] = ys
    end_labels = (
        alt.Chart(last)
        .mark_text(align="left", dx=6, fontSize=11, fontWeight="bold", color=TEXT_PRIMARY)
        .encode(x="minute:Q", y="label_y:Q", text="algorithm:N")
    )
    chart = alt.layer(lines, rule, points, end_labels).properties(height=260)
    return _style(chart)
