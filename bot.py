#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

import websocket

import telegram_client as tg
from binance_client import (
    calculate_quantity,
    close_position_market,
    get_account_equity_usdt,
    get_klines,
    get_mark_price,
    get_open_positions,
    get_symbol_info,
    get_top_usdt_perpet_by_quote_volume,
    open_position_market,
    set_isolated_and_leverage,
)
from config import (
    API_MAX_RETRIES,
    BREAKOUT_TP_CLOSE_RATIO,
    BREAKOUT_TP_PCT,
    DEFAULT_LEVERAGE,
    FAKEOUT_TP_PCT,
    KLINES_BOOTSTRAP_LIMIT,
    LOCK_FILE,
    LOG_FILE,
    MARK_POLL_INTERVAL_SEC,
    MAX_CONCURRENT_POSITIONS,
    POSITION_RISK_PCT,
    STREAM_BATCH_SIZE,
    SWING_LEFT_BARS,
    SWING_LOOKBACK_BARS,
    SWING_RIGHT_BARS,
    SYMBOL_REFRESH_INTERVAL,
    TRENDLINE_MIN_POINTS,
    TRENDLINE_MIN_R2,
    UNIVERSE_TOP_N,
    VOLUME_AVG_PERIOD,
    validate_secrets,
    WEBSOCKET_PING_INTERVAL,
    WEBSOCKET_PING_TIMEOUT,
)
from state_manager import load_state, remove_position, save_state, upsert_position
from trend_strategy import detect_confirmed_swing, fit_regression, line_value

logger = logging.getLogger("bot")

state_lock = threading.Lock()
shutdown_event = threading.Event()
tracked_symbols: List[str] = []
streamed_symbols: Set[str] = set()
active_positions: Dict[str, Dict[str, Any]] = {}
pending_entries: Dict[str, Dict[str, Any]] = {}
trendlines: Dict[str, Dict[str, Optional[Dict[str, Any]]]] = {}
swing_points: Dict[str, Dict[str, List[Dict[str, float]]]] = {}

symbol_data: Dict[str, Dict[str, Any]] = {}


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
    for _ in range(API_MAX_RETRIES):
        try:
            out = fn(*args, **kwargs)
            if out is not None:
                return out
        except Exception as e:
            logger.warning("api_retry %s: %s", fn.__name__, e)
        time.sleep(delay)
        delay = min(delay * 2, 8.0)
    return None


def _fmt(v: float) -> str:
    return f"{v:.6f}"


def _binance_link(sym: str) -> str:
    return f"https://www.binance.com/en/futures/{sym}"


def _round_order_qty(symbol_upper: str, qty: float) -> float:
    info = get_symbol_info(symbol_upper)
    if not info:
        return max(0.0, float(qty))
    q = round(float(qty), int(info["qty_precision"]))
    return max(0.0, q)


def persist_runtime_state() -> None:
    st = load_state()
    st["pending_entries"] = pending_entries
    st["trendlines"] = trendlines
    st["swing_points"] = swing_points
    for sym, pos in active_positions.items():
        upsert_position(st, sym, pos)
    save_state(st)


def restore_runtime_state() -> None:
    st = load_state()
    pending_entries.update(st.get("pending_entries", {}))
    trendlines.update(st.get("trendlines", {}))
    swing_points.update(st.get("swing_points", {}))


def init_symbol_data(symbol_upper: str) -> None:
    symbol_data[symbol_upper] = {
        "seq": deque(maxlen=600),
        "open": deque(maxlen=600),
        "high": deque(maxlen=600),
        "low": deque(maxlen=600),
        "close": deque(maxlen=600),
        "volume": deque(maxlen=600),
        "last_seq": -1,
        "last_close_time": 0,
    }
    swing_points.setdefault(symbol_upper, {"highs": [], "lows": []})
    trendlines.setdefault(symbol_upper, {"up": None, "down": None})


def bootstrap_symbol(symbol_upper: str) -> None:
    init_symbol_data(symbol_upper)
    kl = api_retry(get_klines, symbol_upper, "1m", KLINES_BOOTSTRAP_LIMIT)
    if not kl:
        return
    s = symbol_data[symbol_upper]
    seq = 0
    for k in kl:
        s["seq"].append(seq)
        s["open"].append(float(k[1]))
        s["high"].append(float(k[2]))
        s["low"].append(float(k[3]))
        s["close"].append(float(k[4]))
        s["volume"].append(float(k[5]))
        s["last_close_time"] = int(k[6])
        seq += 1
    s["last_seq"] = seq - 1
    rebuild_swings(symbol_upper)
    rebuild_lines(symbol_upper)


def rebuild_swings(symbol_upper: str) -> None:
    s = symbol_data[symbol_upper]
    highs = list(s["high"])
    lows = list(s["low"])
    seqs = list(s["seq"])
    out_h: List[Dict[str, float]] = []
    out_l: List[Dict[str, float]] = []
    n = len(seqs)
    for center in range(SWING_LEFT_BARS, n - SWING_RIGHT_BARS):
        lo = center - SWING_LEFT_BARS
        hi = center + SWING_RIGHT_BARS + 1
        if highs[center] == max(highs[lo:hi]):
            out_h.append({"seq": float(seqs[center]), "price": float(highs[center])})
        if lows[center] == min(lows[lo:hi]):
            out_l.append({"seq": float(seqs[center]), "price": float(lows[center])})
    swing_points[symbol_upper] = {"highs": out_h, "lows": out_l}


def _line_from_swings(points: List[Dict[str, float]], kind: str, now_seq: int) -> Optional[Dict[str, Any]]:
    min_seq = now_seq - SWING_LOOKBACK_BARS + 1
    filt = [p for p in points if int(p["seq"]) >= min_seq]
    if len(filt) < TRENDLINE_MIN_POINTS:
        return None
    xy = [(int(p["seq"]), float(p["price"])) for p in filt]
    reg = fit_regression(xy)
    if not reg:
        return None
    if kind == "up" and reg["slope"] <= 0:
        return None
    if kind == "down" and reg["slope"] >= 0:
        return None
    if reg["r2"] < TRENDLINE_MIN_R2:
        return None
    return {
        "slope": reg["slope"],
        "intercept": reg["intercept"],
        "r2": reg["r2"],
        "created_seq": now_seq,
        "last_swing_price": float(filt[-1]["price"]),
        "points": filt,
    }


def rebuild_lines(symbol_upper: str) -> None:
    s = symbol_data[symbol_upper]
    now_seq = int(s["last_seq"])
    swings = swing_points.get(symbol_upper, {"highs": [], "lows": []})
    t = trendlines.setdefault(symbol_upper, {"up": None, "down": None})
    if t.get("up") is None:
        t["up"] = _line_from_swings(swings.get("highs", []), "up", now_seq)
    if t.get("down") is None:
        t["down"] = _line_from_swings(swings.get("lows", []), "down", now_seq)


def update_swing_and_invalidation(symbol_upper: str) -> None:
    s = symbol_data[symbol_upper]
    d = detect_confirmed_swing(
        list(s["high"]),
        list(s["low"]),
        list(s["seq"]),
        SWING_LEFT_BARS,
        SWING_RIGHT_BARS,
    )
    if not d:
        return
    seq = int(d["seq"])
    sp = swing_points.setdefault(symbol_upper, {"highs": [], "lows": []})
    t = trendlines.setdefault(symbol_upper, {"up": None, "down": None})

    if "swing_high" in d:
        hs = sp["highs"]
        if not hs or int(hs[-1]["seq"]) != seq:
            hs.append({"seq": float(seq), "price": float(d["swing_high"])})
            up = t.get("up")
            if up and float(d["swing_high"]) < float(up["last_swing_price"]):
                t["up"] = None
    if "swing_low" in d:
        ls = sp["lows"]
        if not ls or int(ls[-1]["seq"]) != seq:
            ls.append({"seq": float(seq), "price": float(d["swing_low"])})
            dn = t.get("down")
            if dn and float(d["swing_low"]) > float(dn["last_swing_price"]):
                t["down"] = None
    rebuild_lines(symbol_upper)


def merge_universe_with_positions(symbols: List[str]) -> List[str]:
    out = set(s.lower() for s in symbols)
    for p in get_open_positions():
        sym = str(p.get("symbol", "")).lower()
        if sym:
            out.add(sym)
    return sorted(out)


def select_universe() -> List[str]:
    return api_retry(get_top_usdt_perpet_by_quote_volume, UNIVERSE_TOP_N, ("BTCUSDT", "ETHUSDT")) or []


def execute_pending_if_due(symbol_upper: str, bar_open_ms: int) -> None:
    pe = pending_entries.get(symbol_upper)
    if not pe:
        return
    if int(pe.get("next_open_ms", 0)) > bar_open_ms:
        return
    if symbol_upper in active_positions or len(active_positions) >= MAX_CONCURRENT_POSITIONS:
        pending_entries.pop(symbol_upper, None)
        persist_runtime_state()
        return
    direction = str(pe["direction"])
    try:
        mark = float(get_mark_price(symbol_upper))
        eq = float(get_account_equity_usdt())
    except Exception:
        return
    margin_usdt = eq * float(POSITION_RISK_PCT)
    lev = set_isolated_and_leverage(symbol_upper)
    if lev is None:
        pending_entries.pop(symbol_upper, None)
        persist_runtime_state()
        return
    qty_res = calculate_quantity(symbol_upper, margin_usdt, mark, int(lev))
    if not qty_res:
        pending_entries.pop(symbol_upper, None)
        persist_runtime_state()
        return
    qty, _ = qty_res
    opened = open_position_market(symbol_upper.lower(), direction, qty)
    if not opened:
        pending_entries.pop(symbol_upper, None)
        persist_runtime_state()
        tg.send_message(f"❌ 진입 실패 #{symbol_upper}")
        return

    pos = {
        "symbol": symbol_upper,
        "direction": direction,
        "entry_price": float(opened["entry_price"]),
        "quantity": float(opened["quantity"]),
        "initial_quantity": float(opened["quantity"]),
        "entry_kind": str(pe["entry_kind"]),
        "partial_taken": False,
        "fake_ref_high": float(pe.get("fake_ref_high", 0)),
        "fake_ref_low": float(pe.get("fake_ref_low", 0)),
    }
    active_positions[symbol_upper] = pos
    pending_entries.pop(symbol_upper, None)
    persist_runtime_state()
    tg.send_message(
        f"🟢 진입 {direction.upper()} ({pos['entry_kind']})\n"
        f"#{symbol_upper}\n"
        f"진입가: {_fmt(pos['entry_price'])}\n"
        f"수량: {pos['quantity']}\n"
        f'<a href="{_binance_link(symbol_upper)}">Binance</a>'
    )


def close_full_position(symbol_upper: str, pos: Dict[str, Any], reason: str, mark: float) -> None:
    qty = float(pos["quantity"])
    direction = str(pos["direction"])
    if qty <= 0:
        return
    close_position_market(symbol_upper.lower(), direction, qty)
    entry = float(pos["entry_price"])
    pnl_pct = ((mark - entry) / entry * 100.0) if direction == "long" else ((entry - mark) / entry * 100.0)
    active_positions.pop(symbol_upper, None)
    st = load_state()
    remove_position(st, symbol_upper)
    st["pending_entries"] = pending_entries
    st["trendlines"] = trendlines
    st["swing_points"] = swing_points
    save_state(st)
    tg.send_message(
        f"🏁 청산 ({reason})\n#{symbol_upper} {direction.upper()}\n"
        f"청산가(마크): {_fmt(mark)}\n손익: {pnl_pct:+.2f}%\n"
        f'<a href="{_binance_link(symbol_upper)}">Binance</a>'
    )


def process_position_on_closed_bar(symbol_upper: str, close_price: float, prev_close: float, seq: int) -> None:
    pos = active_positions.get(symbol_upper)
    if not pos:
        return
    direction = str(pos["direction"])
    kind = str(pos["entry_kind"])
    t = trendlines.get(symbol_upper, {"up": None, "down": None})
    if kind == "breakout":
        if direction == "long" and t.get("up"):
            cur_line = line_value(float(t["up"]["slope"]), float(t["up"]["intercept"]), seq)
            if close_price < cur_line:
                mark = float(get_mark_price(symbol_upper))
                close_full_position(symbol_upper, pos, "추세선 이탈", mark)
        elif direction == "short" and t.get("down"):
            cur_line = line_value(float(t["down"]["slope"]), float(t["down"]["intercept"]), seq)
            if close_price > cur_line:
                mark = float(get_mark_price(symbol_upper))
                close_full_position(symbol_upper, pos, "추세선 이탈", mark)
    _ = prev_close


def process_closed_bar_signal(symbol_upper: str) -> None:
    if symbol_upper in active_positions or symbol_upper in pending_entries:
        return
    if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
        return
    s = symbol_data[symbol_upper]
    if len(s["close"]) < max(3, VOLUME_AVG_PERIOD + 1):
        return
    seq_now = int(s["last_seq"])
    prev_seq = seq_now - 1
    c_prev = float(s["close"][-2])
    c_now = float(s["close"][-1])
    h_now = float(s["high"][-1])
    l_now = float(s["low"][-1])
    v_now = float(s["volume"][-1])
    avg_vol = sum(list(s["volume"])[-VOLUME_AVG_PERIOD - 1 : -1]) / float(VOLUME_AVG_PERIOD)

    t = trendlines.get(symbol_upper, {"up": None, "down": None})
    up = t.get("up")
    dn = t.get("down")
    next_open_ms = int(s["last_close_time"]) + 1

    if up:
        up_prev = line_value(float(up["slope"]), float(up["intercept"]), prev_seq)
        up_now = line_value(float(up["slope"]), float(up["intercept"]), seq_now)
        crossed_up = c_prev < up_prev and c_now > up_now and v_now >= avg_vol
        fake_short = h_now >= up_now and c_now < up_now
        if crossed_up:
            pending_entries[symbol_upper] = {"direction": "long", "entry_kind": "breakout", "next_open_ms": next_open_ms}
            persist_runtime_state()
            return
        if fake_short:
            pending_entries[symbol_upper] = {
                "direction": "short",
                "entry_kind": "fakeout",
                "next_open_ms": next_open_ms,
                "fake_ref_high": h_now,
            }
            persist_runtime_state()
            return
    if dn:
        dn_prev = line_value(float(dn["slope"]), float(dn["intercept"]), prev_seq)
        dn_now = line_value(float(dn["slope"]), float(dn["intercept"]), seq_now)
        crossed_dn = c_prev > dn_prev and c_now < dn_now and v_now >= avg_vol
        fake_long = l_now <= dn_now and c_now > dn_now
        if crossed_dn:
            pending_entries[symbol_upper] = {"direction": "short", "entry_kind": "breakout", "next_open_ms": next_open_ms}
            persist_runtime_state()
            return
        if fake_long:
            pending_entries[symbol_upper] = {
                "direction": "long",
                "entry_kind": "fakeout",
                "next_open_ms": next_open_ms,
                "fake_ref_low": l_now,
            }
            persist_runtime_state()


def process_kline(symbol_lower: str, k: Dict[str, Any]) -> None:
    symbol_upper = symbol_lower.upper()
    if symbol_lower not in tracked_symbols:
        return
    if symbol_upper not in symbol_data:
        bootstrap_symbol(symbol_upper)
    s = symbol_data[symbol_upper]
    bar_open_ms = int(k.get("t", 0))
    execute_pending_if_due(symbol_upper, bar_open_ms)
    if not k.get("x"):
        return
    bar_close_ms = int(k.get("T", 0))
    if bar_close_ms <= int(s["last_close_time"]):
        return
    s["last_seq"] = int(s["last_seq"]) + 1
    seq = int(s["last_seq"])
    s["seq"].append(seq)
    s["open"].append(float(k["o"]))
    s["high"].append(float(k["h"]))
    s["low"].append(float(k["l"]))
    s["close"].append(float(k["c"]))
    s["volume"].append(float(k["v"]))
    s["last_close_time"] = bar_close_ms
    update_swing_and_invalidation(symbol_upper)
    prev_close = float(s["close"][-2]) if len(s["close"]) >= 2 else float(s["close"][-1])
    process_position_on_closed_bar(symbol_upper, float(s["close"][-1]), prev_close, seq)
    process_closed_bar_signal(symbol_upper)


def mark_monitor_loop() -> None:
    while not shutdown_event.is_set():
        time.sleep(MARK_POLL_INTERVAL_SEC)
        try:
            snap = list(active_positions.items())
            for sym, pos in snap:
                mark = float(get_mark_price(sym))
                direction = str(pos["direction"])
                entry = float(pos["entry_price"])
                qty = float(pos["quantity"])
                if qty <= 0:
                    continue
                if pos["entry_kind"] == "breakout":
                    if not bool(pos.get("partial_taken")):
                        hit = mark >= entry * (1.0 + BREAKOUT_TP_PCT) if direction == "long" else mark <= entry * (1.0 - BREAKOUT_TP_PCT)
                        if hit:
                            close_qty = _round_order_qty(sym, qty * BREAKOUT_TP_CLOSE_RATIO)
                            if close_qty <= 0.0 or close_qty >= qty:
                                close_full_position(sym, pos, "1차 익절 수량 보정(전량)", mark)
                                continue
                            close_position_market(sym.lower(), direction, close_qty)
                            pos["quantity"] = _round_order_qty(sym, max(0.0, qty - close_qty))
                            pos["partial_taken"] = True
                            active_positions[sym] = pos
                            persist_runtime_state()
                            tg.send_message(
                                f"✅ 1차 익절 40% #{sym} ({direction.upper()})\n"
                                f"익절수량: {close_qty}\n마크: {_fmt(mark)}"
                            )
                else:
                    tp_hit = mark >= entry * (1.0 + FAKEOUT_TP_PCT) if direction == "long" else mark <= entry * (1.0 - FAKEOUT_TP_PCT)
                    if tp_hit:
                        close_full_position(sym, pos, "거짓돌파 TP +1%", mark)
                        continue
                    if direction == "short" and mark > float(pos.get("fake_ref_high", 0)):
                        close_full_position(sym, pos, "거짓돌파 기준 고가 상향", mark)
                        continue
                    if direction == "long" and mark < float(pos.get("fake_ref_low", 0)):
                        close_full_position(sym, pos, "거짓돌파 기준 저가 하향", mark)
                        continue
        except Exception as e:
            logger.exception("mark_monitor: %s", e)
            tg.send_message(f"❌ 마크 감시 오류\n{e!s}")


def recover_positions() -> None:
    st = load_state()
    saved = st.get("positions", {})
    exch = get_open_positions()
    exch_syms = {p["symbol"] for p in exch}
    for p in exch:
        sym = p["symbol"]
        direction = p["direction"]
        entry = float(p["entry_price"])
        qty = float(p["amount"])
        base = dict(saved.get(sym, {}))
        base["symbol"] = sym
        base["direction"] = direction
        base["entry_price"] = entry
        base["quantity"] = qty
        base.setdefault("initial_quantity", qty)
        base.setdefault("entry_kind", "breakout")
        base.setdefault("partial_taken", False)
        base.setdefault("fake_ref_high", 0.0)
        base.setdefault("fake_ref_low", 0.0)
        active_positions[sym] = base
        upsert_position(st, sym, base)
    for sym in list(saved.keys()):
        if sym not in exch_syms:
            remove_position(st, sym)
    save_state(st)


def cmd_status() -> None:
    eq = get_account_equity_usdt()
    up_lines = sum(1 for v in trendlines.values() if v.get("up"))
    dn_lines = sum(1 for v in trendlines.values() if v.get("down"))
    tg.send_message(
        f"📊 Trendline Bot\n"
        f"잔고: {eq:.2f} USDT\n"
        f"포지션: {len(active_positions)}/{MAX_CONCURRENT_POSITIONS}\n"
        f"감시: {len(tracked_symbols)} 심볼\n"
        f"상승선: {up_lines} / 하락선: {dn_lines}\n"
        f"대기진입: {len(pending_entries)}"
    )


def cmd_stop() -> None:
    tg.send_message("🛑 /stop 수신: 프로세스 종료 시작")
    shutdown_event.set()


def _on_message(ws, message: str) -> None:
    try:
        payload = json.loads(message)
        data = payload.get("data") if "data" in payload else payload
        if data.get("e") != "kline":
            return
        k = data.get("k", {})
        s = data.get("s", "").lower()
        with state_lock:
            process_kline(s, k)
    except Exception as e:
        logger.error("WS: %s", e)


def _on_error(ws, err):
    logger.error("WS err: %s", err)


def _on_close(ws, code, msg):
    logger.warning("WS close %s %s", code, msg)


def _on_open(ws):
    logger.info("WS connected")


def _run_ws(url: str, idx: int) -> None:
    delay = 3.0
    while not shutdown_event.is_set():
        try:
            ws = websocket.WebSocketApp(url, on_message=_on_message, on_error=_on_error, on_close=_on_close, on_open=_on_open)
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
    streams = [f"{s}@kline_1m" for s in new]
    for i in range(0, len(streams), STREAM_BATCH_SIZE):
        batch = streams[i : i + STREAM_BATCH_SIZE]
        url = "wss://fstream.binance.com/stream?streams=" + "/".join(batch)
        idx = i // STREAM_BATCH_SIZE + 1
        threading.Thread(target=_run_ws, args=(url, idx), daemon=True).start()
        time.sleep(0.2)
    streamed_symbols |= set(new)


def universe_refresh_loop() -> None:
    while not shutdown_event.is_set():
        time.sleep(max(60.0, float(SYMBOL_REFRESH_INTERVAL)))
        try:
            uni = select_universe()
            if not uni:
                continue
            merged = merge_universe_with_positions(uni)
            tracked_symbols[:] = merged
            for s in merged:
                su = s.upper()
                if su not in symbol_data:
                    bootstrap_symbol(su)
            start_websockets(merged)
            tg.send_message(f"🔄 유니버스 갱신 ({SYMBOL_REFRESH_INTERVAL}s)\n대상 {len(merged)}개")
        except Exception as e:
            logger.exception("universe_refresh: %s", e)
            tg.send_message(f"❌ 유니버스 갱신 오류\n{e!s}")


def main() -> None:
    setup_logging()
    validate_secrets()
    check_single_instance()

    tg.register_command("status", cmd_status)
    tg.register_command("stop", cmd_stop)
    tg.start_polling()

    restore_runtime_state()
    recover_positions()

    uni = select_universe()
    if not uni:
        tg.send_message("❌ 유니버스 조회 실패")
        remove_lock()
        return
    tracked_symbols[:] = merge_universe_with_positions(uni)
    for s in tracked_symbols:
        bootstrap_symbol(s.upper())

    persist_runtime_state()

    tg.send_message(
        f"🚀 Trendline 봇 시작\n"
        f"1분봉 · 레버 {DEFAULT_LEVERAGE}x · 진입 {POSITION_RISK_PCT*100:.0f}% · 최대 {MAX_CONCURRENT_POSITIONS}포지션\n"
        f"스윙: 좌우 {SWING_LEFT_BARS}/{SWING_RIGHT_BARS} · 추세선 최소 {TRENDLINE_MIN_POINTS}점 · R²≥{TRENDLINE_MIN_R2}\n"
        f"감시 심볼: {len(tracked_symbols)} (BTC/ETH 제외)"
    )

    threading.Thread(target=mark_monitor_loop, daemon=True).start()
    threading.Thread(target=universe_refresh_loop, daemon=True).start()
    start_websockets(tracked_symbols)

    try:
        while not shutdown_event.is_set():
            time.sleep(1)
    finally:
        try:
            tg.send_message("🛑 봇 종료")
        except Exception:
            pass
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
