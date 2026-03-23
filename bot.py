#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance USDT 무기한 선물 — BB 스퀴즈 + 밴드 돌파 + RSI 기울기 + ATR SL (15분봉).

재시작 표준 (서버 예시):
  pkill -f "python3 bot.py"
  sleep 3
  rm -f /tmp/bot.lock
  nohup python3 /root/bot/bot.py >> /root/bot/bot.log 2>&1 &
  ps aux | grep bot.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import websocket

import telegram_client as tg
from bb_strategy import (
    compute_atr_wilder,
    evaluate_entry_signal,
    initial_sl_price,
    loss_pct_vs_entry,
    trail_candidate_sl,
)
from binance_client import (
    calculate_quantity,
    cancel_all_open_orders,
    close_position_market,
    get_account_equity_usdt,
    get_klines,
    get_mark_price,
    get_open_positions,
    get_top_usdt_perpet_by_quote_volume,
    open_position_market,
    round_price_to_tick,
    set_isolated_and_leverage,
)
from config import (
    API_MAX_RETRIES,
    ATR_MULTIPLIER,
    ATR_PERIOD,
    BB_PERIOD,
    BB_STD,
    BB_SQUEEZE_LOOKBACK,
    BB_SQUEEZE_MAX_MULT,
    DEFAULT_LEVERAGE,
    HIGH_VOL_MAX_SL_PCT,
    HIGH_VOL_POSITION_SIZE_PCT,
    KLINES_BOOTSTRAP_LIMIT,
    LOCK_FILE,
    LOG_FILE,
    MARK_POLL_INTERVAL_SEC,
    MAX_CONCURRENT_POSITIONS,
    MAX_SL_PCT,
    POSITION_RISK_PCT,
    RSI_PERIOD,
    RSI_SLOPE_BARS,
    STREAM_BATCH_SIZE,
    SYMBOL_REFRESH_INTERVAL,
    TIME_EXIT_BARS,
    TRAIL_ACTIVATE_MULTIPLIER,
    UNIVERSE_TOP_N,
    validate_secrets,
    WEBSOCKET_PING_INTERVAL,
    WEBSOCKET_PING_TIMEOUT,
)
from state_manager import load_state, remove_position, save_state, upsert_position

logger = logging.getLogger("bot")

state_lock = threading.Lock()
bot_active = True

tracked_symbols: List[str] = []
streamed_symbols: Set[str] = set()

# OHLC 히스토리 (15분 확정봉만 append)
ohlc_closes: Dict[str, deque] = {}
ohlc_highs: Dict[str, deque] = {}
ohlc_lows: Dict[str, deque] = {}

pending_entry: Dict[str, Dict[str, Any]] = {}
active_positions: Dict[str, Dict[str, Any]] = {}
latest_price_map: Dict[str, float] = {}

_last_universe_refresh_ts: float = 0.0


def check_single_instance() -> None:
    lock_dir = os.path.dirname(LOCK_FILE)
    if lock_dir and not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip() or "0")
            if old_pid and os.name != "nt" and os.path.exists(f"/proc/{old_pid}"):
                print(f"Bot already running (PID: {old_pid}). Exiting.")
                sys.exit(1)
        except Exception:
            pass
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def remove_lock() -> None:
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )


def api_retry(fn, *args, **kwargs):
    delay = 1.0
    last = None
    for attempt in range(API_MAX_RETRIES):
        try:
            r = fn(*args, **kwargs)
            if r is not None:
                return r
        except Exception as e:
            last = e
            logger.warning("api_retry %s attempt %s: %s", fn.__name__, attempt + 1, e)
        time.sleep(delay)
        delay = min(delay * 2, 8.0)
    if last:
        logger.error("api_retry exhausted: %s", last)
    return None


def _binance_link(sym: str) -> str:
    return f"https://www.binance.com/en/futures/{sym}"


def _fmt(x: float) -> str:
    return f"{x:.6f}"


def _binance_footer(sym: str) -> str:
    return f'<a href="{_binance_link(sym)}">Binance</a>'


def _sl_close_title(pnl_pct: float) -> str:
    # 아이콘만 봐도 구분: 🟢 익(초록) / 🔴 손(빨강) / ⚖️ 본절
    if pnl_pct > 0:
        return "🟢 익절 청산 (마크 SL)"
    if pnl_pct < 0:
        return "🔴 손절 청산 (마크 SL)"
    return "⚖️ 본절 청산 (마크 SL)"


def merge_universe_with_positions(symbols: List[str]) -> List[str]:
    u: Set[str] = set(s.lower() for s in symbols)
    for p in get_open_positions():
        s = str(p.get("symbol", "")).lower()
        if s:
            u.add(s)
    return sorted(u)


def bootstrap_symbol(symbol_upper: str) -> None:
    ohlc_closes[symbol_upper] = deque(maxlen=500)
    ohlc_highs[symbol_upper] = deque(maxlen=500)
    ohlc_lows[symbol_upper] = deque(maxlen=500)
    kl = api_retry(get_klines, symbol_upper, "15m", KLINES_BOOTSTRAP_LIMIT)
    if not kl:
        return
    for k in kl:
        o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        _ = o
        ohlc_highs[symbol_upper].append(h)
        ohlc_lows[symbol_upper].append(l)
        ohlc_closes[symbol_upper].append(c)


def select_universe() -> List[str]:
    return api_retry(get_top_usdt_perpet_by_quote_volume, UNIVERSE_TOP_N) or []


def update_trailing_sl(
    symbol_upper: str,
    pos: Dict[str, Any],
    bar_high: float,
    bar_low: float,
    closes: List[float],
    highs: List[float],
    lows: List[float],
) -> Tuple[bool, float, float]:
    """
    15분 봉 마감 시 ATR×배수로 트레일.
    Returns: (changed, old_sl, new_sl)
    """
    direction = str(pos["direction"])
    sl = float(pos["sl_price"])
    old_sl = sl
    atr_arr = compute_atr_wilder(highs, lows, closes, ATR_PERIOD)
    cur = len(closes) - 1
    atr_val = atr_arr[cur] if cur < len(atr_arr) else None
    if atr_val is None or atr_val <= 0:
        return False, old_sl, sl
    cand = trail_candidate_sl(direction, bar_high, bar_low, atr_val, ATR_MULTIPLIER)
    cand = round_price_to_tick(symbol_upper, cand)
    if direction == "long":
        if cand > sl:
            pos["sl_price"] = cand
            return True, old_sl, cand
    else:
        if cand < sl:
            pos["sl_price"] = cand
            return True, old_sl, cand
    return False, old_sl, sl


def time_exit_close(symbol_upper: str) -> None:
    """TIME_EXIT_BARS 경과 + 트레일 미활성 시 시장가 청산."""
    with state_lock:
        if symbol_upper not in active_positions:
            return
        pos = dict(active_positions[symbol_upper])
    direction = str(pos["direction"])
    qty = float(pos["quantity"])
    entry = float(pos["entry_price"])
    try:
        mk = float(get_mark_price(symbol_upper))
    except Exception as e:
        logger.warning("time_exit_close mark %s: %s", symbol_upper, e)
        return
    try:
        cancel_all_open_orders(symbol_upper)
    except Exception:
        pass
    close_position_market(symbol_upper.lower(), direction, qty)
    if direction == "long":
        pnl_pct = (mk - entry) / entry * 100.0 if entry else 0.0
    else:
        pnl_pct = (entry - mk) / entry * 100.0 if entry else 0.0
    with state_lock:
        if symbol_upper not in active_positions:
            return
        active_positions.pop(symbol_upper, None)
        st = load_state()
        remove_position(st, symbol_upper)
        save_state(st)
    tg.send_message(
        f"⏰ 시간초과 청산: #{symbol_upper}\n"
        f"포지션 : {direction.upper()}\n"
        f"진입가: {_fmt(entry)}\n"
        f"청산가: {_fmt(mk)}\n"
        f"손익: {pnl_pct:+.2f}%\n"
        f"{_binance_footer(symbol_upper)}"
    )
    logger.info("TIME_EXIT %s mark=%s pnl%%=%s", symbol_upper, mk, pnl_pct)


def try_fire_pending(symbol_upper: str, k: Dict[str, Any]) -> None:
    pe = pending_entry.get(symbol_upper)
    if not pe or pe.get("done"):
        return
    t_open = int(k.get("t", 0))
    if t_open < int(pe["next_bar_open_ms"]):
        return
    pe["done"] = True
    execute_entry(symbol_upper, pe)


def execute_entry(symbol_upper: str, pe: Dict[str, Any]) -> None:
    if not bot_active:
        return
    direction = str(pe["direction"])
    br_hi = float(pe["breakout_high"])
    br_lo = float(pe["breakout_low"])
    signal_atr = float(pe["signal_atr"])

    with state_lock:
        if symbol_upper in active_positions:
            pending_entry.pop(symbol_upper, None)
            return
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
            pending_entry.pop(symbol_upper, None)
            return

    sl0 = initial_sl_price(direction, br_hi, br_lo, signal_atr, ATR_MULTIPLIER)
    sl0 = round_price_to_tick(symbol_upper, sl0)

    mark = float(get_mark_price(symbol_upper))
    lp = loss_pct_vs_entry(direction, mark, sl0)
    if lp > HIGH_VOL_MAX_SL_PCT:
        tg.send_message(
            f"⛔ 진입 SKIP (SL 초과): #{symbol_upper}\n"
            f"SL거리: {lp*100:.2f}%\n"
            f"{_binance_footer(symbol_upper)}"
        )
        pending_entry.pop(symbol_upper, None)
        logger.info("SKIP_SL_TOO_WIDE %s loss_pct=%.4f", symbol_upper, lp)
        return

    high_vol = lp > MAX_SL_PCT
    risk_pct = HIGH_VOL_POSITION_SIZE_PCT if high_vol else POSITION_RISK_PCT

    eq = float(get_account_equity_usdt())
    margin_usdt = eq * float(risk_pct)
    lev = set_isolated_and_leverage(symbol_upper)
    if lev is None:
        pending_entry.pop(symbol_upper, None)
        return
    qty_res = calculate_quantity(symbol_upper, margin_usdt, mark, int(lev))
    if not qty_res:
        pending_entry.pop(symbol_upper, None)
        return
    qty, _ = qty_res

    res = open_position_market(symbol_upper.lower(), direction, qty)
    if not res:
        tg.send_message(f"❌ 진입 실패 #{symbol_upper}\n{_binance_footer(symbol_upper)}")
        pending_entry.pop(symbol_upper, None)
        return

    entry = float(res["entry_price"])
    qty_f = float(res["quantity"])

    lp_fill = loss_pct_vs_entry(direction, entry, sl0)
    sl_cap = HIGH_VOL_MAX_SL_PCT if high_vol else MAX_SL_PCT
    if lp_fill > sl_cap:
        try:
            cancel_all_open_orders(symbol_upper)
        except Exception:
            pass
        close_position_market(symbol_upper.lower(), direction, qty_f)
        tg.send_message(
            f"⚠️ 체결 후 SL캡 초과 → 즉시 청산\n"
            f"#{symbol_upper}\n"
            f"진입: {_fmt(entry)}\n"
            f"SL: {_fmt(sl0)}\n"
            f"{_binance_footer(symbol_upper)}"
        )
        pending_entry.pop(symbol_upper, None)
        logger.warning("POST_FILL_CAP %s", symbol_upper)
        return

    pos = {
        "symbol": symbol_upper,
        "direction": direction,
        "entry_price": entry,
        "quantity": qty_f,
        "sl_price": sl0,
        "signal_atr": signal_atr,
        "trail_active": False,
        "bars_since_entry": 0,
        "high_vol_entry": high_vol,
    }
    with state_lock:
        active_positions[symbol_upper] = pos
        st = load_state()
        upsert_position(st, symbol_upper, pos)
        save_state(st)
        pending_entry.pop(symbol_upper, None)

    side_ico = "📈" if direction == "long" else "📉"
    tg.send_message(
        f"{side_ico} 진입 {direction.upper()}\n"
        f"#{symbol_upper}\n"
        f"진입가: {_fmt(entry)}\n"
        f"초기 SL: {_fmt(sl0)}\n"
        f"ATR(신호봉): {_fmt(signal_atr)}\n"
        f"{_binance_footer(symbol_upper)}"
    )
    if high_vol:
        tg.send_message(
            f"⚡ 고변동성 진입 (사이즈 축소): #{symbol_upper} {direction.upper()}\n"
            f"SL거리: {lp*100:.2f}% 사이즈: {HIGH_VOL_POSITION_SIZE_PCT*100:.1f}%\n"
            f"{_binance_footer(symbol_upper)}"
        )
    logger.info("ENTRY %s %s entry=%s sl=%s atr=%s qty=%s high_vol=%s", symbol_upper, direction, entry, sl0, signal_atr, qty_f, high_vol)


def process_kline(symbol_lower: str, k: Dict[str, Any]) -> None:
    if not bot_active:
        return
    symbol_upper = symbol_lower.upper()
    if symbol_lower not in tracked_symbols:
        return

    if "c" in k:
        try:
            latest_price_map[symbol_upper] = float(k["c"])
        except Exception:
            pass

    try_fire_pending(symbol_upper, k)

    if not k.get("x"):
        return

    o = float(k["o"])
    h, l, c = float(k["h"]), float(k["l"]), float(k["c"])
    _ = o

    dq_c = ohlc_closes.get(symbol_upper)
    dq_h = ohlc_highs.get(symbol_upper)
    dq_l = ohlc_lows.get(symbol_upper)
    if dq_c is None:
        return

    dq_h.append(h)
    dq_l.append(l)
    dq_c.append(c)

    closes = list(dq_c)
    highs = list(dq_h)
    lows = list(dq_l)

    with state_lock:
        in_pos = symbol_upper in active_positions

    if in_pos:
        with state_lock:
            pos = dict(active_positions.get(symbol_upper, {}))
        if not pos:
            return
        pos["bars_since_entry"] = int(pos.get("bars_since_entry", 0)) + 1
        trail_active = bool(pos.get("trail_active"))
        if pos["bars_since_entry"] >= TIME_EXIT_BARS and not trail_active:
            time_exit_close(symbol_upper)
            return
        if trail_active:
            chg, old_sl, new_sl = update_trailing_sl(
                symbol_upper, pos, h, l, closes, highs, lows
            )
            with state_lock:
                if symbol_upper in active_positions:
                    active_positions[symbol_upper] = pos
                    st = load_state()
                    upsert_position(st, symbol_upper, pos)
                    save_state(st)
            if chg:
                tg.send_message(
                    f"📌 SL 갱신 #{symbol_upper}\n"
                    f"이전 SL: {_fmt(old_sl)}\n"
                    f"새 SL: {_fmt(new_sl)}\n"
                    f"{_binance_footer(symbol_upper)}"
                )
        else:
            with state_lock:
                if symbol_upper in active_positions:
                    active_positions[symbol_upper] = pos
                    st = load_state()
                    upsert_position(st, symbol_upper, pos)
                    save_state(st)
        return

    sig = evaluate_entry_signal(
        closes,
        highs,
        lows,
        BB_PERIOD,
        BB_STD,
        BB_SQUEEZE_LOOKBACK,
        BB_SQUEEZE_MAX_MULT,
        RSI_PERIOD,
        RSI_SLOPE_BARS,
        ATR_PERIOD,
    )
    if not sig:
        return

    direction, br_hi, br_lo, sig_atr = sig
    with state_lock:
        if symbol_upper in active_positions:
            return
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
            return

    next_open_ms = int(k["T"]) + 1
    pending_entry[symbol_upper] = {
        "direction": direction,
        "breakout_high": br_hi,
        "breakout_low": br_lo,
        "signal_atr": float(sig_atr),
        "next_bar_open_ms": next_open_ms,
        "done": False,
    }
    logger.info(
        "PENDING %s %s next_open=%s",
        symbol_upper,
        direction,
        next_open_ms,
    )


def mark_monitor_loop() -> None:
    while True:
        time.sleep(MARK_POLL_INTERVAL_SEC)
        try:
            with state_lock:
                snap = list(active_positions.items())
            for sym, pos in snap:
                direction = str(pos["direction"])
                sl = float(pos["sl_price"])
                qty = float(pos["quantity"])
                try:
                    mk = float(get_mark_price(sym))
                except Exception:
                    continue

                # 1순위: 초기/트레일 SL
                hit = False
                if direction == "long" and mk <= sl:
                    hit = True
                elif direction == "short" and mk >= sl:
                    hit = True
                if hit:
                    try:
                        cancel_all_open_orders(sym)
                    except Exception:
                        pass
                    close_position_market(sym.lower(), direction, qty)
                    entry = float(pos["entry_price"])
                    if direction == "long":
                        pnl_pct = (mk - entry) / entry * 100.0 if entry else 0.0
                    else:
                        pnl_pct = (entry - mk) / entry * 100.0 if entry else 0.0
                    with state_lock:
                        active_positions.pop(sym, None)
                        st = load_state()
                        remove_position(st, sym)
                        save_state(st)
                    tg.send_message(
                        f"{_sl_close_title(pnl_pct)}\n"
                        f"#{sym} {direction.upper()}\n"
                        f"청산가(마크): {_fmt(mk)}\n"
                        f"손익: {pnl_pct:+.2f}%\n"
                        f"{_binance_footer(sym)}"
                    )
                    logger.info("CLOSE %s mark=%s sl=%s pnl%%=%s", sym, mk, sl, pnl_pct)
                    continue

                # 트레일 활성화 (마크 폴링, 1회만)
                if bool(pos.get("trail_active")):
                    continue
                entry = float(pos["entry_price"])
                sig_atr = float(pos.get("signal_atr", 0))
                if sig_atr <= 0:
                    continue
                thr = sig_atr * TRAIL_ACTIVATE_MULTIPLIER
                activated = False
                if direction == "long" and mk >= entry + thr:
                    activated = True
                elif direction == "short" and mk <= entry - thr:
                    activated = True
                if not activated:
                    continue
                with state_lock:
                    if sym not in active_positions:
                        continue
                    p = active_positions[sym]
                    if bool(p.get("trail_active")):
                        continue
                    p["trail_active"] = True
                    st = load_state()
                    upsert_position(st, sym, p)
                    save_state(st)
                tg.send_message(
                    f"🎯 트레일링 활성화: #{sym} {direction.upper()}\n"
                    f"진입가: {_fmt(entry)}\n"
                    f"활성화가: {_fmt(mk)}\n"
                    f"{_binance_footer(sym)}"
                )
                logger.info("TRAIL_ON %s entry=%s mk=%s thr=%s", sym, entry, mk, thr)
        except Exception as e:
            logger.exception("mark_monitor: %s", e)
            try:
                tg.send_message(f"❌ 마크 감시 오류\n{e!s}")
            except Exception:
                pass


def recover_positions() -> None:
    global active_positions
    st = load_state()
    saved = st.get("positions", {})
    exch = get_open_positions()
    exch_syms = {p["symbol"] for p in exch}
    for p in exch:
        sym = p["symbol"]
        direction = p["direction"]
        entry = float(p["entry_price"])
        qty = float(p["amount"])
        if sym in saved:
            pos = saved[sym]
            pos["quantity"] = qty
            pos["entry_price"] = entry
            pos["direction"] = direction
            pos.setdefault("trail_active", False)
            pos.setdefault("bars_since_entry", 0)
            pos.setdefault("high_vol_entry", False)
            sa = float(pos.get("signal_atr", 0) or 0)
            if sa <= 0:
                pos["signal_atr"] = max(entry * 0.002, 1e-12)
            if "sl_price" not in pos or float(pos.get("sl_price", 0)) <= 0:
                if direction == "long":
                    pos["sl_price"] = round_price_to_tick(sym, entry * (1.0 - MAX_SL_PCT * 0.9))
                else:
                    pos["sl_price"] = round_price_to_tick(sym, entry * (1.0 + MAX_SL_PCT * 0.9))
        else:
            if direction == "long":
                sl0 = round_price_to_tick(sym, entry * (1.0 - MAX_SL_PCT * 0.8))
            else:
                sl0 = round_price_to_tick(sym, entry * (1.0 + MAX_SL_PCT * 0.8))
            pos = {
                "symbol": sym,
                "direction": direction,
                "entry_price": entry,
                "quantity": qty,
                "sl_price": sl0,
                "trail_active": False,
                "bars_since_entry": 0,
                "high_vol_entry": False,
                "signal_atr": max(entry * 0.002, 1e-12),
            }
        active_positions[sym] = pos
        upsert_position(st, sym, pos)
    for s in list(saved.keys()):
        if s not in exch_syms:
            remove_position(st, s)
    save_state(st)


def cmd_status() -> None:
    n = len(active_positions)
    eq = get_account_equity_usdt()
    st = "ON" if bot_active else "OFF"
    tg.send_message(
        f"📊 BB 봇\n"
        f"신규진입: {st}\n"
        f"잔고: {eq:.2f} USDT\n"
        f"포지션: {n}/{MAX_CONCURRENT_POSITIONS}\n"
        f"감시: {len(tracked_symbols)} 심볼"
    )


def cmd_stop() -> None:
    global bot_active
    bot_active = False
    tg.send_message("⏸️ 신규 진입 중지")


def cmd_restart() -> None:
    global bot_active
    bot_active = True
    tg.send_message("▶️ 신규 진입 재개")


def cmd_closeall() -> None:
    global bot_active
    bot_active = False
    with state_lock:
        snap = dict(active_positions)
    for sym in snap:
        try:
            cancel_all_open_orders(sym)
        except Exception:
            pass
    for sym, pos in snap.items():
        try:
            close_position_market(sym.lower(), pos["direction"], float(pos["quantity"]))
        except Exception:
            pass
    with state_lock:
        active_positions.clear()
        st = load_state()
        st["positions"] = {}
        save_state(st)
    tg.send_message("🟠 CLOSEALL 완료")


def _on_message(ws, message: str) -> None:
    try:
        payload = json.loads(message)
        data = payload.get("data") if "data" in payload else payload
        if data.get("e") != "kline":
            return
        k = data.get("k", {})
        s = data.get("s", "").lower()
        process_kline(s, k)
    except Exception as e:
        logger.error("WS: %s", e)


def _on_error(ws, err):
    logger.error("WS err: %s", err)


def _on_close(ws, code, msg):
    logger.warning("WS close %s %s", code, msg)


def _on_open(ws):
    logger.info("WS connected")


def _run_ws(url: str, idx: int, n: int) -> None:
    delay = 3.0
    while True:
        try:
            ws = websocket.WebSocketApp(
                url, on_message=_on_message, on_error=_on_error, on_close=_on_close, on_open=_on_open
            )
            ws.run_forever(ping_interval=WEBSOCKET_PING_INTERVAL, ping_timeout=WEBSOCKET_PING_TIMEOUT)
        except Exception as e:
            logger.error("WS batch %s: %s", idx, e)
        time.sleep(delay)
        delay = min(delay * 2, 30.0)


def start_websockets(symbols_lower: List[str]) -> None:
    global streamed_symbols
    new = [s for s in symbols_lower if s not in streamed_symbols]
    if not new:
        return
    streams = [f"{s}@kline_15m" for s in new]
    for i in range(0, len(streams), STREAM_BATCH_SIZE):
        batch = streams[i : i + STREAM_BATCH_SIZE]
        url = "wss://fstream.binance.com/stream?streams=" + "/".join(batch)
        bi = i // STREAM_BATCH_SIZE + 1
        threading.Thread(target=_run_ws, args=(url, bi, len(batch)), daemon=True).start()
        time.sleep(0.3)
    streamed_symbols |= set(new)


def universe_refresh_loop() -> None:
    global tracked_symbols, _last_universe_refresh_ts
    while True:
        time.sleep(max(60.0, float(SYMBOL_REFRESH_INTERVAL)))
        try:
            uni = select_universe()
            if not uni:
                continue
            merged = merge_universe_with_positions(uni)
            tracked_symbols[:] = merged
            for s in merged:
                su = s.upper()
                if su not in ohlc_closes:
                    bootstrap_symbol(su)
            start_websockets(list(merged))
            _last_universe_refresh_ts = time.time()
            tg.send_message(
                f"🔄 유니버스 갱신 ({SYMBOL_REFRESH_INTERVAL}s)\n대상 {len(merged)}개"
            )
        except Exception as e:
            logger.exception("universe_refresh: %s", e)
            try:
                tg.send_message(f"❌ 유니버스 갱신 오류\n{e!s}")
            except Exception:
                pass


def main() -> None:
    global tracked_symbols, _last_universe_refresh_ts
    setup_logging()
    validate_secrets()
    check_single_instance()

    tg.register_command("status", cmd_status)
    tg.register_command("stop", cmd_stop)
    tg.register_command("restart", cmd_restart)
    tg.register_command("closeall", cmd_closeall)
    tg.start_polling()

    recover_positions()

    uni = select_universe()
    if not uni:
        tg.send_message("❌ 유니버스 조회 실패")
        remove_lock()
        return

    tracked_symbols[:] = merge_universe_with_positions(uni)
    for s in tracked_symbols:
        bootstrap_symbol(s.upper())

    _last_universe_refresh_ts = time.time()

    tg.send_message(
        f"🚀 BB 스퀴즈 봇 시작\n"
        f"15분봉 · 레버 {DEFAULT_LEVERAGE}x · 진입 {POSITION_RISK_PCT*100:.0f}% "
        f"(고변동 {HIGH_VOL_POSITION_SIZE_PCT*100:.1f}%) · 최대 {MAX_CONCURRENT_POSITIONS}포지션\n"
        f"SL: ATR{ATR_PERIOD}×{ATR_MULTIPLIER} · SL캡 일반≤{MAX_SL_PCT*100:.0f}% 고변동≤{HIGH_VOL_MAX_SL_PCT*100:.0f}%\n"
        f"트레일 활성: 마크가 ±ATR×{TRAIL_ACTIVATE_MULTIPLIER} · "
        f"미활성 {TIME_EXIT_BARS}봉 시 시간초과 청산\n"
        f"감시 심볼: {len(tracked_symbols)}"
    )

    threading.Thread(target=mark_monitor_loop, daemon=True).start()
    threading.Thread(target=universe_refresh_loop, daemon=True).start()
    start_websockets(tracked_symbols)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        tg.send_message("🛑 봇 종료")
        remove_lock()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("fatal: %s", e)
        try:
            tg.send_message(f"💥 봇 치명적 오류\n{e!s}")
        except Exception:
            pass
        remove_lock()
        sys.exit(1)
