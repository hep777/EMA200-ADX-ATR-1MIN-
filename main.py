import json
import logging
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import websocket

from config import (
    LOCK_FILE,
    LOG_FILE,
    MAX_CONCURRENT_POSITIONS,
    POSITION_MARGIN_PCT,
    STREAM_BATCH_SIZE,
    validate_secrets,
    TRAIL_ACT_ATR_MULT,
    TRAIL_DIST_ATR_MULT,
    SL_ATR_MULT,
    ATR_INTERVAL,
    ATR_PERIOD,
    SL_MIN_DIST_PCT,
)
from atr import compute_atr_wilder_from_klines
from binance_client import (
    close_position_market,
    get_all_usdt_futures_symbols,
    get_account_equity_usdt,
    get_klines,
    get_mark_price,
    get_open_positions,
    open_position_market,
)
from indicators import RSIComputer
from state_manager import load_state, remove_position, save_state, upsert_position
from strategy import decide_entry_direction
import telegram_client as tg

logger = logging.getLogger("bot")

bot_active = True
state_lock = threading.Lock()

rsi_computers: Dict[str, RSIComputer] = defaultdict(lambda: RSIComputer(14))
active_positions: Dict[str, Dict[str, Any]] = {}  # symbol_upper -> pos_state


def check_single_instance() -> None:
    lock_path = LOCK_FILE
    lock_dir = os.path.dirname(lock_path)
    if lock_dir and not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)

    if os.path.exists(lock_path):
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip() or "0")
            if old_pid:
                # On Linux/Mac, /proc exists; on Windows, this check may not work.
                if os.name != "nt" and os.path.exists(f"/proc/{old_pid}"):
                    print(f"Bot already running (PID: {old_pid}). Exiting.")
                    sys.exit(1)
        except Exception:
            pass

    with open(lock_path, "w", encoding="utf-8") as f:
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


def _round_price(symbol_upper: str, price: float) -> float:
    # This project avoids exchange-specific precision caching;
    # for safety, we just return price unchanged here.
    return price


def _compute_entry_exit_params(symbol_upper: str, entry_price: float) -> Optional[Dict[str, Any]]:
    klines = get_klines(symbol_upper, ATR_INTERVAL, ATR_PERIOD + 2)
    if not klines:
        logger.error(f"{symbol_upper} ATR klines fetch failed.")
        return None

    atr = compute_atr_wilder_from_klines(klines, ATR_PERIOD)
    if atr is None or atr <= 0:
        logger.error(f"{symbol_upper} ATR compute failed: {atr}")
        return None

    fixed_distance = atr * SL_ATR_MULT
    min_distance = entry_price * SL_MIN_DIST_PCT
    if fixed_distance < min_distance:
        fixed_distance = min_distance

    activation_dist = atr * TRAIL_ACT_ATR_MULT
    trail_distance = atr * TRAIL_DIST_ATR_MULT

    return {
        "atr": atr,
        "fixed_distance": fixed_distance,
        "activation_dist": activation_dist,
        "trail_distance": trail_distance,
    }


def _build_position_state(symbol_upper: str, direction: str, entry_price: float, quantity: float, leverage: int) -> Optional[Dict[str, Any]]:
    params = _compute_entry_exit_params(symbol_upper, entry_price)
    if params is None:
        return None

    fixed_distance = params["fixed_distance"]
    activation_dist = params["activation_dist"]
    trail_distance = params["trail_distance"]

    if direction == "long":
        fixed_sl_price = entry_price - fixed_distance
        peak_price = entry_price
        trough_price = None
    else:
        fixed_sl_price = entry_price + fixed_distance
        trough_price = entry_price
        peak_price = None

    return {
        "symbol": symbol_upper,
        "direction": direction,
        "entry_price": float(entry_price),
        "quantity": float(quantity),
        "leverage": leverage,
        "fixed_sl_price": float(fixed_sl_price),
        "activation_dist": float(activation_dist),
        "trail_distance": float(trail_distance),
        "trail_active": False,
        "peak_price": peak_price,  # long
        "trough_price": trough_price,  # short
        "current_stop_price": float(fixed_sl_price),
        "opened_at": int(time.time()),
        "params": {"atr": params["atr"], "fixed_distance": fixed_distance},
    }


def recover_positions() -> None:
    """
    Restore internal state for open exchange positions.
    Prefer state.json values; otherwise rebuild ATR params using current 5m klines at entry_price.
    """
    saved = load_state()
    saved_positions = saved.get("positions", {})

    exchange_positions = get_open_positions()
    open_symbols = set()

    for p in exchange_positions:
        sym = p["symbol"]
        open_symbols.add(sym)
        direction = p["direction"]
        entry = p["entry_price"]
        qty = p["amount"]

        if sym in saved_positions:
            sp = saved_positions[sym]
            try:
                # Basic consistency check: entry_price close enough.
                sp_entry = float(sp.get("entry_price", 0))
                if abs(sp_entry - entry) / entry < 0.002:
                    active_positions[sym] = sp
                    continue
            except Exception:
                pass

        # Rebuild missing/stale state.
        # leverage isn't included in positionRisk response here; we reconstruct as desired leverage.
        lev = 7
        pos_state = _build_position_state(sym, direction, entry, qty, lev)
        if pos_state:
            active_positions[sym] = pos_state
            upsert_position(saved, sym, pos_state)

    # Remove stale positions from state.json
    for sym in list(saved_positions.keys()):
        if sym not in open_symbols:
            remove_position(saved, sym)

    save_state(saved)


def _current_equity_margin_usdt() -> float:
    equity = get_account_equity_usdt()
    return equity * POSITION_MARGIN_PCT


def _can_open_more() -> bool:
    return len(active_positions) < MAX_CONCURRENT_POSITIONS


def _open_trade(symbol_upper: str, direction: str) -> None:
    # symbol_upper like "BTCUSDT"
    margin_usdt = _current_equity_margin_usdt()
    if margin_usdt <= 0:
        logger.error("Equity is 0 or invalid; cannot open.")
        return

    res = open_position_market(symbol_lower=symbol_upper.lower(), direction=direction, margin_usdt=margin_usdt)
    if not res:
        logger.error(f"{symbol_upper} order failed.")
        tg.send_message(f"❌ Order failed: {symbol_upper} {direction}")
        with state_lock:
            if active_positions.get(symbol_upper, {}).get("pending"):
                active_positions.pop(symbol_upper, None)
        return

    entry_price = float(res["entry_price"])
    qty = float(res["quantity"])
    lev = int(res["leverage"])

    pos_state = _build_position_state(symbol_upper, direction, entry_price, qty, lev)
    if pos_state is None:
        # If we can't compute exits, we should close immediately to avoid unprotected trades.
        logger.error(f"{symbol_upper} exit params build failed; closing immediately.")
        close_position_market(symbol_lower=symbol_upper.lower(), direction=direction, quantity=qty)
        with state_lock:
            if active_positions.get(symbol_upper, {}).get("pending"):
                active_positions.pop(symbol_upper, None)
        return

    with state_lock:
        active_positions[symbol_upper] = pos_state
        st = load_state()
        upsert_position(st, symbol_upper, pos_state)
        save_state(st)

    tg.alert_entry(symbol_upper, direction, entry_price, pos_state["current_stop_price"])


def process_kline(symbol_lower: str, kline: Dict[str, Any]) -> None:
    """
    kline: event['k']
    """
    global bot_active
    if not bot_active:
        return
    if not kline.get("x"):
        return  # candle not closed

    symbol_upper = symbol_lower.upper()  # e.g. BTCUSDT
    open_price = float(kline["o"])
    high_price = float(kline["h"])
    low_price = float(kline["l"])
    close_price = float(kline["c"])

    rsi = rsi_computers[symbol_upper].update(close_price)
    direction = decide_entry_direction(open_price, high_price, low_price, close_price, rsi)
    if direction is None:
        return

    # Reserve a slot to avoid races where multiple websocket threads open simultaneously.
    with state_lock:
        if symbol_upper in active_positions:
            return
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
            return
        active_positions[symbol_upper] = {"pending": True, "direction": direction}

    _open_trade(symbol_upper, direction)


def monitor_positions_loop() -> None:
    """
    Periodically update trailing stop for each open position and close when stop is hit.
    """
    last_persist_ts = 0.0
    while True:
        time.sleep(3)
        with state_lock:
            pos_snapshot = dict(active_positions)

        dirty = False
        for symbol_upper, pos in pos_snapshot.items():
            try:
                if pos.get("pending"):
                    continue
                current = get_mark_price(symbol_upper)
                direction = pos["direction"]
                entry = float(pos["entry_price"])
                fixed_sl = float(pos["fixed_sl_price"])
                trail_active = bool(pos.get("trail_active", False))
                trail_distance = float(pos["trail_distance"])
                activation_dist = float(pos["activation_dist"])

                if direction == "long":
                    if not trail_active and current >= entry + activation_dist:
                        trail_active = True
                        pos["trail_active"] = True
                        pos["peak_price"] = max(float(pos["peak_price"]), current)

                    if trail_active:
                        pos["peak_price"] = max(float(pos["peak_price"]), current)
                        trailing_sl = float(pos["peak_price"]) - trail_distance
                        pos["current_stop_price"] = max(fixed_sl, trailing_sl)

                    # SL hit?
                    if current <= float(pos["current_stop_price"]):
                        _close_and_cleanup(symbol_upper, pos, current, reason="SL/STOP")

                else:  # short
                    if not trail_active and current <= entry - activation_dist:
                        trail_active = True
                        pos["trail_active"] = True
                        pos["trough_price"] = min(float(pos["trough_price"]), current)

                    if trail_active:
                        pos["trough_price"] = min(float(pos["trough_price"]), current)
                        trailing_sl = float(pos["trough_price"]) + trail_distance
                        pos["current_stop_price"] = min(fixed_sl, trailing_sl)

                    if current >= float(pos["current_stop_price"]):
                        _close_and_cleanup(symbol_upper, pos, current, reason="SL/STOP")

            except Exception as e:
                logger.error(f"Monitor error {symbol_upper}: {e}")

            dirty = True

        # Persist trailing state periodically so restarts can continue seamlessly.
        now = time.time()
        if dirty and (now - last_persist_ts) >= 10:
            last_persist_ts = now
            with state_lock:
                st = load_state()
                for sym, p in active_positions.items():
                    if p.get("pending"):
                        continue
                    upsert_position(st, sym, p)
                save_state(st)


def _close_and_cleanup(symbol_upper: str, pos: Dict[str, Any], exit_price: float, reason: str) -> None:
    direction = pos["direction"]
    qty = float(pos["quantity"])
    entry = float(pos["entry_price"])
    margin_usdt = _current_equity_margin_usdt()
    lev = int(pos.get("leverage", 7))

    with state_lock:
        # Another thread might have already removed it.
        if symbol_upper not in active_positions or active_positions[symbol_upper].get("pending"):
            return

    res = close_position_market(symbol_lower=symbol_upper.lower(), direction=direction, quantity=qty)
    if not res:
        logger.error(f"{symbol_upper} close failed.")
        return

    # Cleanup state
    with state_lock:
        active_positions.pop(symbol_upper, None)
        st = load_state()
        remove_position(st, symbol_upper)
        save_state(st)

    if direction == "long":
        pnl_pct = ((exit_price - entry) / entry) * 100.0
    else:
        pnl_pct = ((entry - exit_price) / entry) * 100.0
    pnl_usdt_est = margin_usdt * (pnl_pct / 100.0) * lev

    tg.alert_exit(symbol_upper, direction, "TRAIL" if pos.get("trail_active") else "SL", entry, exit_price, pnl_pct)


# ─── Telegram commands ─────────────────────────────────────────────


def cmd_status() -> None:
    with state_lock:
        pos_list = list(active_positions.items())
    equity = get_account_equity_usdt()

    lines = [
        "📊 <b>STATUS</b>",
        f"💰 Equity: {equity:.2f} USDT",
        f"📂 Positions: {len(pos_list)}/{MAX_CONCURRENT_POSITIONS}",
    ]
    if pos_list:
        lines.append("")
        for sym, pos in pos_list:
            if pos.get("pending"):
                continue
            dir_text = "LONG" if pos["direction"] == "long" else "SHORT"
            stop = pos.get("current_stop_price")
            lines.append(f"• {sym} {dir_text} entry={pos['entry_price']:.6f} stop={stop:.6f}")

    tg.send_message("\n".join(lines))


def cmd_stop() -> None:
    global bot_active
    bot_active = False
    tg.send_message("🛑 STOP: new entries disabled. Existing positions are kept.")


def cmd_resume() -> None:
    global bot_active
    bot_active = True
    tg.send_message("▶️ RESUME: new entries enabled.")


def cmd_closeall() -> None:
    with state_lock:
        pos_snapshot = dict(active_positions)

    if not pos_snapshot:
        tg.send_message("ℹ️ No open positions.")
        return

    tg.send_message(f"🟠 Closing all positions: {len(pos_snapshot)}")
    for symbol_upper, pos in pos_snapshot.items():
        if pos.get("pending"):
            continue
        qty = float(pos["quantity"])
        direction = pos["direction"]
        try:
            close_position_market(symbol_upper.lower(), direction, qty)
        except Exception:
            pass

    # Cleanup local state after a short delay
    time.sleep(2)
    with state_lock:
        active_positions.clear()
        st = load_state()
        st["positions"] = {}
        save_state(st)

    tg.send_message("✅ All positions closed (local state cleared).")


def cmd_restart() -> None:
    tg.send_message("🔄 Restarting bot...")
    remove_lock()
    time.sleep(1)
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ─── Websocket callbacks ─────────────────────────────────────────


def _on_message(ws, message: str) -> None:
    try:
        data = json.loads(message)
        payload = data.get("data") if "data" in data else data
        if payload.get("e") != "kline":
            return
        k = payload.get("k", {})
        symbol_lower = payload.get("s", "").lower()
        process_kline(symbol_lower, k)
    except Exception as e:
        logger.error(f"WS message error: {e}")


def _on_error(ws, error) -> None:
    logger.error(f"WS error: {error}")


def _on_close(ws, close_status_code, close_msg) -> None:
    tg.send_message(f"⚠️ Websocket disconnected: {close_status_code} {close_msg}")


def _on_open(ws) -> None:
    logger.info("Websocket connected")


def start_websockets(symbols_lower: List[str]) -> None:
    streams = [f"{s}@kline_1m" for s in symbols_lower]
    threads = []

    for i in range(0, len(streams), STREAM_BATCH_SIZE):
        batch = streams[i : i + STREAM_BATCH_SIZE]
        stream_url = "wss://fstream.binance.com/stream?streams=" + "/".join(batch)
        ws = websocket.WebSocketApp(
            stream_url,
            on_message=_on_message,
            on_error=_on_error,
            on_close=_on_close,
            on_open=_on_open,
        )
        t = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 20, "ping_timeout": 10}, daemon=True)
        t.start()
        threads.append(t)
        logger.info(f"WS batch {len(threads)} started: {len(batch)} streams")
        time.sleep(0.5)


def main() -> None:
    setup_logging()
    validate_secrets()
    check_single_instance()

    tg.register_command("status", cmd_status)
    tg.register_command("stop", cmd_stop)
    tg.register_command("resume", cmd_resume)
    tg.register_command("closeall", cmd_closeall)
    tg.register_command("restart", cmd_restart)
    tg.start_polling()

    logger.info("Starting ATR trading bot...")
    tg.alert_bot_status("🚀 Bot started (ATR 5m exits, 1m RSI/body signals).")

    recover_positions()

    symbols = get_all_usdt_futures_symbols()
    if not symbols:
        logger.error("No symbols loaded.")
        remove_lock()
        return

    threading.Thread(target=monitor_positions_loop, daemon=True).start()
    start_websockets(symbols)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        tg.send_message("🛑 Bot stopped manually (KeyboardInterrupt).")
        remove_lock()


if __name__ == "__main__":
    main()

