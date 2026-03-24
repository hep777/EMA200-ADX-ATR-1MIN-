import logging
import threading
import time
from typing import Callable, Dict, Optional

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


logger = logging.getLogger("telegram")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

_cmd_callbacks: Dict[str, Callable[[], None]] = {}
_last_update_id = 0


def send_message(text: str) -> None:
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


def register_command(command: str, callback: Callable[[], None]) -> None:
    _cmd_callbacks[command.lower()] = callback


def _get_latest_update_id() -> int:
    try:
        resp = requests.get(f"{BASE_URL}/getUpdates", params={"limit": 100}, timeout=10)
        data = resp.json()
        updates = data.get("result", [])
        if not updates:
            return 0
        return int(updates[-1]["update_id"])
    except Exception as e:
        logger.error(f"Telegram init failed: {e}")
        return 0


def _poll_loop() -> None:
    global _last_update_id
    while True:
        try:
            resp = requests.get(
                f"{BASE_URL}/getUpdates",
                params={
                    "offset": _last_update_id + 1,
                    "timeout": 10,
                    "allowed_updates": ["message"],
                },
                timeout=15,
            )
            data = resp.json()

            if not data.get("ok"):
                time.sleep(5)
                continue

            for update in data.get("result", []):
                _last_update_id = int(update["update_id"])
                msg = update.get("message", {}) or {}
                if str(msg.get("chat", {}).get("id", "")) != str(TELEGRAM_CHAT_ID):
                    continue
                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue

                cmd = text.lstrip("/").split()[0].lower()
                cb = _cmd_callbacks.get(cmd)
                if cb is None:
                    send_message(
                        "❓ 알 수 없는 명령\n\n"
                        "<b>Trendline 봇</b>\n"
                        "/status — 잔고·포지션·추세선\n"
                        "/stop — 프로세스 종료 (봇 프로세스 완전 종료)\n"
                        "/closeall — 열린 포지션 전부 시장가 청산\n"
                        "다시 켜기: 서버에서 "
                        "<code>sudo systemctl start atr_bot.service</code> 또는 nohup 스크립트"
                    )
                    continue

                cb()
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            time.sleep(5)

        time.sleep(1)


def start_polling() -> threading.Thread:
    global _last_update_id
    _last_update_id = _get_latest_update_id()
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    return t


def alert_bot_status(message: str) -> None:
    send_message(f"🤖 <b>BOT STATUS</b>\n{message}")


def alert_entry(
    symbol_upper: str,
    direction: str,
    entry_price: float,
    tp_price: float,
    sl_price: float,
    reason: str,
    rsi: float,
    body_move_pct: float,
) -> None:
    reason_map = {
        "SIMPLE_LONG": "기본 롱 (바디+RSI)",
        "SIMPLE_SHORT": "기본 숏 (바디+RSI)",
    }
    reason_text = reason_map.get(reason, reason)
    side_emoji = "📈" if direction == "long" else "📉"
    send_message(
        f"🟢 <b>ENTRY OPEN</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"{side_emoji} <b>{symbol_upper}</b>  {direction.upper()}\n"
        f"사유: <b>{reason_text}</b>\n"
        f"RSI: <b>{rsi:.2f}</b>\n"
        f"바디변화: <b>{body_move_pct*100:+.2f}%</b>\n"
        f"진입가: <b>{entry_price:.8f}</b>\n"
        f"익절가(TP): <b>{tp_price:.8f}</b>\n"
        f"현재손절: <b>{sl_price:.8f}</b>"
    )


def alert_exit(symbol_upper: str, direction: str, reason: str, entry_price: float, exit_price: float, pnl_pct: float) -> None:
    emoji = "💰" if reason == "TP" else "🔴"
    send_message(
        f"{emoji} <b>EXIT</b> ({reason})\n"
        f"{symbol_upper} {direction.upper()}\n"
        f"Entry: {entry_price}\n"
        f"Exit: {exit_price}\n"
        f"PnL: {pnl_pct:+.2f}%"
    )

