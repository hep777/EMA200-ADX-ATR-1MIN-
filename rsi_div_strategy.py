"""
RSI 다이버전스 역추세 (1분봉) — 상태머신.

숏: RSI≥80 트리거 → 트리거 이후 20봉 이내 가격은 peak 이상 유지·RSI는 peak 미만(다이버전스) →
    다이버전스 ON 뒤: 종가 < 직전 20봉 종가 최저 → 다음 봉 시가 시장가 진입.

롱: RSI≤20 트리거 → 미러 (저점 이하 종가·RSI 상승 다이버전스 → 종가 > 직전 20봉 최고).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Literal, Optional, Tuple


class RsiAtrComputer:
    """Wilder RSI(14) + ATR(14) — 종가 확정 봉만 update()."""

    def __init__(self, rsi_period: int = 14, atr_period: int = 14):
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.prev_close: Optional[float] = None
        self.prev_high: Optional[float] = None
        self.prev_low: Optional[float] = None

        self._rsi_gain_sum = 0.0
        self._rsi_loss_sum = 0.0
        self._rsi_count = 0
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None
        self.rsi: Optional[float] = None

        self.atr: Optional[float] = None
        self._tr_count = 0
        self._tr_sum = 0.0

    def _true_range(self, high: float, low: float, prev_close: float) -> float:
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    def update(self, high: float, low: float, close: float) -> Dict[str, Optional[float]]:
        if self.prev_close is None:
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            return {"rsi": None, "atr": None}

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

        tr = self._true_range(high, low, self.prev_close)
        if self.atr is None:
            self._tr_count += 1
            self._tr_sum += tr
            if self._tr_count >= self.atr_period:
                self.atr = self._tr_sum / self.atr_period
        else:
            self.atr = ((self.atr * (self.atr_period - 1)) + tr) / self.atr_period

        self.prev_close = close
        self.prev_high = high
        self.prev_low = low
        return {"rsi": self.rsi, "atr": self.atr}


Phase = Literal["idle", "watch", "div"]


@dataclass
class SideState:
    phase: Phase = "idle"
    trigger_idx: int = 0
    ref_rsi: float = 0.0
    ref_close: float = 0.0
    divergence: bool = False


class RsiDivergenceMachine:
    """심볼별 숏/롱 독립 상태."""

    def __init__(
        self,
        short_trigger_rsi: float = 80.0,
        long_trigger_rsi: float = 20.0,
        div_window: int = 20,
        breakout_lookback: int = 20,
    ):
        self.short_trigger_rsi = short_trigger_rsi
        self.long_trigger_rsi = long_trigger_rsi
        self.div_window = div_window
        self.breakout_lookback = breakout_lookback
        self.short_st: SideState = SideState()
        self.long_st: SideState = SideState()

    def reset(self) -> None:
        self.short_st = SideState()
        self.long_st = SideState()

    def on_closed_bar(
        self,
        bar_index: int,
        close: float,
        rsi: Optional[float],
        atr: Optional[float],
        closes: Deque[float],
    ) -> Tuple[Optional[Dict[str, float | str]], List[str]]:
        """
        종가 확정 봉 1개 처리. closes[-1] == 현재 봉 종가.

        Returns:
            (schedule_entry | None, events)
        """
        events: List[str] = []
        if rsi is None or atr is None or atr <= 0:
            return None, events

        # ---------- Short ----------
        s = self.short_st
        bars_after_s = bar_index - s.trigger_idx

        if s.phase == "idle":
            if rsi >= self.short_trigger_rsi:
                s.phase = "watch"
                s.trigger_idx = bar_index
                s.ref_rsi = float(rsi)
                s.ref_close = float(close)
                s.divergence = False
                events.append("SHORT_TRIGGER")
        elif s.phase == "watch":
            if not s.divergence:
                if bars_after_s > self.div_window:
                    s.phase = "idle"
                    events.append("SHORT_WATCH_TIMEOUT")
                elif bars_after_s >= 1:
                    if float(close) > s.ref_close and float(rsi) < s.ref_rsi:
                        s.divergence = True
                        s.phase = "div"
                        events.append("SHORT_DIV_ON")

        if s.phase == "div":
            lo = self._min_prev_n_closes(closes, self.breakout_lookback)
            if lo is not None and float(close) < lo:
                events.append("SHORT_BREAKDOWN")
                sched = {"side": "short", "atr": float(atr)}
                self.short_st = SideState()
                return sched, events

        # ---------- Long ----------
        lg = self.long_st
        bars_after_l = bar_index - lg.trigger_idx

        if lg.phase == "idle":
            if rsi <= self.long_trigger_rsi:
                lg.phase = "watch"
                lg.trigger_idx = bar_index
                lg.ref_rsi = float(rsi)
                lg.ref_close = float(close)
                lg.divergence = False
                events.append("LONG_TRIGGER")
        elif lg.phase == "watch":
            if not lg.divergence:
                if bars_after_l > self.div_window:
                    lg.phase = "idle"
                    events.append("LONG_WATCH_TIMEOUT")
                elif bars_after_l >= 1:
                    if float(close) < lg.ref_close and float(rsi) > lg.ref_rsi:
                        lg.divergence = True
                        lg.phase = "div"
                        events.append("LONG_DIV_ON")

        if lg.phase == "div":
            hi = self._max_prev_n_closes(closes, self.breakout_lookback)
            if hi is not None and float(close) > hi:
                events.append("LONG_BREAKOUT")
                sched = {"side": "long", "atr": float(atr)}
                self.long_st = SideState()
                return sched, events

        return None, events

    @staticmethod
    def _min_prev_n_closes(closes: Deque[float], n: int) -> Optional[float]:
        """현재 종가 제외 직전 n개 종가의 최소."""
        if len(closes) < n + 1:
            return None
        prev = list(closes)[-(n + 1) : -1]
        return min(prev) if prev else None

    @staticmethod
    def _max_prev_n_closes(closes: Deque[float], n: int) -> Optional[float]:
        if len(closes) < n + 1:
            return None
        prev = list(closes)[-(n + 1) : -1]
        return max(prev) if prev else None
