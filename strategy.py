from typing import Dict, Optional

from config import (
    ADX_MIN,
    ATR_MIN_BY_SYMBOL,
    ATR_SPIKE_CAP_MULT,
    CONFIRM_WITHIN_BARS,
    DEFAULT_ATR_MIN,
    EMA_ATR_OFFSET_MULT,
)


def _atr_floor(symbol_upper: str) -> float:
    return ATR_MIN_BY_SYMBOL.get(symbol_upper, DEFAULT_ATR_MIN)


def decide_entry_signal(
    symbol_upper: str,
    close_price: float,
    ema: Optional[float],
    atr: Optional[float],
    atr_ma30: Optional[float],
    adx: Optional[float],
    candidate: Optional[Dict[str, float | int | str]],
    bar_index: int,
) -> tuple[Optional[Dict[str, float | str]], Optional[Dict[str, float | int | str]]]:
    """
    A strategy:
      1) Basis candle: close crosses EMA +/- ATR*k and passes ATR/ADX filters
      2) Confirmation: within N bars after basis, close breaks basis close.
    """
    if ema is None or atr is None or adx is None:
        return None, candidate

    atr_used = atr
    if atr_ma30 is not None:
        atr_used = min(atr, atr_ma30 * ATR_SPIKE_CAP_MULT)

    if atr_used < _atr_floor(symbol_upper):
        return None, candidate
    if adx <= ADX_MIN:
        return None, candidate

    long_basis = close_price >= (ema + atr_used * EMA_ATR_OFFSET_MULT)
    short_basis = close_price <= (ema - atr_used * EMA_ATR_OFFSET_MULT)

    # NOTE:
    # 후보(candiate)가 이미 존재하는 동안에는 basis(close/basis_bar)를 계속 갱신하면
    # "확인 N캔들" 시간이 지나기 전에 기준이 계속 이동해서 주문이 거의 안 나올 수 있습니다.
    # 그래서 basis는 "방향이 바뀔 때만" 갱신합니다.
    # (같은 방향의 long_basis/short_basis가 계속 떠도 basis는 고정)
    existing_dir = str(candidate.get("direction", "")).lower() if candidate else ""
    if long_basis:
        if not candidate or existing_dir != "long":
            candidate = {
                "direction": "long",
                "basis_close": close_price,
                "basis_bar": bar_index,
                "atr_used": atr_used,
            }
    elif short_basis:
        if not candidate or existing_dir != "short":
            candidate = {
                "direction": "short",
                "basis_close": close_price,
                "basis_bar": bar_index,
                "atr_used": atr_used,
            }

    if not candidate:
        return None, None

    basis_bar = int(candidate["basis_bar"])
    if bar_index - basis_bar > CONFIRM_WITHIN_BARS:
        return None, None

    direction = str(candidate["direction"])
    basis_close = float(candidate["basis_close"])

    if direction == "long" and close_price > basis_close:
        return (
            {
                "direction": "long",
                "reason": "A_LONG_CONFIRM",
                "atr_used": float(candidate["atr_used"]),
                "basis_close": basis_close,
            },
            None,
        )
    if direction == "short" and close_price < basis_close:
        return (
            {
                "direction": "short",
                "reason": "A_SHORT_CONFIRM",
                "atr_used": float(candidate["atr_used"]),
                "basis_close": basis_close,
            },
            None,
        )

    return None, candidate

