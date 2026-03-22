"""
RSI 다이버전스 역추세 — 바이낸스 USDT 선물 자동매매.

실행: python3 -u bot.py  (또는 main.py가 이 모듈을 호출)

전략·유니버스·TP/SL 규격은 STRATEGY_RSI_DIV.md 참고.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import websocket

from binance_client import (
    calculate_quantity,
    cancel_all_open_orders,
    close_position_market,
    get_account_equity_usdt,
    get_klines,
    get_mark_price,
    get_open_orders,
    get_open_positions,
    get_top_usdt_perpet_by_quote_volume,
    open_position_market,
    place_reduce_only_stop_market,
    place_take_profit_market,
    round_price_to_tick,
    set_isolated_and_leverage,
)
from config import (
    BREAKOUT_LOOKBACK_BARS,
    DEFAULT_LEVERAGE,
    DIV_WINDOW_BARS,
    ENABLE_SERVER_STOP,
    LOCK_FILE,
    LOG_FILE,
    MAX_CONCURRENT_POSITIONS,
    POSITION_RISK_PCT,
    RSI_DIV_PERIOD,
    RSI_LONG_TRIGGER,
    RSI_SHORT_TRIGGER,
    SL_ATR_MULT,
    STREAM_BATCH_SIZE,
    TP_ATR_MULT,
    UNIVERSE_TOP_VOLUME,
    validate_secrets,
    WEBSOCKET_PING_INTERVAL,
    WEBSOCKET_PING_TIMEOUT,
    WS_DISCONNECT_TELEGRAM_COOLDOWN_SEC,
)
from rsi_div_strategy import RsiAtrComputer, RsiDivergenceMachine
import telegram_client as tg
from state_manager import load_state, remove_position, save_state, upsert_position

logger = logging.getLogger("bot")

KST = timezone(timedelta(hours=9))

state_lock = threading.Lock()
bot_active = True

tracked_symbols: List[str] = []
streamed_symbols: Set[str] = set()

computer_map: Dict[str, RsiAtrComputer] = {}
machine_map: Dict[str, RsiDivergenceMachine] = {}
closes_map: Dict[str, deque] = {}
bar_index_map: Dict[str, int] = {}

pending_entry: Dict[str, Dict[str, Any]] = {}
latest_price_map: Dict[str, float] = {}
latest_ws_price_ts: Dict[str, float] = {}
last_mark_map: Dict[str, float] = {}

active_positions: Dict[str, Dict[str, Any]] = {}

_last_ws_disconnect_notify_ts = 0.0
_ws_disconnect_notify_lock = threading.Lock()


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


def _retry_call(fn, *args, **kwargs):
    delay = 2.0
    for _ in range(5):
        try:
            r = fn(*args, **kwargs)
            if r is not None:
                return r
        except Exception:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 20.0)
    return None


def _binance_link(symbol_upper: str) -> str:
    return f"https://www.binance.com/en/futures/{symbol_upper}"


def _fmt_price(v: float) -> str:
    return f"{v:.6f}"


def _bootstrap_symbol(symbol_upper: str) -> None:
    comp = RsiAtrComputer(rsi_period=RSI_DIV_PERIOD, atr_period=RSI_DIV_PERIOD)
    machine_map[symbol_upper] = RsiDivergenceMachine(
        short_trigger_rsi=RSI_SHORT_TRIGGER,
        long_trigger_rsi=RSI_LONG_TRIGGER,
        div_window=DIV_WINDOW_BARS,
        breakout_lookback=BREAKOUT_LOOKBACK_BARS,
    )
    closes_map[symbol_upper] = deque(maxlen=400)
    bar_index_map[symbol_upper] = 0
    klines = _retry_call(get_klines, symbol_upper, "1m", 500)
    if not klines:
        computer_map[symbol_upper] = comp
        return
    for k in klines:
        o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        comp.update(h, l, c)
        closes_map[symbol_upper].append(c)
    computer_map[symbol_upper] = comp
    bar_index_map[symbol_upper] = len(klines)


def select_universe() -> List[str]:
    return _retry_call(get_top_usdt_perpet_by_quote_volume, UNIVERSE_TOP_VOLUME) or []


def merge_universe_with_positions(symbols: List[str]) -> List[str]:
    """열린 포지션 심볼은 유니버스에서 빠져도 스트림 유지."""
    u: Set[str] = set(s.lower() for s in symbols)
    for p in get_open_positions():
        sym = str(p.get("symbol", "")).lower()
        if sym:
            u.add(sym)
    return sorted(u)


def _orders_have_tp_sl(symbol_upper: str, _direction: str) -> bool:
    orders = get_open_orders(symbol_upper)
    has_stop = False
    has_tp = False
    for o in orders:
        ot = str(o.get("type", "")).upper()
        ro = o.get("reduceOnly")
        ro_ok = ro is True or str(ro).lower() == "true"
        if not ro_ok:
            continue
        if ot == "STOP_MARKET":
            has_stop = True
        if ot == "TAKE_PROFIT_MARKET":
            has_tp = True
    return has_stop and has_tp


def ensure_exchange_tp_sl(symbol_upper: str, pos: Dict[str, Any]) -> bool:
    if not ENABLE_SERVER_STOP:
        return False
    if pos.get("server_tp_sl_unsupported"):
        return False
    direction = str(pos["direction"])
    qty = float(pos.get("quantity", 0))
    sl_p = float(pos.get("sl_price", 0))
    tp_p = float(pos.get("tp_price", 0))
    if qty <= 0 or sl_p <= 0 or tp_p <= 0:
        return False

    if _orders_have_tp_sl(symbol_upper, direction):
        return True

    try:
        cancel_all_open_orders(symbol_upper)
    except Exception:
        pass

    sl = place_reduce_only_stop_market(symbol_upper, direction, sl_p, qty)
    tp = place_take_profit_market(symbol_upper, direction, tp_p, qty)
    ok = sl is not None and tp is not None
    if not ok:
        pos["server_tp_sl_unsupported"] = True
        tg.send_message(
            f"⚠️ TP/SL 주문 실패 #{symbol_upper}\n"
            f"거래소 모드 확인 또는 ENABLE_SERVER_STOP=0 후 수동 관리"
        )
        return False
    try:
        pos["sl_order_id"] = int(sl.get("orderId")) if sl else None
        pos["tp_order_id"] = int(tp.get("orderId")) if tp else None
    except Exception:
        pass
    return True


def open_trade(symbol_upper: str, direction: str, atr_val: float) -> None:
    if not bot_active:
        return
    mark = latest_price_map.get(symbol_upper) or float(get_mark_price(symbol_upper))
    margin_usdt = get_account_equity_usdt() * float(POSITION_RISK_PCT)
    lev = set_isolated_and_leverage(symbol_upper)
    if lev is None:
        return
    qty_res = calculate_quantity(symbol_upper, margin_usdt, mark, int(lev))
    if not qty_res:
        return
    qty, _ = qty_res

    with state_lock:
        if symbol_upper in active_positions:
            return
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
            return

    res = open_position_market(symbol_upper.lower(), direction, qty)
    if not res:
        return

    entry = float(res["entry_price"])
    if direction == "long":
        sl_price = entry - SL_ATR_MULT * atr_val
        tp_price = entry + TP_ATR_MULT * atr_val
    else:
        sl_price = entry + SL_ATR_MULT * atr_val
        tp_price = entry - TP_ATR_MULT * atr_val

    sl_price = round_price_to_tick(symbol_upper, sl_price)
    tp_price = round_price_to_tick(symbol_upper, tp_price)

    pos = {
        "symbol": symbol_upper,
        "direction": direction,
        "entry_price": entry,
        "quantity": float(res["quantity"]),
        "atr": float(atr_val),
        "sl_price": sl_price,
        "tp_price": tp_price,
        "sl_order_id": None,
        "tp_order_id": None,
        "server_tp_sl_unsupported": False,
    }

    with state_lock:
        active_positions[symbol_upper] = pos
        st = load_state()
        upsert_position(st, symbol_upper, pos)
        save_state(st)
        if symbol_upper in machine_map:
            machine_map[symbol_upper].reset()
        pending_entry.pop(symbol_upper, None)

    ensure_exchange_tp_sl(symbol_upper, pos)
    with state_lock:
        st = load_state()
        upsert_position(st, symbol_upper, active_positions[symbol_upper])
        save_state(st)

    side = "LONG" if direction == "long" else "SHORT"
    tg.send_message(
        f"🟢 진입 ({side})\n"
        f"#{symbol_upper}\n"
        f"진입가: {_fmt_price(entry)}\n"
        f"ATR: {_fmt_price(atr_val)}\n"
        f"TP: {_fmt_price(tp_price)}\n"
        f"SL: {_fmt_price(sl_price)}\n"
        f'<a href="{_binance_link(symbol_upper)}">Binance</a>'
    )
    logger.info("ENTRY %s %s entry=%s tp=%s sl=%s", symbol_upper, direction, entry, tp_price, sl_price)


def try_fire_pending(symbol_upper: str, k: Dict[str, Any]) -> None:
    pe = pending_entry.get(symbol_upper)
    if not pe or pe.get("done"):
        return
    t_open = int(k.get("t", 0))
    if t_open < int(pe["next_bar_open_ms"]):
        return
    pe["done"] = True
    side = str(pe["side"])
    atr_v = float(pe["atr"])
    open_trade(symbol_upper, side, atr_v)


def process_kline(symbol_lower: str, k: Dict[str, Any]) -> None:
    global bot_active
    if not bot_active:
        return

    symbol_upper = symbol_lower.upper()
    if symbol_lower not in tracked_symbols:
        return

    if "c" in k:
        try:
            latest_price_map[symbol_upper] = float(k["c"])
            latest_ws_price_ts[symbol_upper] = time.time()
            last_mark_map[symbol_upper] = float(k["c"])
        except Exception:
            pass

    try_fire_pending(symbol_upper, k)

    if not k.get("x"):
        return

    comp = computer_map.get(symbol_upper)
    mach = machine_map.get(symbol_upper)
    dq = closes_map.get(symbol_upper)
    if comp is None or mach is None or dq is None:
        return

    o = float(k["o"])
    h, l, c = float(k["h"]), float(k["l"]), float(k["c"])
    _ = o
    vals = comp.update(h, l, c)
    dq.append(c)
    rsi = vals.get("rsi")
    atr = vals.get("atr")

    idx = int(bar_index_map.get(symbol_upper, 0)) + 1
    bar_index_map[symbol_upper] = idx

    with state_lock:
        if symbol_upper in active_positions:
            return

    sched, events = mach.on_closed_bar(idx, c, rsi, atr, dq)
    for ev in events:
        logger.info("%s %s", symbol_upper, ev)

    if not sched:
        return

    with state_lock:
        if symbol_upper in active_positions:
            return
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
            return

    side = str(sched["side"])
    atr_sig = float(sched["atr"])
    next_open_ms = int(k["T"]) + 1
    pending_entry[symbol_upper] = {
        "side": side,
        "atr": atr_sig,
        "next_bar_open_ms": next_open_ms,
        "done": False,
    }
    logger.info(
        "PENDING %s %s next_open_ms=%s atr=%s",
        symbol_upper,
        side,
        next_open_ms,
        atr_sig,
    )


def reconcile_and_detect_exits() -> None:
    global active_positions
    exch = {p["symbol"] for p in get_open_positions()}
    with state_lock:
        snapshot = dict(active_positions)

    for sym, pos in snapshot.items():
        if sym in exch:
            mp = float(get_mark_price(sym))
            last_mark_map[sym] = mp
            ensure_exchange_tp_sl(sym, pos)
            with state_lock:
                if sym in active_positions:
                    active_positions[sym].update(pos)
                    st = load_state()
                    upsert_position(st, sym, active_positions[sym])
                    save_state(st)
            continue

        entry = float(pos.get("entry_price", 0))
        direction = str(pos.get("direction", "long"))
        mark = float(last_mark_map.get(sym, entry))
        if entry > 0:
            if direction == "long":
                pnl_pct = (mark - entry) / entry * 100.0
            else:
                pnl_pct = (entry - mark) / entry * 100.0
        else:
            pnl_pct = 0.0

        with state_lock:
            active_positions.pop(sym, None)
            st = load_state()
            remove_position(st, sym)
            save_state(st)

        tg.send_message(
            f"📭 청산 감지\n"
            f"#{sym} {direction.upper()}\n"
            f"진입: {_fmt_price(entry)}\n"
            f"기준가(마지막마크): {_fmt_price(mark)}\n"
            f"추정 손익: {pnl_pct:+.2f}%\n"
            f"(거래소 TP/SL·수동 청산)\n"
            f'<a href="{_binance_link(sym)}">Binance</a>'
        )


def monitor_loop() -> None:
    while True:
        time.sleep(3)
        try:
            reconcile_and_detect_exits()
        except Exception as e:
            logger.exception("monitor_loop: %s", e)
            try:
                tg.send_message(f"❌ 모니터 오류\n{e!s}")
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
        atr_guess = entry * 0.002
        if sym in saved:
            pos = saved[sym]
            pos["quantity"] = qty
            pos["entry_price"] = entry
            pos["direction"] = direction
        else:
            if direction == "long":
                sl_p = entry - SL_ATR_MULT * atr_guess
                tp_p = entry + TP_ATR_MULT * atr_guess
            else:
                sl_p = entry + SL_ATR_MULT * atr_guess
                tp_p = entry - TP_ATR_MULT * atr_guess
            pos = {
                "symbol": sym,
                "direction": direction,
                "entry_price": entry,
                "quantity": qty,
                "atr": atr_guess,
                "sl_price": round_price_to_tick(sym, sl_p),
                "tp_price": round_price_to_tick(sym, tp_p),
            }
        active_positions[sym] = pos
        ensure_exchange_tp_sl(sym, pos)
        upsert_position(st, sym, active_positions[sym])
    for s in list(saved.keys()):
        if s not in exch_syms:
            remove_position(st, s)
    save_state(st)


def cmd_status() -> None:
    reconcile_and_detect_exits()
    eq = get_account_equity_usdt()
    with state_lock:
        n = len(active_positions)
    tg.send_message(
        f"📊 RSI 다이버전스 봇\n"
        f"잔고: {eq:.2f} USDT\n"
        f"포지션: {n}/{MAX_CONCURRENT_POSITIONS}\n"
        f"감시 심볼: {len(tracked_symbols)}"
    )


def cmd_stop() -> None:
    global bot_active
    bot_active = False
    tg.send_message("⏸️ 신규 진입 중지 (포지션·TP/SL 유지)")


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
        logger.error("WS message error: %s", e)


def _on_error(ws, error) -> None:
    logger.error("WS error: %s", error)


def _on_close(ws, code, msg) -> None:
    global _last_ws_disconnect_notify_ts
    now = time.time()
    cd = float(WS_DISCONNECT_TELEGRAM_COOLDOWN_SEC)
    with _ws_disconnect_notify_lock:
        if cd > 0 and (now - _last_ws_disconnect_notify_ts) < cd:
            return
        _last_ws_disconnect_notify_ts = now
    tg.send_message(f"⚠️ WS 끊김 code={code}\n재연결 시도 중…")


def _on_open(ws) -> None:
    logger.info("Websocket connected")


def _run_ws_batch(stream_url: str, batch_index: int, n: int) -> None:
    delay = 3.0
    while True:
        try:
            ws = websocket.WebSocketApp(
                stream_url,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
                on_open=_on_open,
            )
            ws.run_forever(
                ping_interval=int(WEBSOCKET_PING_INTERVAL),
                ping_timeout=int(WEBSOCKET_PING_TIMEOUT),
            )
        except Exception as e:
            logger.error("WS batch-%s: %s", batch_index, e)
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
        bi = (i // STREAM_BATCH_SIZE) + 1
        threading.Thread(target=_run_ws_batch, args=(url, bi, len(batch)), daemon=True).start()
        time.sleep(0.3)
    streamed_symbols |= set(new)


def daily_universe_loop() -> None:
    global tracked_symbols
    while True:
        time.sleep(20)
        now = datetime.now(KST)
        if now.hour == 9 and now.minute == 0:
            old = set(tracked_symbols)
            uni = select_universe()
            if not uni:
                time.sleep(70)
                continue
            merged = merge_universe_with_positions(uni)
            added = set(merged) - old
            removed = old - set(merged)
            tracked_symbols[:] = merged
            for s in merged:
                su = s.upper()
                if su not in computer_map:
                    _bootstrap_symbol(su)
            start_websockets(list(merged))
            tg.send_message(
                f"🔄 유니버스 갱신 (KST 09:00)\n"
                f"총 {len(merged)}개\n"
                f"+{len(added)} / -{len(removed)}"
            )
            time.sleep(70)


def main() -> None:
    global tracked_symbols
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
        _bootstrap_symbol(s.upper())

    tg.send_message(
        f"🚀 RSI 다이버전스 봇 시작\n"
        f"감시: {len(tracked_symbols)}개 (거래대금 상위 {UNIVERSE_TOP_VOLUME})\n"
        f"레버리지: {DEFAULT_LEVERAGE}x ISOLATED\n"
        f"리스크: 잔고의 {POSITION_RISK_PCT*100:.1f}% / 포지션 최대 {MAX_CONCURRENT_POSITIONS}개"
    )

    threading.Thread(target=monitor_loop, daemon=True).start()
    threading.Thread(target=daily_universe_loop, daemon=True).start()
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
    main()
