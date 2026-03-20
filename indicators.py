from collections import deque
from typing import Deque, Dict, Optional


class TrendIndicatorComputer:
    """
    Incremental EMA200 + ATR14 + ADX14 calculator (Wilder smoothing).
    Feed only closed candles via update(high, low, close).
    """

    def __init__(self, ema_period: int = 200, atr_period: int = 14, adx_period: int = 14):
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.adx_period = adx_period

        self.prev_close: Optional[float] = None
        self.prev_high: Optional[float] = None
        self.prev_low: Optional[float] = None

        self.ema: Optional[float] = None
        self._ema_count = 0
        self._ema_sum = 0.0
        self._ema_k = 2.0 / (ema_period + 1.0)

        self.atr: Optional[float] = None
        self._tr_count = 0
        self._tr_sum = 0.0

        self._dm_count = 0
        self._plus_dm_sum = 0.0
        self._minus_dm_sum = 0.0
        self._sm_plus_dm: Optional[float] = None
        self._sm_minus_dm: Optional[float] = None
        self._dx_count = 0
        self._dx_sum = 0.0
        self.adx: Optional[float] = None

        self.atr_window: Deque[float] = deque(maxlen=30)

        # RSI(14) - Wilder smoothing
        self.rsi_period = 14
        self._rsi_gain_sum = 0.0
        self._rsi_loss_sum = 0.0
        self._rsi_count = 0
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None
        self.rsi: Optional[float] = None

    def _true_range(self, high: float, low: float, prev_close: float) -> float:
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    def update(self, high: float, low: float, close: float) -> Dict[str, Optional[float]]:
        if self.prev_close is None:
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            self._ema_count = 1
            self._ema_sum = close
            return {"ema": None, "atr": None, "adx": None, "atr_ma30": None, "rsi": None}

        # RSI (Wilder)
        delta = close - self.prev_close
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        if self._avg_gain is None or self._avg_loss is None:
            self._rsi_count += 1
            self._rsi_gain_sum += gain
            self._rsi_loss_sum += loss
            if self._rsi_count >= self.rsi_period:
                self._avg_gain = self._rsi_gain_sum / self.rsi_period
                self._avg_loss = self._rsi_loss_sum / self.rsi_period
        else:
            self._avg_gain = ((self._avg_gain * (self.rsi_period - 1)) + gain) / self.rsi_period
            self._avg_loss = ((self._avg_loss * (self.rsi_period - 1)) + loss) / self.rsi_period

        if self._avg_gain is not None and self._avg_loss is not None:
            if self._avg_loss == 0:
                self.rsi = 100.0
            else:
                rs = self._avg_gain / self._avg_loss
                self.rsi = 100.0 - (100.0 / (1.0 + rs))

        # EMA
        if self.ema is None:
            self._ema_count += 1
            self._ema_sum += close
            if self._ema_count >= self.ema_period:
                self.ema = self._ema_sum / self.ema_period
        else:
            self.ema = (close - self.ema) * self._ema_k + self.ema

        # ATR (Wilder)
        tr = self._true_range(high, low, self.prev_close)
        if self.atr is None:
            self._tr_count += 1
            self._tr_sum += tr
            if self._tr_count >= self.atr_period:
                self.atr = self._tr_sum / self.atr_period
        else:
            self.atr = ((self.atr * (self.atr_period - 1)) + tr) / self.atr_period
        if self.atr is not None:
            self.atr_window.append(self.atr)

        # DMI/ADX
        up_move = high - (self.prev_high or high)
        down_move = (self.prev_low or low) - low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        if self._sm_plus_dm is None or self._sm_minus_dm is None:
            self._dm_count += 1
            self._plus_dm_sum += plus_dm
            self._minus_dm_sum += minus_dm
            if self._dm_count >= self.adx_period:
                self._sm_plus_dm = self._plus_dm_sum
                self._sm_minus_dm = self._minus_dm_sum
        else:
            self._sm_plus_dm = self._sm_plus_dm - (self._sm_plus_dm / self.adx_period) + plus_dm
            self._sm_minus_dm = self._sm_minus_dm - (self._sm_minus_dm / self.adx_period) + minus_dm

        if self.atr and self._sm_plus_dm is not None and self._sm_minus_dm is not None and self.atr > 0:
            plus_di = (self._sm_plus_dm / self.atr) * 100.0
            minus_di = (self._sm_minus_dm / self.atr) * 100.0
            denom = plus_di + minus_di
            dx = 0.0 if denom == 0 else abs(plus_di - minus_di) / denom * 100.0

            if self.adx is None:
                self._dx_count += 1
                self._dx_sum += dx
                if self._dx_count >= self.adx_period:
                    self.adx = self._dx_sum / self.adx_period
            else:
                self.adx = ((self.adx * (self.adx_period - 1)) + dx) / self.adx_period

        self.prev_close = close
        self.prev_high = high
        self.prev_low = low

        atr_ma30 = None
        if len(self.atr_window) >= 30:
            atr_ma30 = sum(self.atr_window) / 30.0

        return {
            "ema": self.ema,
            "atr": self.atr,
            "adx": self.adx,
            "atr_ma30": atr_ma30,
            "rsi": self.rsi,
        }

