from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def line_value(slope: float, intercept: float, x: int) -> float:
    return slope * float(x) + intercept


def _touch_tol_price(price: float, touch_tol_pct: float) -> float:
    return abs(float(price)) * touch_tol_pct


def fit_outer_tangent_line(
    xy: List[Tuple[int, float]],
    kind: str,
    min_touches: int,
    touch_tol_pct: float,
) -> Optional[Dict[str, float]]:
    """
    최외곽 접선: 스윙 고점/저점 쌍으로 선을 만들고,
    상승(고점): 나머지 고점이 모두 선 위 또는 아래(선 아래 = 가격이 선 이하), 기울기 양수.
    하락(저점): 나머지 저점이 모두 선 위 또는 아래(선 위 = 가격이 선 이상), 기울기 음수.
    유효한 선 중 터치(가격 대비 touch_tol_pct 이내)가 가장 많은 선을 채택.
    """
    if len(xy) < min_touches:
        return None

    pts = sorted(xy, key=lambda p: p[0])
    n = len(pts)
    # (touch_count, x_span, i, j) — 타이: 터치 수, 그다음 앵커 x 간격, 그다음 (i,j) 사전순
    best: Optional[Tuple[int, int, int, int]] = None

    def is_better(
        tch: int, span: int, ai: int, aj: int, old: Tuple[int, int, int, int]
    ) -> bool:
        ot, os_, oi, oj = old
        if tch != ot:
            return tch > ot
        if span != os_:
            return span > os_
        return (ai, aj) < (oi, oj)

    for i in range(n):
        x1, y1 = pts[i]
        for j in range(i + 1, n):
            x2, y2 = pts[j]
            dx = float(x2 - x1)
            if dx == 0.0:
                continue
            slope = (float(y2) - float(y1)) / dx
            if kind == "up":
                if slope <= 0.0:
                    continue
            else:
                if slope >= 0.0:
                    continue
            intercept = float(y1) - slope * float(x1)

            valid = True
            for k in range(n):
                if k in (i, j):
                    continue
                xk, yk = pts[k]
                lv = line_value(slope, intercept, int(xk))
                tol_line = max(abs(lv) * touch_tol_pct, 1e-12)
                if kind == "up":
                    if float(yk) > lv + tol_line:
                        valid = False
                        break
                else:
                    if float(yk) < lv - tol_line:
                        valid = False
                        break
            if not valid:
                continue

            touches = 0
            for k in range(n):
                xk, yk = pts[k]
                lv = line_value(slope, intercept, int(xk))
                tol_p = _touch_tol_price(float(yk), touch_tol_pct)
                if abs(float(yk) - lv) <= tol_p:
                    touches += 1

            if touches < min_touches:
                continue

            x_span = int(x2 - x1)
            if best is None or is_better(touches, x_span, i, j, best):
                best = cand

    if best is None:
        return None
    touches, _, bi, bj = best
    x1, y1 = pts[bi]
    x2, y2 = pts[bj]
    slope = (float(y2) - float(y1)) / float(x2 - x1)
    intercept = float(y1) - slope * float(x1)
    return {
        "slope": slope,
        "intercept": intercept,
        "touch_count": float(touches),
    }


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
