import hashlib
import hmac
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests

from config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    DESIRED_LEVERAGE,
    DEFAULT_LEVERAGE,
    EXCLUDE_SYMBOLS,
    LEVERAGE_BY_SYMBOL,
)


logger = logging.getLogger("binance")
BASE_URL = "https://fapi.binance.com"


def _sign(params: Dict[str, Any]) -> Dict[str, Any]:
    query_string = urlencode(params)
    signature = hmac.new(
        BINANCE_API_SECRET.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    params["signature"] = signature
    return params


def _headers() -> Dict[str, str]:
    return {"X-MBX-APIKEY": BINANCE_API_KEY}


def _request(method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, signed: bool = True) -> Optional[Dict[str, Any]]:
    url = f"{BASE_URL}{endpoint}"
    if params is None:
        params = {}

    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params = _sign(params)

    try:
        if method == "GET":
            resp = requests.get(url, params=params, headers=_headers() if signed else None, timeout=10)
        elif method == "DELETE":
            resp = requests.delete(url, params=params, headers=_headers() if signed else None, timeout=10)
        else:
            resp = requests.post(url, params=params, headers=_headers() if signed else None, timeout=10)
    except Exception as e:
        logger.error(f"API request failed: {e}")
        return None

    try:
        data = resp.json()
    except Exception:
        logger.error(f"API non-json response {resp.status_code}: {resp.text[:200]}")
        return None

    if resp.status_code != 200:
        logger.error(f"API error {resp.status_code}: {data}")
        return None

    return data


def get_all_usdt_futures_symbols() -> List[str]:
    url = f"{BASE_URL}/fapi/v1/exchangeInfo"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    symbols: List[str] = []
    for s in data.get("symbols", []):
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol", "")
        if not sym:
            continue
        if sym in EXCLUDE_SYMBOLS:
            continue
        symbols.append(sym.lower())

    logger.info(f"Trading universe size: {len(symbols)}")
    return symbols


def get_top_usdt_symbols_by_quote_volume(limit: int = 20, min_last_price: float = 1.0) -> List[str]:
    """
    Returns symbol list in lower-case (e.g. btcusdt).
    """
    info_resp = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=10)
    info_data = info_resp.json()
    tradable = set()
    for s in info_data.get("symbols", []):
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol", "")
        if sym and sym not in EXCLUDE_SYMBOLS:
            tradable.add(sym)

    tick_resp = requests.get(f"{BASE_URL}/fapi/v1/ticker/24hr", timeout=10)
    tick_data = tick_resp.json()
    rows = []
    for row in tick_data:
        sym = row.get("symbol", "")
        if sym not in tradable:
            continue
        try:
            last_price = float(row.get("lastPrice", 0))
            qv = float(row.get("quoteVolume", 0))
        except Exception:
            continue
        if last_price < min_last_price:
            continue
        rows.append((sym, qv))

    rows.sort(key=lambda x: x[1], reverse=True)
    return [sym.lower() for sym, _ in rows[:limit]]


def get_symbol_info(symbol: str) -> Optional[Dict[str, Any]]:
    # Note: symbol must be like "BTCUSDT"
    resp = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=10)
    data = resp.json()
    for s in data.get("symbols", []):
        if s.get("symbol") == symbol:
            price_precision = s.get("pricePrecision", 4)
            qty_precision = s.get("quantityPrecision", 0)
            min_qty = 0.0
            min_notional = 0.0
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    min_qty = float(f.get("minQty", 0))
                elif f.get("filterType") == "MIN_NOTIONAL":
                    min_notional = float(f.get("notional", 0))
            return {
                "price_precision": price_precision,
                "qty_precision": qty_precision,
                "min_qty": min_qty,
                "min_notional": min_notional,
            }
    return None


def get_max_leverage(symbol: str) -> int:
    data = _request("GET", "/fapi/v1/leverageBracket", {"symbol": symbol})
    if not data:
        return DESIRED_LEVERAGE
    try:
        return int(data[0]["brackets"][0]["initialLeverage"])
    except Exception:
        return DESIRED_LEVERAGE


def set_isolated_and_leverage(symbol: str) -> Optional[int]:
    """
    Returns actual leverage if set successfully, else None.
    We enforce "ISOLATED 7x fixed" by skipping symbols where 7x isn't allowed.
    """
    desired = LEVERAGE_BY_SYMBOL.get(symbol, DEFAULT_LEVERAGE)
    max_lev = get_max_leverage(symbol)
    if max_lev < desired:
        logger.warning(f"{symbol} max leverage {max_lev} < desired {desired}. Skip.")
        return None

    _request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"})
    result = _request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": desired})
    if result is None:
        logger.error(f"Failed to set leverage for {symbol}")
        return None
    return desired


def get_mark_price(symbol: str) -> float:
    resp = requests.get(f"{BASE_URL}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=5)
    data = resp.json()
    return float(data["markPrice"])


def get_account_equity_usdt() -> float:
    data = _request("GET", "/fapi/v2/balance")
    if not data:
        return 0.0
    for asset in data:
        if asset.get("asset") == "USDT":
            return float(asset.get("availableBalance", 0))
    return 0.0


def get_open_positions() -> List[Dict[str, Any]]:
    data = _request("GET", "/fapi/v2/positionRisk")
    if not data:
        return []
    positions: List[Dict[str, Any]] = []
    for p in data:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        positions.append(
            {
                "symbol": p.get("symbol"),
                "direction": "long" if amt > 0 else "short",
                "amount": abs(amt),
                "entry_price": float(p.get("entryPrice", 0)),
                "mark_price": float(p.get("markPrice", 0)),
            }
        )
    return positions


def get_klines(symbol: str, interval: str, limit: int) -> Optional[List[List[Any]]]:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(f"{BASE_URL}/fapi/v1/klines", params=params, timeout=10)
    if resp.status_code != 200:
        return None
    return resp.json()


def calculate_quantity(symbol: str, margin_usdt: float, mark_price: float, leverage: int) -> Optional[Tuple[float, int]]:
    info = get_symbol_info(symbol)
    if not info:
        logger.error(f"{symbol} symbol info not found")
        return None

    position_value = margin_usdt * leverage  # notional
    raw_qty = position_value / mark_price
    qty = round(raw_qty, info["qty_precision"])

    if qty <= 0:
        return None
    if qty < info["min_qty"]:
        logger.warning(f"{symbol} qty {qty} < min_qty {info['min_qty']}")
        return None
    if qty * mark_price < info["min_notional"]:
        logger.warning(f"{symbol} notional {qty*mark_price:.4f} < min_notional {info['min_notional']}")
        return None

    return float(qty), int(info["price_precision"])


def calculate_quantity_by_risk(symbol: str, risk_usdt: float, stop_distance: float) -> Optional[Tuple[float, int]]:
    info = get_symbol_info(symbol)
    if not info:
        logger.error(f"{symbol} symbol info not found")
        return None
    if stop_distance <= 0:
        return None

    raw_qty = risk_usdt / stop_distance
    qty = round(raw_qty, info["qty_precision"])
    if qty <= 0:
        return None
    if qty < info["min_qty"]:
        return None
    return float(qty), int(info["price_precision"])


def open_position_market(symbol_lower: str, direction: str, quantity: float) -> Optional[Dict[str, Any]]:
    symbol = symbol_lower.upper()
    leverage = set_isolated_and_leverage(symbol)
    if leverage is None:
        return None

    mark_price = get_mark_price(symbol)
    info = get_symbol_info(symbol)
    if not info:
        return None
    qty = round(quantity, info["qty_precision"])
    if qty <= 0:
        return None
    if qty < info["min_qty"]:
        return None
    price_precision = int(info["price_precision"])

    side = "BUY" if direction == "long" else "SELL"
    order = _request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
        },
    )
    if not order:
        logger.error(f"{symbol} market order failed")
        return None

    order_id = order.get("orderId")
    entry_price = float(order.get("avgPrice", 0) or 0)
    if entry_price == 0:
        # Fallback: mark price (server-side fill price can be tricky via avgPrice=0)
        entry_price = mark_price

    return {
        "symbol": symbol,
        "symbol_lower": symbol_lower,
        "direction": direction,
        "entry_price": entry_price,
        "quantity": qty,
        "leverage": leverage,
        "price_precision": price_precision,
        "order_id": order_id,
    }


def close_position_market(symbol_lower: str, direction: str, quantity: float) -> Optional[Dict[str, Any]]:
    symbol = symbol_lower.upper()
    close_side = "SELL" if direction == "long" else "BUY"
    order = _request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": close_side,
            "type": "MARKET",
            "quantity": quantity,
            "reduceOnly": "true",
        },
    )
    return order


def place_reduce_only_stop_market(symbol: str, direction: str, stop_price: float) -> Optional[Dict[str, Any]]:
    side = "SELL" if direction == "long" else "BUY"
    return _request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": round(stop_price, 8),
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
            "closePosition": "true",
        },
    )


def get_open_orders(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {}
    if symbol:
        params["symbol"] = symbol
    data = _request("GET", "/fapi/v1/openOrders", params=params)
    if not data:
        return []
    return data if isinstance(data, list) else []


def cancel_all_open_orders(symbol: str) -> Optional[List[Dict[str, Any]]]:
    data = _request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
    if data is None:
        return None
    if isinstance(data, list):
        return data
    return [data]


def cancel_order(symbol: str, order_id: int) -> Optional[Dict[str, Any]]:
    return _request("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})

