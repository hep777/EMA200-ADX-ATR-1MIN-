from typing import Dict, Optional

from config import (
    ADX_MIN,
    RSI_LONG_MIN,
    RSI_SHORT_MAX,
    STATE_TIMEOUT_BARS,
)

IDLE = "IDLE"
BREAKOUT = "BREAKOUT"


def _build_breakout_state(
    direction: str,
    bar_index: int,
    basis_high: float,
    basis_low: float,
    basis_close: float,
) -> Dict[str, float | int | str]:
    state: Dict[str, float | int | str] = {
        "phase": BREAKOUT,
        "direction": direction,
        "basis_bar": bar_index,
        "basis_close": float(basis_close),
    }
    if direction == "long":
        state["breakout_high"] = float(basis_high)
        state["basis_low"] = float(basis_low)
    else:
        state["breakout_low"] = float(basis_low)
        state["basis_high"] = float(basis_high)
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
    rsi_5: Optional[float],
    adx: Optional[float],
    adx_5: Optional[float],
    state: Optional[Dict[str, float | int | str]],
    bar_index: int,
    prev_close: Optional[float],
) -> tuple[Optional[Dict[str, float | str]], Optional[Dict[str, float | int | str]], Optional[str]]:
    _ = symbol_upper
    _ = open_price
    _ = prev_close
    if (
        ema is None
        or ema_5 is None
        or atr is None
        or rsi is None
        or rsi_5 is None
        or adx is None
        or adx_5 is None
    ):
        return None, state, None

    # 예전 버전 state.json 에 PULLBACK 등이 남아 있으면 무효화
    if state and str(state.get("phase", IDLE)) not in (IDLE, BREAKOUT):
        return None, None, "STATE_RESET"

    # EMA / RSI / ADX: 5봉 전 값보다 큰지·작은지만 본다 (0.1% 최소 기울기 없음)
    long_basis = (
        close_price > ema
        and ema > ema_5
        and rsi >= RSI_LONG_MIN
        and rsi > rsi_5
        and adx >= ADX_MIN
        and adx > adx_5
    )
    short_basis = (
        close_price < ema
        and ema < ema_5
        and rsi <= RSI_SHORT_MAX
        and rsi < rsi_5
        and adx >= ADX_MIN
        and adx > adx_5
    )

    phase = str(state.get("phase", IDLE)) if state else IDLE

    # ── 기존 상태: BREAKOUT (기준봉 이후 진입 대기) ──
    if state and phase == BREAKOUT:
        basis_bar = int(state.get("basis_bar", bar_index))
        direction = str(state.get("direction", ""))
        timed_out = (bar_index - basis_bar) > STATE_TIMEOUT_BARS
        ema_invalid = (direction == "long" and close_price <= ema) or (direction == "short" and close_price >= ema)
        opposite_signal = (direction == "long" and short_basis) or (direction == "short" and long_basis)

        if timed_out or ema_invalid:
            return None, None, "STATE_RESET"

        if opposite_signal:
            state = None
        else:
            if direction == "long":
                breakout_high = float(state.get("breakout_high", 0.0))
                if close_price > breakout_high:
                    return (
                        {
                            "direction": "long",
                            "reason": "ENTRY_LONG",
                            "atr_used": f"{atr}",
                            "basis_low": float(state.get("basis_low", low_price)),
                        },
                        None,
                        "ENTRY_SIGNAL",
                    )
            elif direction == "short":
                breakout_low = float(state.get("breakout_low", 0.0))
                if close_price < breakout_low:
                    return (
                        {
                            "direction": "short",
                            "reason": "ENTRY_SHORT",
                            "atr_used": f"{atr}",
                            "basis_high": float(state.get("basis_high", high_price)),
                        },
                        None,
                        "ENTRY_SIGNAL",
                    )
            return None, state, None

    # ── IDLE: 새 기준봉 (반대 시그널로 state 가 비워진 봉 포함) ──
    if state is None or str(state.get("phase", IDLE)) == IDLE:
        if long_basis:
            next_state = _build_breakout_state("long", bar_index, high_price, low_price, close_price)
            return None, next_state, "BREAKOUT"
        if short_basis:
            next_state = _build_breakout_state("short", bar_index, high_price, low_price, close_price)
            return None, next_state, "BREAKOUT"
        return None, None, None

    # opposite_signal 후 state=None 으로 내려온 경우 위에서 처리됨; 여기는 방어
    return None, state, None
