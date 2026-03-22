"""
BB 스퀴즈 + 밴드 돌파 + RSI 기울기 (15분봉) + ATR(14) Wilder.
"""

from __future__ import annotations

import math
from typing import List, Literal, Optional, Tuple

Direction = Literal["long", "short"]


def _sma(values: List[float]) -> float:
    return sum(values) / len(values)


def _std_pop(values: List[float], mean: float) -> float:
    v = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(v)


def bollinger_at(closes: List[float], i: int, period: int, num_std: float) -> Optional[Tuple[float, float, float, float]]:
    if i < period - 1 or len(closes) <= i:
        return None
    seg = closes[i - period + 1 : i + 1]
    if len(seg) != period:
        return None
    mid = _sma(seg)
    sd = _std_pop(seg, mid)
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    width = upper - lower
    return (mid, upper, lower, width)


def compute_rsi_wilder(closes: List[float], period: int = 14) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < 2:
        return out
    avg_gain: Optional[float] = None
    avg_loss: Optional[float] = None
    rsi_count = 0
    gain_sum = 0.0
    loss_sum = 0.0
    for i in range(1, n):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        if avg_gain is None or avg_loss is None:
            rsi_count += 1
            gain_sum += gain
            loss_sum += loss
            if rsi_count >= period:
                avg_gain = gain_sum / period
                avg_loss = loss_sum / period
        else:
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
        if avg_gain is not None and avg_loss is not None:
            if avg_loss == 0:
                out[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def compute_atr_wilder(
    highs: List[float], lows: List[float], closes: List[float], period: int
) -> List[Optional[float]]:
    """Wilder ATR. out[i] = ATR at bar i."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < period:
        return out
    tr: List[float] = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        pc = closes[i - 1]
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - pc),
            abs(lows[i] - pc),
        )
    # 첫 ATR = 첫 period개 TR 단순평균 (인덱스 0..period-1)
    s = sum(tr[0:period])
    atr = s / float(period)
    out[period - 1] = atr
    for i in range(period, n):
        atr = (atr * (period - 1) + tr[i]) / float(period)
        out[i] = atr
    return out


def evaluate_entry_signal(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    bb_period: int,
    bb_std: float,
    squeeze_lookback: int,
    squeeze_max_mult: float,
    rsi_period: int,
    rsi_slope_bars: int,
    atr_period: int,
) -> Optional[Tuple[Direction, float, float, float]]:
    """
    마지막 확정봉 기준.
    Returns: (direction, breakout_high, breakout_low, atr_at_signal) or None.
    """
    n = len(closes)
    need = max(bb_period + squeeze_lookback, rsi_period + rsi_slope_bars + 5, atr_period + 5)
    if n < need:
        return None

    cur = n - 1

    widths: List[float] = []
    for i in range(cur - squeeze_lookback + 1, cur + 1):
        if i < bb_period - 1:
            continue
        bb = bollinger_at(closes, i, bb_period, bb_std)
        if bb is None:
            return None
        widths.append(bb[3])

    if len(widths) < squeeze_lookback:
        return None

    min_w = min(widths)
    cur_bb = bollinger_at(closes, cur, bb_period, bb_std)
    if cur_bb is None:
        return None
    _, upper, lower, cur_w = cur_bb

    if min_w <= 0:
        return None
    if cur_w > squeeze_max_mult * min_w:
        return None

    c = closes[cur]
    hi = highs[cur]
    lo = lows[cur]

    if c > upper:
        direction: Direction = "long"
    elif c < lower:
        direction = "short"
    else:
        return None

    rsi_arr = compute_rsi_wilder(closes, rsi_period)
    r_now = rsi_arr[cur]
    back = cur - rsi_slope_bars
    r_back = rsi_arr[back] if back >= 0 else None
    if r_now is None or r_back is None:
        return None

    slope = (r_now - r_back) / float(rsi_slope_bars)
    if direction == "long":
        if slope <= 0:
            return None
    else:
        if slope >= 0:
            return None

    atr_arr = compute_atr_wilder(highs, lows, closes, atr_period)
    atr_sig = atr_arr[cur]
    if atr_sig is None or atr_sig <= 0:
        return None

    return (direction, float(hi), float(lo), float(atr_sig))


def initial_sl_price(
    direction: Direction,
    breakout_high: float,
    breakout_low: float,
    atr: float,
    atr_mult: float,
) -> float:
    """LONG: 저가 - ATR*mult, SHORT: 고가 + ATR*mult"""
    if direction == "long":
        return breakout_low - atr_mult * atr
    return breakout_high + atr_mult * atr


def trail_candidate_sl(
    direction: Direction,
    bar_high: float,
    bar_low: float,
    atr: float,
    atr_mult: float,
) -> float:
    """15분 봉 마감 시 트레일 후보 SL 가격."""
    if direction == "long":
        return bar_low - atr_mult * atr
    return bar_high + atr_mult * atr


def loss_pct_vs_entry(direction: Direction, entry: float, sl: float) -> float:
    """진입가 대비 SL까지 손실 비율 (양수)."""
    if entry <= 0:
        return 1.0
    if direction == "long":
        return max(0.0, (entry - sl) / entry)
    return max(0.0, (sl - entry) / entry)
