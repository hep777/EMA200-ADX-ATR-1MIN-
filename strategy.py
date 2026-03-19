from typing import Optional

from config import (
    BODY_MOVE_PCT,
    TAIL_MIN_BODY_PCT,
    TAIL_RATIO,
    RSI_EXTREME_SHORT,
    RSI_EXTREME_LONG,
    RSI_LONG,
    RSI_SHORT,
    RSI_PERIOD,
)


def decide_entry_direction(
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    rsi: Optional[float],
) -> Optional[str]:
    """
    Returns: "long" / "short" / None

    Priority:
      1) RSI extreme: RSI >= 90 => short, RSI <= 12 => long
      2) Tail exception: tail/body >= 0.7 (only if body% >= 0.2%)
      3) Default: body +/-1.5% + RSI 70/32
    """
    if rsi is None:
        return None

    # 1) RSI extreme exception
    if rsi >= RSI_EXTREME_SHORT:
        return "short"
    if rsi <= RSI_EXTREME_LONG:
        return "long"

    # body metrics (use open as denominator for %)
    if open_price == 0:
        return None

    body = abs(close_price - open_price)
    if body == 0:
        body_pct = 0.0
    else:
        body_pct = body / open_price

    # 2) Tail exception
    if body_pct >= TAIL_MIN_BODY_PCT and body > 0:
        upper_tail = high_price - max(open_price, close_price)
        lower_tail = min(open_price, close_price) - low_price

        upper_ok = (upper_tail / body) >= TAIL_RATIO
        lower_ok = (lower_tail / body) >= TAIL_RATIO

        if upper_ok or lower_ok:
            # If both somehow happen, choose the larger tail.
            if upper_tail >= lower_tail:
                return "short"
            return "long"

    # 3) Default condition
    body_move_pct = (close_price - open_price) / open_price
    if body_move_pct >= BODY_MOVE_PCT and rsi >= RSI_LONG:
        return "long"
    if body_move_pct <= -BODY_MOVE_PCT and rsi <= RSI_SHORT:
        return "short"

    return None

