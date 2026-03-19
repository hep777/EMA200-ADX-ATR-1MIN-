import json
import os
import time
from typing import Any, Dict, Optional

from config import STATE_FILE


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"positions": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "positions" not in data:
                data["positions"] = {}
            return data
    except Exception:
        return {"positions": {}}


def save_state(state: Dict[str, Any]) -> None:
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_FILE)


def upsert_position(state: Dict[str, Any], symbol_upper: str, pos: Dict[str, Any]) -> None:
    state.setdefault("positions", {})
    state["positions"][symbol_upper] = {**pos, "symbol": symbol_upper, "updated_at": int(time.time())}


def remove_position(state: Dict[str, Any], symbol_upper: str) -> None:
    state.setdefault("positions", {})
    state["positions"].pop(symbol_upper, None)


def get_position(state: Dict[str, Any], symbol_upper: str) -> Optional[Dict[str, Any]]:
    return state.get("positions", {}).get(symbol_upper)

