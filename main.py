import logging
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import websocket

from binance_client import (
    calculate_quantity_by_risk,
    cancel_all_open_orders,
    cancel_order,
    close_position_market,
    get_account_equity_usdt,
    get_klines,
    get_open_orders,
    get_open_positions,
    get_top_usdt_symbols_by_quote_volume,
    open_position_market,
    place_reduce_only_stop_market,
)
from config import (
    ATR_PERIOD,
    CONFIRM_WITHIN_BARS,
    INITIAL_SL_ATR_MULT,
    LOCK_FILE,
    LOG_FILE,
    MAX_CONCURRENT_POSITIONS,
    POSITION_RISK_PCT,
    STREAM_BATCH_SIZE,
    TRAILING_ATR_MULT,
    validate_secrets,
)
from indicators import TrendIndicatorComputer
from state_manager import load_state, remove_position, save_state, upsert_position
from strategy import decide_entry_signal
import telegram_client as tg

logger = logging.getLogger("bot")
state_lock = threading.Lock()
bot_active = True

indicator_map: Dict[str, TrendIndicatorComputer] = {}
candidate_map: Dict[str, Dict[str, float | int | str]] = {}
bar_index_map: Dict[str, int] = {}
active_positions: Dict[str, Dict[str, Any]] = {}
tracked_symbols: List[str] = []
last_protection_sync_ts = 0.0
latest_price_map: Dict[str, float] = {}


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
    delay = 2
    for _ in range(5):
        try:
            result = fn(*args, **kwargs)
            if result is not None:
                return result
        except Exception:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 20)
    return None


def _bootstrap_symbol_indicators(symbol_upper: str) -> None:
    comp = TrendIndicatorComputer(ema_period=200, atr_period=ATR_PERIOD, adx_period=ATR_PERIOD)
    klines = _retry_call(get_klines, symbol_upper, "1m", 500)
    if not klines:
        indicator_map[symbol_upper] = comp
        bar_index_map[symbol_upper] = 0
        return
    for k in klines:
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])
        comp.update(high, low, close)
    indicator_map[symbol_upper] = comp
    bar_index_map[symbol_upper] = len(klines)


def _select_daily_symbols() -> List[str]:
    symbols = _retry_call(get_top_usdt_symbols_by_quote_volume, 20, 1.0)
    return symbols or []


def _build_position_state(symbol_upper: str, direction: str, entry_price: float, quantity: float, atr_used: float) -> Dict[str, Any]:
    if direction == "long":
        initial_sl = entry_price - (atr_used * INITIAL_SL_ATR_MULT)
    else:
        initial_sl = entry_price + (atr_used * INITIAL_SL_ATR_MULT)
    return {
        "symbol": symbol_upper,
        "direction": direction,
        "entry_price": float(entry_price),
        "quantity": float(quantity),
        "atr_used": float(atr_used),
        "initial_sl": float(initial_sl),
        "trail_sl": float(initial_sl),
        "highest": float(entry_price),
        "lowest": float(entry_price),
        "opened_at": int(time.time()),
        "server_stop_ok": False,
        "server_stop_order_id": None,
        "server_stop_price": float(initial_sl),
    }


def _binance_link(symbol_upper: str) -> str:
    return f"https://www.binance.com/en/futures/{symbol_upper}"


def _fmt_price(value: float) -> str:
    return f"{value:.4f}"


def _ensure_server_stop_for_position(symbol_upper: str, pos: Dict[str, Any], force_replace: bool = False) -> bool:
    direction = str(pos["direction"])
    stop_price = float(pos.get("server_stop_price", pos["initial_sl"]))
    existing_stop_order = None
    orders = get_open_orders(symbol_upper)
    for o in orders:
        if o.get("type") == "STOP_MARKET" and str(o.get("closePosition", "")).lower() == "true":
            existing_stop_order = o
            break

    if existing_stop_order and not force_replace:
        pos["server_stop_ok"] = True
        try:
            pos["server_stop_order_id"] = int(existing_stop_order.get("orderId"))
        except Exception:
            pos["server_stop_order_id"] = None
        return True

    if existing_stop_order:
        try:
            cancel_order(symbol_upper, int(existing_stop_order.get("orderId")))
        except Exception:
            pass

    placed = place_reduce_only_stop_market(symbol_upper, direction, stop_price)
    ok = placed is not None
    pos["server_stop_ok"] = ok
    if ok:
        try:
            pos["server_stop_order_id"] = int(placed.get("orderId"))
        except Exception:
            pos["server_stop_order_id"] = None
        pos["server_stop_price"] = stop_price
    return ok


def _open_trade(symbol_upper: str, signal: Dict[str, Any]) -> None:
    direction = str(signal["direction"])
    atr_used = float(signal["atr_used"])
    entry_mark = latest_price_map.get(symbol_upper)
    if entry_mark is None:
        return
    if not entry_mark:
        return
    stop_distance = atr_used * INITIAL_SL_ATR_MULT
    risk_usdt = get_account_equity_usdt() * POSITION_RISK_PCT
    qty_result = calculate_quantity_by_risk(symbol_upper, risk_usdt, stop_distance)
    if not qty_result:
        return
    qty, _ = qty_result
    res = open_position_market(symbol_upper.lower(), direction, qty)
    if not res:
        return
    entry_price = float(res["entry_price"])
    pos_state = _build_position_state(symbol_upper, direction, entry_price, qty, atr_used)
    with state_lock:
        active_positions[symbol_upper] = pos_state
        st = load_state()
        upsert_position(st, symbol_upper, pos_state)
        save_state(st)
    _ensure_server_stop_for_position(symbol_upper, pos_state)
    side_icon = "🟢📈" if direction == "long" else "🔴📉"
    tg.send_message(
        f"{side_icon} 진입\n"
        f"코인: #{symbol_upper}\n"
        f"방향: {direction.upper()}\n"
        f"진입가: {entry_price:.6f}\n"
        f"손절가: {float(pos_state['initial_sl']):.6f}\n"
        f"ATR: {atr_used:.6f}\n\n"
        f"<a href=\"{_binance_link(symbol_upper)}\">Binance</a>"
    )


def process_kline(symbol_lower: str, kline: Dict[str, Any]) -> None:
    global bot_active
    if not bot_active:
        return
    if not kline.get("x"):
        return

    symbol_upper = symbol_lower.upper()
    if symbol_lower not in tracked_symbols:
        return
    comp = indicator_map.get(symbol_upper)
    if comp is None:
        return

    high = float(kline["h"])
    low = float(kline["l"])
    close = float(kline["c"])
    idx = bar_index_map.get(symbol_upper, 0) + 1
    bar_index_map[symbol_upper] = idx
    values = comp.update(high, low, close)

    prev_candidate = candidate_map.get(symbol_upper)
    signal, next_candidate = decide_entry_signal(
        symbol_upper=symbol_upper,
        close_price=close,
        ema=values["ema"],
        atr=values["atr"],
        atr_ma30=values["atr_ma30"],
        adx=values["adx"],
        candidate=prev_candidate,
        bar_index=idx,
    )

    if next_candidate:
        # "진입 감지"는 대기중이면 1회만 보내고, 방향이 바뀔 때만 다시 보냄(스팸 방지).
        prev_direction = str(prev_candidate.get("direction", "")).lower() if prev_candidate else ""
        next_direction = str(next_candidate.get("direction", "")).lower()
        candidate_map[symbol_upper] = next_candidate
        should_notify = (prev_candidate is None) or (prev_direction != next_direction)
        if should_notify:
            direction = str(next_candidate["direction"]).upper()
            basis_close = float(next_candidate["basis_close"])
            tg.send_message(
                f"👀📌 진입 감지\n"
                f"코인: #{symbol_upper}\n"
                f"방향: {direction}\n"
                f"기준종가: {_fmt_price(basis_close)}\n"
                f"조건: EMA+ATR+ADX 통과\n"
                    f"다음: 확인 캔들(5캔들) 대기\n\n"
                f"<a href=\"{_binance_link(symbol_upper)}\">Binance</a>"
            )
    else:
        # If a previous basis existed but expired, send "entry skip".
        if prev_candidate and "basis_bar" in prev_candidate:
            expired = (idx - int(prev_candidate["basis_bar"])) > CONFIRM_WITHIN_BARS
            if expired:
                direction = str(prev_candidate["direction"]).upper()
                basis_close = float(prev_candidate["basis_close"])
                tg.send_message(
                    f"⛔ 진입 스킵\n"
                    f"코인: #{symbol_upper}\n"
                    f"방향: {direction}\n"
                    f"기준종가: {_fmt_price(basis_close)}\n"
                    f"사유: 확인 캔들 만료(5캔들)\n\n"
                    f"<a href=\"{_binance_link(symbol_upper)}\">Binance</a>"
                )

        if symbol_upper in candidate_map:
            candidate_map.pop(symbol_upper, None)

    if not signal:
        return

    with state_lock:
        if symbol_upper in active_positions:
            return
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
            return
    _open_trade(symbol_upper, signal)


def _close_and_cleanup(symbol_upper: str, pos: Dict[str, Any], reason: str, exit_price: float) -> None:
    direction = pos["direction"]
    qty = float(pos["quantity"])
    if not close_position_market(symbol_upper.lower(), direction, qty):
        return
    with state_lock:
        active_positions.pop(symbol_upper, None)
        st = load_state()
        remove_position(st, symbol_upper)
        save_state(st)
    entry = float(pos["entry_price"])
    pnl_pct = ((exit_price - entry) / entry) * 100.0 if direction == "long" else ((entry - exit_price) / entry) * 100.0
    exit_icon = "✅🟢" if pnl_pct >= 0 else "✅🔴"
    tg.send_message(
        f"{exit_icon} 청산 ({reason})\n"
        f"코인: #{symbol_upper}\n"
        f"방향: {direction.upper()}\n"
        f"진입가: {entry:.6f}\n"
        f"청산가: {exit_price:.6f}\n"
        f"손익: {pnl_pct:+.2f}%\n\n"
        f"<a href=\"{_binance_link(symbol_upper)}\">Binance</a>"
    )


def monitor_positions_loop() -> None:
    global last_protection_sync_ts
    while True:
        time.sleep(2)
        with state_lock:
            snapshot = dict(active_positions)
        for sym, pos in snapshot.items():
            try:
                mark = latest_price_map.get(sym)
                if mark is None:
                    continue
                direction = str(pos["direction"])
                atr_used = float(pos["atr_used"])
                if direction == "long":
                    pos["highest"] = max(float(pos["highest"]), mark)
                    trail = pos["highest"] - (atr_used * TRAILING_ATR_MULT)
                    pos["trail_sl"] = max(float(pos["trail_sl"]), trail)
                    stop_price = max(float(pos["initial_sl"]), float(pos["trail_sl"]))
                    if abs(stop_price - float(pos.get("server_stop_price", 0.0))) > 1e-9:
                        pos["server_stop_price"] = stop_price
                        _ensure_server_stop_for_position(sym, pos, force_replace=True)
                    if mark <= stop_price:
                        _close_and_cleanup(sym, pos, "SL/TRAIL", mark)
                else:
                    pos["lowest"] = min(float(pos["lowest"]), mark)
                    trail = pos["lowest"] + (atr_used * TRAILING_ATR_MULT)
                    pos["trail_sl"] = min(float(pos["trail_sl"]), trail)
                    stop_price = min(float(pos["initial_sl"]), float(pos["trail_sl"]))
                    if abs(stop_price - float(pos.get("server_stop_price", 0.0))) > 1e-9:
                        pos["server_stop_price"] = stop_price
                        _ensure_server_stop_for_position(sym, pos, force_replace=True)
                    if mark >= stop_price:
                        _close_and_cleanup(sym, pos, "SL/TRAIL", mark)
            except Exception as e:
                logger.error(f"Monitor error {sym}: {e}")

        now = time.time()
        if now - last_protection_sync_ts >= 15:
            last_protection_sync_ts = now
            with state_lock:
                st = load_state()
                for sym, pos in active_positions.items():
                    _ensure_server_stop_for_position(sym, pos)
                    upsert_position(st, sym, pos)
                save_state(st)


def recover_positions() -> None:
    st = load_state()
    saved = st.get("positions", {})
    exchange = get_open_positions()
    exchange_symbols = set()
    for p in exchange:
        sym = p["symbol"]
        exchange_symbols.add(sym)
        if sym in saved:
            active_positions[sym] = saved[sym]
            _ensure_server_stop_for_position(sym, active_positions[sym])
            continue
        entry = float(p["entry_price"])
        qty = float(p["amount"])
        direction = p["direction"]
        active_positions[sym] = _build_position_state(sym, direction, entry, qty, atr_used=entry * 0.003)
        _ensure_server_stop_for_position(sym, active_positions[sym])
        upsert_position(st, sym, active_positions[sym])
    for sym in list(saved.keys()):
        if sym not in exchange_symbols:
            remove_position(st, sym)
    save_state(st)


def cmd_status() -> None:
    with state_lock:
        positions = dict(active_positions)
        cnt = len(positions)
    eq = get_account_equity_usdt()
    bot_state = "작동중" if bot_active else "중지중"
    lines = [
        "📊 상태",
        f"잔고: {eq:.2f} USDT",
        f"봇상태: {bot_state}",
        f"포지션: {cnt}/{MAX_CONCURRENT_POSITIONS}",
        f"감시코인: {len(tracked_symbols)}개",
    ]

    shown = 0
    for sym, pos in positions.items():
        if shown >= 8:
            lines.append(f"... 외 {cnt - shown}개")
            break
        direction = str(pos.get("direction", "")).lower()
        side_icon = "🟢📈" if direction == "long" else "🔴📉"
        entry = float(pos.get("entry_price", 0.0))
        stop_price = float(pos.get("server_stop_price", pos.get("initial_sl", 0.0)))
        lines.append("")
        lines.append(f"{side_icon} #{sym}")
        lines.append(f"포지션: {direction.upper()}")
        lines.append(f"진입가: {_fmt_price(entry)}")
        lines.append(f"보호SL: {_fmt_price(stop_price)}")
        lines.append(f"<a href=\"{_binance_link(sym)}\">Binance</a>")
        shown += 1

    tg.send_message("\n".join(lines))


def cmd_stop() -> None:
    global bot_active
    bot_active = False
    tg.send_message("⏸️ 봇 중지\n신규 진입: OFF\n기존 포지션 보호주문: 유지")


def cmd_restart() -> None:
    global bot_active
    bot_active = True
    tg.send_message("▶️ 봇 재개\n신규 진입: ON\n전략 감시: 정상")


def cmd_closeall() -> None:
    global bot_active
    bot_active = False
    with state_lock:
        snapshot = dict(active_positions)
    exchange_positions = get_open_positions()
    for p in exchange_positions:
        if p["symbol"] not in snapshot:
            snapshot[p["symbol"]] = {
                "direction": p["direction"],
                "quantity": float(p["amount"]),
            }
    for sym in snapshot.keys():
        try:
            cancel_all_open_orders(sym)
        except Exception:
            pass
    tg.send_message("🟠 CLOSEALL 시작\n신규 진입: OFF\n미체결 주문 취소 후 시장가 청산")
    for sym, pos in snapshot.items():
        try:
            close_position_market(sym.lower(), pos["direction"], float(pos["quantity"]))
        except Exception:
            pass
    with state_lock:
        active_positions.clear()
        st = load_state()
        st["positions"] = {}
        save_state(st)
    tg.send_message("✅ CLOSEALL 완료\n전체 포지션: 0\n미체결 주문: 0")


def _on_message(ws, message: str) -> None:
    try:
        payload = json.loads(message)
        data = payload.get("data") if "data" in payload else payload
        if data.get("e") != "kline":
            return
        k = data.get("k", {})
        s = data.get("s", "").lower()
        if "c" in k:
            try:
                latest_price_map[s.upper()] = float(k["c"])
            except Exception:
                pass
        process_kline(s, k)
    except Exception as e:
        logger.error(f"WS message error: {e}")


def _on_error(ws, error) -> None:
    logger.error(f"WS error: {error}")


def _on_close(ws, code, msg) -> None:
    tg.send_message(f"⚠️ 연결 끊김\ncode={code}\nmsg={msg}")


def _on_open(ws) -> None:
    logger.info("Websocket connected")


def start_websockets(symbols_lower: List[str]) -> None:
    streams = [f"{s}@kline_1m" for s in symbols_lower]
    for i in range(0, len(streams), STREAM_BATCH_SIZE):
        batch = streams[i : i + STREAM_BATCH_SIZE]
        stream_url = "wss://fstream.binance.com/stream?streams=" + "/".join(batch)
        ws = websocket.WebSocketApp(stream_url, on_message=_on_message, on_error=_on_error, on_close=_on_close, on_open=_on_open)
        threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 20, "ping_timeout": 10}, daemon=True).start()
        time.sleep(0.3)


def daily_symbol_refresh_loop() -> None:
    global tracked_symbols
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute == 0:
            symbols = _select_daily_symbols()
            if symbols:
                tracked_symbols = symbols
                for s in symbols:
                    _bootstrap_symbol_indicators(s.upper())
                tg.send_message(f"🔄 일일 코인 갱신 완료\n대상 코인: {len(symbols)}개")
            time.sleep(70)
        time.sleep(5)


def main() -> None:
    setup_logging()
    validate_secrets()
    check_single_instance()

    tg.register_command("status", cmd_status)
    tg.register_command("stop", cmd_stop)
    tg.register_command("restart", cmd_restart)
    tg.register_command("closeall", cmd_closeall)
    tg.start_polling()

    recover_positions()

    symbols = _select_daily_symbols()
    if not symbols:
        tg.send_message("❌ 거래 가능한 대상 코인을 찾지 못했습니다.")
        remove_lock()
        return
    for s in symbols:
        _bootstrap_symbol_indicators(s.upper())
    tracked_symbols[:] = symbols

    tg.send_message(
        f"🚀 봇 시작\n"
        f"전략: A (EMA200 + ATR + ADX)\n"
        f"대상 코인: {len(symbols)}개\n"
        f"초기 500봉 로딩 완료"
    )

    threading.Thread(target=monitor_positions_loop, daemon=True).start()
    threading.Thread(target=daily_symbol_refresh_loop, daemon=True).start()
    start_websockets(symbols)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        remove_lock()


if __name__ == "__main__":
    main()

