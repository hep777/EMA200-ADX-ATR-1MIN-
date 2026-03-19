from typing import Optional


class RSIComputer:
    """
    Wilder's RSI with incremental updates.
    Call update(close) on every closed candle.
    Returns RSI when warmed up, else None.
    """

    def __init__(self, period: int = 14):
        self.period = period
        self.prev_close: Optional[float] = None
        self.avg_gain: Optional[float] = None
        self.avg_loss: Optional[float] = None
        self._count_changes = 0
        self._gain_sum = 0.0
        self._loss_sum = 0.0

    def update(self, close: float) -> Optional[float]:
        if self.prev_close is None:
            self.prev_close = close
            return None

        delta = close - self.prev_close
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)

        if self.avg_gain is None or self.avg_loss is None:
            self._count_changes += 1
            self._gain_sum += gain
            self._loss_sum += loss
            if self._count_changes < self.period:
                self.prev_close = close
                return None

            # initial smoothed averages
            self.avg_gain = self._gain_sum / self.period
            self.avg_loss = self._loss_sum / self.period
        else:
            # Wilder smoothing
            self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period

        self.prev_close = close

        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return float(rsi)

