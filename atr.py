from typing import Any, List, Optional


def compute_atr_wilder_from_klines(klines: List[List[Any]], period: int) -> Optional[float]:
    """
    Binance kline format:
      [ openTime, open, high, low, close, volume, closeTime, ... ]
    """
    if len(klines) < period + 2:
        # Need prev_close for TR; period TRs require at least period+1 closes => period+2 kline rows
        return None

    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]

    # TR uses prev close => TR indices align with i from 1..N-1
    tr_values: List[float] = []
    for i in range(1, len(klines)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(float(tr))

    if len(tr_values) < period:
        return None

    # initial ATR = average of first period TRs
    atr = sum(tr_values[:period]) / period

    # apply Wilder smoothing for remaining TRs
    for tr in tr_values[period:]:
        atr = (atr * (period - 1) + tr) / period

    return float(atr)

