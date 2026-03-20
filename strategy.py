from typing import Dict, Optional

from config import ADX_MIN, EMA_SLOPE_MIN_PCT

IDLE = "IDLE"
BREAKOUT = "BREAKOUT"
PULLBACK = "PULLBACK"

STATE_TIMEOUT_BARS = 5


def _build_breakout_state(direction: str, bar_index: int, breakout_price: float) -> Dict[str, float | int | str]:
    state: Dict[str, float | int | str] = {
        "phase": BREAKOUT,
        "direction": direction,
        "basis_bar": bar_index,
        "rise_streak": 1,
    }
    if direction == "long":
        state["breakout_high"] = breakout_price
        state["last_low"] = 0.0
    else:
        state["breakout_low"] = breakout_price
        state["last_high"] = 0.0
    return state


def decide_entry_signal(
    symbol_upper: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    ema: Optional[float],
    ema_5: Optional[float],
    atr: Optional[float],
    rsi: Optional[float],
    prev_rsi: Optional[float],
    adx: Optional[float],
    state: Optional[Dict[str, float | int | str]],
    bar_index: int,
    prev_close: Optional[float],
) -> tuple[Optional[Dict[str, float | str]], Optional[Dict[str, float | int | str]], Optional[str]]:
    _ = symbol_upper
    if ema is None or ema_5 is None or atr is None or adx is None or rsi is None or prev_rsi is None:
        return None, state, None
    if adx < ADX_MIN:
        return None, state, None

    long_basis = close_price > ema and ema > (ema_5 * (1.0 + EMA_SLOPE_MIN_PCT)) and prev_rsi < 50.0 <= rsi
    short_basis = close_price < ema and ema < (ema_5 * (1.0 - EMA_SLOPE_MIN_PCT)) and prev_rsi > 50.0 >= rsi

    phase = str(state.get("phase", IDLE)) if state else IDLE
    if phase != IDLE and state:
        basis_bar = int(state.get("basis_bar", bar_index))
        direction = str(state.get("direction", ""))
        timed_out = (bar_index - basis_bar) > STATE_TIMEOUT_BARS
        ema_invalid = (direction == "long" and close_price <= ema) or (direction == "short" and close_price >= ema)
        opposite_signal = (direction == "long" and short_basis) or (direction == "short" and long_basis)
        if timed_out or ema_invalid or opposite_signal:
            state = None
            phase = IDLE
            if opposite_signal:
                # Opposite basis should be processed in the same bar.
                pass
            else:
                return None, None, "STATE_RESET"

    if phase == IDLE:
        if long_basis:
            next_state = _build_breakout_state("long", bar_index, high_price)
            next_state["last_low"] = low_price
            return None, next_state, "BREAKOUT"
        if short_basis:
            next_state = _build_breakout_state("short", bar_index, low_price)
            next_state["last_high"] = high_price
            return None, next_state, "BREAKOUT"
        return None, None, None

    if not state:
        return None, None, None

    direction = str(state.get("direction", ""))
    phase = str(state.get("phase", IDLE))

    if direction == "long":
        breakout_high = float(state.get("breakout_high", 0.0))
        if phase == BREAKOUT:
            pullback = (
                low_price <= (breakout_high + (atr * 0.5))
                and close_price > ema
                and (close_price < open_price or (prev_close is not None and close_price <= prev_close))
            )
            if pullback:
                state["phase"] = PULLBACK
                return None, state, "PULLBACK"

            rise_streak = int(state.get("rise_streak", 1))
            last_low = float(state.get("last_low", low_price))
            if prev_close is not None and close_price > prev_close and low_price > last_low:
                rise_streak += 1
            else:
                rise_streak = 1
            state["rise_streak"] = rise_streak
            state["last_low"] = low_price

            if rise_streak == 3 and close_price >= (breakout_high * 1.005) and rsi < 70.0:
                return (
                    {
                        "direction": "long",
                        "reason": "CHASE_LONG",
                        "atr_used": f"{atr}",
                    },
                    None,
                    "ENTRY_CHASE",
                )
            return None, state, None

        if phase == PULLBACK and close_price > breakout_high:
            return (
                {
                    "direction": "long",
                    "reason": "REBREAK_LONG",
                    "atr_used": f"{atr}",
                },
                None,
                "ENTRY_REBREAK",
            )
        return None, state, None

    breakout_low = float(state.get("breakout_low", 0.0))
    if phase == BREAKOUT:
        pullback = (
            high_price >= (breakout_low - (atr * 0.5))
            and close_price < ema
            and (close_price > open_price or (prev_close is not None and close_price >= prev_close))
        )
        if pullback:
            state["phase"] = PULLBACK
            return None, state, "PULLBACK"

        fall_streak = int(state.get("rise_streak", 1))
        last_high = float(state.get("last_high", high_price))
        if prev_close is not None and close_price < prev_close and high_price < last_high:
            fall_streak += 1
        else:
            fall_streak = 1
        state["rise_streak"] = fall_streak
        state["last_high"] = high_price

        if fall_streak == 3 and close_price <= (breakout_low * 0.995) and rsi > 30.0:
            return (
                {
                    "direction": "short",
                    "reason": "CHASE_SHORT",
                    "atr_used": f"{atr}",
                },
                None,
                "ENTRY_CHASE",
            )
        return None, state, None

    if phase == PULLBACK and close_price < breakout_low:
        return (
            {
                "direction": "short",
                "reason": "REBREAK_SHORT",
                "atr_used": f"{atr}",
            },
            None,
            "ENTRY_REBREAK",
        )
    return None, state, None

