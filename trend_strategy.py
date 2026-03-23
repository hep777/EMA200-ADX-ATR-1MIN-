from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def line_value(slope: float, intercept: float, x: int) -> float:
    return slope * float(x) + intercept


def fit_regression(points: List[Tuple[int, float]]) -> Optional[Dict[str, float]]:
    if len(points) < 3:
        return None
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    n = float(len(points))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx <= 0:
        return None
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - line_value(slope, intercept, int(x))) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 if ss_tot <= 0 else max(0.0, 1.0 - (ss_res / ss_tot))
    return {"slope": slope, "intercept": intercept, "r2": r2}


def detect_confirmed_swing(
    highs: List[float],
    lows: List[float],
    seqs: List[int],
    left: int,
    right: int,
) -> Optional[Dict[str, float]]:
    n = len(seqs)
    center = n - 1 - right
    if center - left < 0 or center < 0 or center >= n:
        return None

    lo = center - left
    hi = center + right + 1
    hi_seg = highs[lo:hi]
    lo_seg = lows[lo:hi]

    c_hi = highs[center]
    c_lo = lows[center]
    out: Dict[str, float] = {"seq": float(seqs[center])}

    if c_hi == max(hi_seg):
        out["swing_high"] = float(c_hi)
    if c_lo == min(lo_seg):
        out["swing_low"] = float(c_lo)
    if "swing_high" not in out and "swing_low" not in out:
        return None
    return out

