"""Pure HTML/CSS rendering of the building view (no Streamlit import; unit-testable).

Car status colors use the reserved status palette (fault = critical, overload = serious,
door obstruction = warning, doors open = good); every status also carries a text label + glyph so
color is never the only cue.
"""
from __future__ import annotations

from html import escape
from typing import Iterable, List, Optional

from src.gui.replay import (
    STATUS_DOORS_OPEN,
    STATUS_FAULT,
    STATUS_IDLE,
    STATUS_MOVING,
    STATUS_OVERLOADED,
    CarFrame,
    Frame,
    Replay,
    SimEvent,
)

STATUS_STYLE = {
    # status: (label, glyph, accent, tint)
    STATUS_MOVING: ("Moving", "↕", "#4f46e5", "#eef0ff"),
    STATUS_DOORS_OPEN: ("Doors open", "⇔", "#0ca30c", "#e9f8e9"),
    STATUS_OVERLOADED: ("Overloaded", "⚠", "#ec835a", "#fdf0ea"),
    STATUS_FAULT: ("Fault", "⛔", "#d03b3b", "#fbeaea"),
    STATUS_IDLE: ("Idle", "•", "#64748b", "#f1f5f9"),
}
WARNING = "#fab219"
ARROWS = {"UP": "▲", "DOWN": "▼", "IDLE": ""}

CSS = """
<style>
.eb { font-family: "Inter", "Segoe UI", system-ui, sans-serif; color: #111827; }
.eb * { box-sizing: border-box; }
.eb-grid { display: grid; gap: 8px; align-items: stretch; }
.eb-colh { font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
           color: #6b7280; padding: 6px 4px; align-self: end; }
.eb-card { background: #fff; border: 1px solid #e5e7eb; border-top: 4px solid var(--accent);
           border-radius: 12px; padding: 8px 9px; font-size: 12px; box-shadow: 0 1px 2px rgba(16,24,40,.05); }
.eb-card .top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.eb-card .cid { font-weight: 800; font-size: 15px; }
.eb-pill { display: inline-flex; gap: 4px; align-items: center; font-weight: 700; font-size: 11px;
           padding: 2px 8px; border-radius: 999px; color: var(--accent); background: var(--tint); white-space: nowrap; }
.eb-meter { display: grid; grid-template-columns: 1fr; gap: 2px; margin-top: 4px; color: #374151; }
.eb-meter .lbl { display: flex; justify-content: space-between; font-size: 11px; }
.eb-bar { height: 6px; background: #eef0f4; border-radius: 99px; overflow: hidden; }
.eb-bar i { display: block; height: 100%; border-radius: 99px; }
.eb-next { margin-top: 6px; font-size: 11px; color: #6b7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eb-flags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.eb-flag { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 6px; }
.eb-flag.warn { background: #fff4d6; color: #8a5a00; border: 1px solid #fab219; }
.eb-flag.crit { background: #fbeaea; color: #a32626; border: 1px solid #d03b3b; }
.eb-flag.serious { background: #fdf0ea; color: #9a4421; border: 1px solid #ec835a; }
.eb-col { position: relative; border-radius: 10px; background-color: #fff; border: 1px solid #e5e7eb; overflow: hidden; }
.eb-floors { background: transparent; border: none; }
.eb-flabel { position: absolute; left: 0; right: 0; display: flex; align-items: center; justify-content: flex-end;
             padding-right: 8px; font-size: 12px; font-weight: 600; color: #6b7280; }
.eb-flabel.lobby { color: #4f46e5; font-weight: 800; }
.eb-wrow { position: absolute; left: 6px; right: 6px; display: flex; gap: 4px; align-items: center; overflow: hidden; }
.eb-chip { font-size: 10.5px; font-weight: 700; padding: 1px 6px; border-radius: 6px; background: #f3f4f6;
           color: #374151; white-space: nowrap; border: 1px solid #e5e7eb; }
.eb-chip.up { background: #eef0ff; border-color: #c7cbff; color: #3730a3; }
.eb-chip.down { background: #fff1e8; border-color: #ffd3b8; color: #9a3412; }
.eb-chip.more { background: #fff; color: #6b7280; }
.eb-shaft { background-color: #fbfbfd; }
.eb-shaft .rail { position: absolute; top: 0; bottom: 0; left: 50%; width: 2px; margin-left: -1px; background: #e5e7eb; }
.eb-dot { position: absolute; left: 50%; width: 8px; height: 8px; margin-left: -4px; border-radius: 99px;
          background: #fff; border: 2px solid var(--accent); }
.eb-car { position: absolute; left: 8%; right: 8%; border-radius: 8px; background: var(--accent); color: #fff;
          display: flex; align-items: center; justify-content: center; gap: 5px; font-weight: 800; font-size: 12px;
          box-shadow: 0 2px 6px rgba(17,24,39,.18); border: 2px solid rgba(255,255,255,.9); }
.eb-car.obstructed { outline: 3px solid #fab219; outline-offset: 1px; }
.eb-car .pax { background: rgba(255,255,255,.25); border-radius: 6px; padding: 0 5px; font-size: 11px; }
.eb.compact .eb-grid { gap: 4px; }
.eb.compact .eb-flabel { padding-right: 4px; font-size: 10.5px; }
.eb.compact .eb-colh { font-size: 10px; padding: 4px 2px; }
.eb-card.compact { padding: 6px 6px; font-size: 11px; border-radius: 10px; min-width: 0; }
.eb-card.compact .cid { font-size: 13px; }
.eb-card.compact .eb-pill { padding: 1px 6px; }
.eb-card.compact .eb-meter .lbl { font-size: 10px; }
.eb-card.compact .eb-meter .lbl span { color: #9ca3af; }
.eb-card.compact .eb-flag { font-size: 9px; padding: 0 4px; }
.eb-chip { min-width: 0; }
.eb-title { font-weight: 800; font-size: 15px; margin: 0 0 6px 2px; }
.eb-legend { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
</style>
"""


def _bar_color(ratio: float) -> str:
    if ratio >= 0.9:
        return "#d03b3b"
    if ratio >= 0.7:
        return "#fab219"
    return "#0ca30c"


def _style_vars(status: str) -> str:
    _, _, accent, tint = STATUS_STYLE[status]
    return f"--accent:{accent};--tint:{tint};"


def status_pill(status: str, direction: str = "IDLE") -> str:
    label, glyph, _, _ = STATUS_STYLE[status]
    arrow = f" {ARROWS[direction]}" if status == STATUS_MOVING and ARROWS.get(direction) else ""
    return f'<span class="eb-pill" style="{_style_vars(status)}">{glyph} {label}{arrow}</span>'


def car_card(car: CarFrame, compact: bool = False) -> str:
    pax_ratio = car.passengers / car.capacity if car.capacity else 0.0
    w_ratio = car.weight_kg / car.max_weight_kg if car.max_weight_kg else 0.0
    flags: List[str] = []
    if car.status == STATUS_FAULT:
        flags.append(f'<span class="eb-flag crit">{"VIB" if compact else "VIBRATION"} {car.vibration:g}</span>')
    if car.door_obstructed:
        flags.append(f'<span class="eb-flag warn">{"DOOR" if compact else "DOOR OBSTRUCTED"}</span>')
    if car.at_capacity:
        flags.append(f'<span class="eb-flag serious">{"FULL" if compact else "AT CAPACITY"}</span>')
    pax_bar = f'<div class="eb-bar"><i style="width:{min(pax_ratio, 1) * 100:.0f}%;background:{_bar_color(pax_ratio)}"></i></div>'
    w_bar = f'<div class="eb-bar"><i style="width:{min(w_ratio, 1) * 100:.0f}%;background:{_bar_color(w_ratio)}"></i></div>'
    label, glyph, _, _ = STATUS_STYLE[car.status]
    if compact:
        arrow = ARROWS.get(car.direction, "") if car.status == STATUS_MOVING else ""
        return (
            f'<div class="eb-card compact" style="{_style_vars(car.status)}" title="{label}">'
            f'<div class="top"><span class="cid">E{car.elevator_id}</span>'
            f'<span class="eb-pill" style="{_style_vars(car.status)}">{glyph}{arrow}</span></div>'
            f'<div class="eb-meter"><div class="lbl"><span>pax</span><b>{car.passengers}/{car.capacity}</b></div>{pax_bar}</div>'
            f'<div class="eb-meter"><div class="lbl"><span>kg</span><b>{car.weight_kg:.0f}</b></div>{w_bar}</div>'
            + (f'<div class="eb-flags">{"".join(flags)}</div>' if flags else "")
            + "</div>"
        )
    nxt = " · ".join(str(f) for f in car.stop_queue[:6]) or "—"
    if len(car.stop_queue) > 6:
        nxt += " …"
    return (
        f'<div class="eb-card" style="{_style_vars(car.status)}">'
        f'<div class="top"><span class="cid">E{car.elevator_id}</span>{status_pill(car.status, car.direction)}</div>'
        f'<div class="eb-meter"><div class="lbl"><span>Passengers</span><b>{car.passengers}/{car.capacity}</b></div>{pax_bar}</div>'
        f'<div class="eb-meter"><div class="lbl"><span>Load</span><b>{car.weight_kg:.0f}/{car.max_weight_kg:.0f} kg</b></div>{w_bar}</div>'
        f'<div class="eb-next">Next stops: {nxt}</div>'
        + (f'<div class="eb-flags">{"".join(flags)}</div>' if flags else "")
        + "</div>"
    )


def render_building(frame: Frame, replay: Replay, compact: bool = False, title: Optional[str] = None) -> str:
    """HTML for floor labels, waiting queues and one shaft per car at ``frame.time``."""
    floors = list(range(replay.max_floor, replay.min_floor - 1, -1))
    row = 26 if compact else 34
    height = row * len(floors)
    n = len(frame.cars)
    if compact:
        cols = f"30px minmax(0,0.8fr) repeat({n}, minmax(0,1fr))"
    else:
        cols = f"46px minmax(130px,1.25fr) repeat({n}, minmax(120px,1fr))"
    stripes = (
        f"background-image: repeating-linear-gradient(to bottom, transparent 0, transparent {row - 1}px, "
        f"#eef0f5 {row - 1}px, #eef0f5 {row}px);"
    )

    def top_of(position: float) -> float:
        return (replay.max_floor - position) * row

    head = ['<div></div>', f'<div class="eb-colh">{"Waiting" if compact else "Waiting · destination"}</div>']
    head += [car_card(c, compact) for c in frame.cars]

    labels = []
    for f in floors:
        cls = "eb-flabel lobby" if f == replay.lobby_floor else "eb-flabel"
        name = f"L {f}" if f == replay.lobby_floor else (f"B{-f}" if f < 0 else str(f))
        labels.append(f'<div class="{cls}" style="top:{top_of(f)}px;height:{row}px">{name}</div>')

    wait_rows = []
    max_chips = 4
    for f in floors:
        group = frame.waiting.get(f, ())
        if not group:
            continue
        chips = []
        if compact:  # narrow column: one count chip per direction
            ups = sum(1 for _, target, _ in group if target > f)
            downs = len(group) - ups
            if ups:
                chips.append(f'<span class="eb-chip up">▲{ups}</span>')
            if downs:
                chips.append(f'<span class="eb-chip down">▼{downs}</span>')
            wait_rows.append(f'<div class="eb-wrow" style="top:{top_of(f)}px;height:{row}px">{"".join(chips)}</div>')
            continue
        for _, target, eid in group[:max_chips]:
            cls = "up" if target > f else "down"
            arrow = "▲" if target > f else "▼"
            car = f"·E{eid}" if eid is not None and not compact else ""
            chips.append(f'<span class="eb-chip {cls}">{arrow}{target}{car}</span>')
        if len(group) > max_chips:
            chips.append(f'<span class="eb-chip more">+{len(group) - max_chips}</span>')
        wait_rows.append(f'<div class="eb-wrow" style="top:{top_of(f)}px;height:{row}px">{"".join(chips)}</div>')

    shafts = []
    for c in frame.cars:
        dots = "".join(
            f'<div class="eb-dot" style="{_style_vars(c.status)}top:{top_of(f) + row / 2 - 4}px"></div>'
            for f in sorted(set(c.stop_queue))
        )
        arrow = ARROWS.get(c.direction, "")
        car = (
            f'<div class="eb-car{" obstructed" if c.door_obstructed else ""}" '
            f'style="{_style_vars(c.status)}top:{top_of(c.position) + 2}px;height:{row - 4}px" '
            f'title="E{c.elevator_id} {STATUS_STYLE[c.status][0]} at {c.position:.1f}">'
            + ("" if compact else f"E{c.elevator_id}")
            + f'{(" " + arrow) if arrow else ""}<span class="pax">{c.passengers}</span></div>'
        )
        shafts.append(f'<div class="eb-col eb-shaft" style="height:{height}px;{stripes}"><div class="rail"></div>{dots}{car}</div>')

    body = [
        f'<div class="eb-col eb-floors" style="height:{height}px">{"".join(labels)}</div>',
        f'<div class="eb-col" style="height:{height}px;{stripes}">{"".join(wait_rows)}</div>',
        *shafts,
    ]
    title_html = f'<div class="eb-title">{escape(title)}</div>' if title else ""
    return (
        CSS
        + f'<div class="eb{" compact" if compact else ""}">{title_html}'
        + f'<div class="eb-grid" style="grid-template-columns:{cols}">{"".join(head)}</div>'
        + f'<div class="eb-grid" style="grid-template-columns:{cols};margin-top:8px">{"".join(body)}</div>'
        + "</div>"
    )


def status_legend() -> str:
    pills = "".join(status_pill(s) for s in STATUS_STYLE)
    return CSS + f'<div class="eb eb-legend">{pills}<span class="eb-flag warn">DOOR OBSTRUCTED</span>' \
                 f'<span class="eb-flag serious">AT CAPACITY</span></div>'


EVENT_STYLE = {
    "FAULT_START": ("crit", "⛔"),
    "FAULT_END": ("good", "✓"),
    "OVERLOAD": ("serious", "⚠"),
    "REALLOC": ("serious", "↻"),
    "DOOR_OBSTRUCTION": ("warn", "⚠"),
    "ALLOC": ("info", "→"),
}

ALERT_CSS = """
<style>
.al { font-family: "Inter", "Segoe UI", system-ui, sans-serif; display: flex; flex-direction: column; gap: 6px; }
.al-item { display: flex; gap: 8px; align-items: center; font-size: 12.5px; padding: 6px 10px; border-radius: 10px;
           border: 1px solid #e5e7eb; background: #fff; border-left: 4px solid var(--c); color: #111827; }
.al-item .t { color: #6b7280; font-variant-numeric: tabular-nums; min-width: 58px; font-size: 11.5px; }
.al-item .k { font-weight: 800; color: var(--c); min-width: 14px; text-align: center; }
.al-empty { font-size: 12.5px; color: #6b7280; padding: 8px 10px; border: 1px dashed #d1d5db; border-radius: 10px; }
</style>
"""
_EVENT_COLORS = {"crit": "#d03b3b", "serious": "#ec835a", "warn": "#c98500", "good": "#0ca30c", "info": "#4f46e5"}


def render_events(events: Iterable[SimEvent], empty_text: str = "No active alerts") -> str:
    items = []
    for e in events:
        cls, glyph = EVENT_STYLE.get(e.kind, ("info", "•"))
        items.append(
            f'<div class="al-item" style="--c:{_EVENT_COLORS[cls]}"><span class="t">{e.time:7.1f} s</span>'
            f'<span class="k">{glyph}</span><span>{escape(e.detail)}</span></div>'
        )
    if not items:
        items.append(f'<div class="al-empty">{escape(empty_text)}</div>')
    return ALERT_CSS + f'<div class="al">{"".join(items)}</div>'


KPI_CSS = """
<style>
.kp { font-family: "Inter", "Segoe UI", system-ui, sans-serif; display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 8px; }
.kp div { background: #f8f9fc; border: 1px solid #e5e7eb; border-radius: 10px; padding: 6px 8px; }
.kp span { display: block; font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: #6b7280; }
.kp b { font-size: 16px; color: #111827; font-variant-numeric: tabular-nums; }
</style>
"""


def render_kpi_strip(kpis: dict) -> str:
    """Compact KPI row used under each building in comparison mode."""
    cells = [
        ("Delivered", f"{kpis['completed']}/{kpis['total']}"),
        ("Avg journey", f"{kpis['avg_journey']:.1f} s"),
        ("Avg wait", f"{kpis['avg_wait']:.1f} s"),
        ("Max wait", f"{kpis['max_wait']:.1f} s"),
        ("Stops", str(kpis["stops"])),
        ("Overloads", str(kpis["overloads"])),
    ]
    return KPI_CSS + '<div class="kp">' + "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in cells) + "</div>"
