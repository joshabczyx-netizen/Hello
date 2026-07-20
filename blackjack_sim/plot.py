"""Minimal dependency-free SVG line chart for bankroll trajectories."""

from __future__ import annotations

from typing import List

from .tournament import TournamentResult

_PALETTE = ["#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
            "#42d4f4", "#f032e6", "#bfff00"]


def _downsample(series: List[float], target: int) -> List[float]:
    if len(series) <= target:
        return series
    step = len(series) / target
    return [series[int(i * step)] for i in range(target)]


def render_svg(result: TournamentResult, width: int = 900, height: int = 480) -> str:
    pad_l, pad_r, pad_t, pad_b = 70, 170, 40, 45
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    series = {r.name: _downsample(r.trajectory, plot_w) for r in result.results}
    all_vals = [v for s in series.values() for v in s]
    y_min, y_max = min(all_vals), max(all_vals)
    if y_max == y_min:
        y_max += 1.0
    n = max(len(s) for s in series.values())

    def x(i: int) -> float:
        return pad_l + (i / max(n - 1, 1)) * plot_w

    def y(v: float) -> float:
        return pad_t + (1 - (v - y_min) / (y_max - y_min)) * plot_h

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="system-ui,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#0d1117"/>',
        f'<text x="{width/2}" y="24" fill="#e6edf3" font-size="16" '
        f'text-anchor="middle">Blackjack Bankroll Trajectories '
        f'({result.rounds} rounds)</text>',
    ]

    # Gridlines + y labels.
    for k in range(5):
        val = y_min + (y_max - y_min) * k / 4
        yy = y(val)
        parts.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l+plot_w}" '
            f'y2="{yy:.1f}" stroke="#21262d"/>'
        )
        parts.append(
            f'<text x="{pad_l-8}" y="{yy+4:.1f}" fill="#8b949e" '
            f'font-size="11" text-anchor="end">{val:,.0f}</text>'
        )

    # Starting-bankroll reference line.
    start = result.results[0].start_bankroll
    if y_min <= start <= y_max:
        ys = y(start)
        parts.append(
            f'<line x1="{pad_l}" y1="{ys:.1f}" x2="{pad_l+plot_w}" '
            f'y2="{ys:.1f}" stroke="#484f58" stroke-dasharray="4 4"/>'
        )

    # Trajectories + legend.
    for idx, (name, s) in enumerate(series.items()):
        color = _PALETTE[idx % len(_PALETTE)]
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s))
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.8" opacity="0.9"/>'
        )
        ly = pad_t + 10 + idx * 22
        final = result.results[idx].final_bankroll
        parts.append(
            f'<rect x="{pad_l+plot_w+16}" y="{ly-9}" width="12" height="12" '
            f'fill="{color}"/>'
        )
        parts.append(
            f'<text x="{pad_l+plot_w+34}" y="{ly+1}" fill="#e6edf3" '
            f'font-size="12">{name}: {final:,.0f}</text>'
        )

    parts.append(
        f'<text x="{pad_l+plot_w/2}" y="{height-12}" fill="#8b949e" '
        f'font-size="12" text-anchor="middle">rounds played</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def save_svg(result: TournamentResult, path: str) -> None:
    with open(path, "w") as fh:
        fh.write(render_svg(result))
