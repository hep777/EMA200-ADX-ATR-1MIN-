import hashlib
import hmac
import time
import logging
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


def _request(
    method: str,
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    signed: bool = True,
    ignore_binance_error_codes: Optional[Tuple[int, ...]] = None,
) -> Optional[Dict[str, Any]]:
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
        err_code: Optional[int] = None
        if isinstance(data, dict):
            try:
                err_code = int(data.get("code"))
            except (TypeError, ValueError):
                err_code = None
        if ignore_binance_error_codes and err_code in ignore_binance_error_codes:
            logger.debug("API %s ignored (code=%s): %s", resp.status_code, err_code, data)
            return None
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


def _price_tick_size_from_symbol_info(symbol_dict: dict) -> float:
    for f in symbol_dict.get("filters", []):
        if f.get("filterType") == "PRICE_FILTER":
            try:
                return float(f.get("tickSize", "0"))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def get_combined_universe_symbols(
    volume_top_n: int = 20,
    gainer_pool_size: int = 300,
    max_total: int = 300,
    max_tick_as_pct_of_price: float = 0.004,
    min_last_price: float = 0.0,
) -> List[str]:
    """
    총합 ≤ max_total.
    1) 24h 거래대금(quoteVolume) 상위 volume_top_n — 틱 필터 없음, 먼저 포함
    2) 나머지 슬롯: 24h 상승률 상위 gainer_pool_size개 '풀'을 순회하며
       tick/price >= max_tick_as_pct_of_price 는 제외, max_total까지 채움
    """
    info_resp = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=10)
    info_data = info_resp.json()
    tick_by_symbol: Dict[str, float] = {}
    for s in info_data.get("symbols", []):
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol", "")
        if not sym or sym in EXCLUDE_SYMBOLS:
            continue
        tick = _price_tick_size_from_symbol_info(s)
        if tick <= 0:
            continue
        tick_by_symbol[sym] = tick

    tick_resp = requests.get(f"{BASE_URL}/fapi/v1/ticker/24hr", timeout=10)
    tick_data = tick_resp.json()
    rows: List[Tuple[str, float, float, float]] = []
    for row in tick_data:
        sym = row.get("symbol", "")
        if sym not in tick_by_symbol:
            continue
        try:
            last_price = float(row.get("lastPrice", 0))
            qv = float(row.get("quoteVolume", 0))
            pct = float(row.get("priceChangePercent", 0))
        except (TypeError, ValueError):
            continue
        if last_price < min_last_price:
            continue
        rows.append((sym, qv, pct, last_price))

    by_vol = sorted(rows, key=lambda x: x[1], reverse=True)
    vol_top_syms = [r[0] for r in by_vol[:volume_top_n]]

    by_pct = sorted(rows, key=lambda x: x[2], reverse=True)
    gainer_pool = by_pct[:gainer_pool_size]

    out: List[str] = []
    seen: set[str] = set()

    for sym in vol_top_syms:
        if len(out) >= max_total:
            break
        if sym in tick_by_symbol:
            sl = sym.lower()
            if sl not in seen:
                out.append(sl)
                seen.add(sl)

    for sym, _qv, _pct, last_price in gainer_pool:
        if len(out) >= max_total:
            break
        sl = sym.lower()
        if sl in seen:
            continue
        tick = tick_by_symbol[sym]
        tick_pct = tick / last_price if last_price > 0 else 1.0
        if tick_pct >= max_tick_as_pct_of_price:
            continue
        out.append(sl)
        seen.add(sl)

    logger.info(
        "Universe: total=%d (cap=%d), vol_top_n=%d, gainer_pool=%d, tick<%s%% on filler only",
        len(out),
        max_total,
        volume_top_n,
        gainer_pool_size,
        max_tick_as_pct_of_price * 100.0,
    )
    return out


def get_gainer_universe_symbols(
    top_gainers: int = 300,
    max_tick_as_pct_of_price: float = 0.004,
    min_last_price: float = 0.0,
) -> List[str]:
    """하위 호환: 거래대금 0 + 상승률만."""
    return get_combined_universe_symbols(
        0, top_gainers, top_gainers, max_tick_as_pct_of_price, min_last_price
    )


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
            tick_size = 0.0
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    min_qty = float(f.get("minQty", 0))
                elif f.get("filterType") == "MIN_NOTIONAL":
                    min_notional = float(f.get("notional", 0))
                elif f.get("filterType") == "PRICE_FILTER":
                    try:
                        tick_size = float(f.get("tickSize", 0) or 0)
                    except (TypeError, ValueError):
                        tick_size = 0.0
            return {
                "price_precision": price_precision,
                "qty_precision": qty_precision,
                "min_qty": min_qty,
                "min_notional": min_notional,
                "tick_size": tick_size,
            }
    return None


def get_price_tick_size(symbol: str) -> float:
    """PRICE_FILTER tickSize for symbol (e.g. BTCUSDT)."""
    info = get_symbol_info(symbol)
    if not info:
        return 0.0
    return float(info.get("tick_size", 0.0) or 0.0)


def round_price_to_tick(symbol: str, price: float) -> float:
    tick = get_price_tick_size(symbol)
    if tick <= 0:
        return round(price, 8)
    steps = round(price / tick)
    return round(steps * tick, 8)


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
    ISOLATED margin; leverage from config (per-symbol map or default). Skips if max bracket < desired.
    """
    desired = LEVERAGE_BY_SYMBOL.get(symbol, DEFAULT_LEVERAGE)
    max_lev = get_max_leverage(symbol)
    if max_lev < desired:
        logger.warning(f"{symbol} max leverage {max_lev} < desired {desired}. Skip.")
        return None

    # -4046: already ISOLATED — not an error
    _request(
        "POST",
        "/fapi/v1/marginType",
        {"symbol": symbol, "marginType": "ISOLATED"},
        ignore_binance_error_codes=(-4046,),
    )
    result = _request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": desired})
    if result is None:
        logger.error(f"Failed to set leverage for {symbol}")
        return None
    return desired


def get_mark_price(symbol: str) -> float:
    resp = requests.get(f"{BASE_URL}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=5)
    data = resp.json()
    return float(data["markPrice"])


def _order_avg_from_query(symbol: str, order_id: int) -> Optional[float]:
    """GET /fapi/v1/order — 체결 후 avgPrice 확보."""
    data = _request("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
    if not data:
        return None
    try:
        ap = float(data.get("avgPrice", 0) or 0)
    except (TypeError, ValueError):
        return None
    return ap if ap > 0 else None


def _resolve_market_entry_price(symbol: str, order_id: int, order_response: Dict[str, Any], mark_fallback: float) -> float:
    """
    시장가 응답에 avgPrice가 0이거나 늦게 채워지는 경우가 있어,
    주문 조회 반복 후에도 없으면 포지션 entryPrice로 보완 (텔레그램 진입가 = 실제 체결에 가깝게).
    """
    try:
        ap0 = float(order_response.get("avgPrice", 0) or 0)
    except (TypeError, ValueError):
        ap0 = 0.0
    if ap0 > 0:
        return ap0
    for _ in range(12):
        time.sleep(0.12)
        q = _order_avg_from_query(symbol, order_id)
        if q is not None:
            return q
    for p in get_open_positions():
        if p.get("symbol") == symbol:
            ep = float(p.get("entry_price", 0))
            if ep > 0:
                return ep
    return mark_fallback


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
        liq_raw = p.get("liquidationPrice")
        liq: Optional[float] = None
        if liq_raw is not None:
            try:
                lv = float(liq_raw)
                if lv > 0:
                    liq = lv
            except (TypeError, ValueError):
                pass
        positions.append(
            {
                "symbol": p.get("symbol"),
                "direction": "long" if amt > 0 else "short",
                "amount": abs(amt),
                "entry_price": float(p.get("entryPrice", 0)),
                "mark_price": float(p.get("markPrice", 0)),
                "liquidation_price": liq,
            }
        )
    return positions


def get_liquidation_price(symbol: str) -> Optional[float]:
    """Liquidation price for current open position on symbol, if any."""
    data = _request("GET", "/fapi/v2/positionRisk", {"symbol": symbol.upper()})
    if not data:
        return None
    rows = data if isinstance(data, list) else [data]
    for p in rows:
        if p.get("symbol") != symbol.upper():
            continue
        if float(p.get("positionAmt", 0)) == 0:
            return None
        try:
            lv = float(p.get("liquidationPrice", 0))
            return lv if lv > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def get_liquidation_prices_map() -> Dict[str, float]:
    """symbol -> liquidation price for all non-zero positions."""
    data = _request("GET", "/fapi/v2/positionRisk")
    out: Dict[str, float] = {}
    if not data:
        return out
    for p in data:
        if float(p.get("positionAmt", 0)) == 0:
            continue
        sym = p.get("symbol")
        if not sym:
            continue
        try:
            lv = float(p.get("liquidationPrice", 0))
            if lv > 0:
                out[str(sym)] = lv
        except (TypeError, ValueError):
            continue
    return out


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

    order_id_raw = order.get("orderId")
    if order_id_raw is None:
        entry_price = float(order.get("avgPrice", 0) or 0) or mark_price
    else:
        entry_price = _resolve_market_entry_price(symbol, int(order_id_raw), order, mark_price)

    return {
        "symbol": symbol,
        "symbol_lower": symbol_lower,
        "direction": direction,
        "entry_price": entry_price,
        "quantity": qty,
        "leverage": leverage,
        "price_precision": price_precision,
        "order_id": order_id_raw,
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


def place_reduce_only_stop_market(
    symbol: str, direction: str, stop_price: float, quantity: float
) -> Optional[Dict[str, Any]]:
    side = "SELL" if direction == "long" else "BUY"
    # Primary: reduceOnly + quantity (widely compatible)
    sp = round_price_to_tick(symbol, float(stop_price))
    primary = _request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": sp,
            "quantity": quantity,
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
        },
    )
    if primary is not None:
        return primary

    # Fallback: closePosition mode (some account modes require this)
    return _request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": sp,
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


def place_take_profit_market(
    symbol: str, direction: str, tp_price: float, quantity: float
) -> Optional[Dict[str, Any]]:
    """Reduce-only take-profit (MARK_PRICE trigger)."""
    side = "SELL" if direction == "long" else "BUY"
    px = round_price_to_tick(symbol, float(tp_price))
    primary = _request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": px,
            "quantity": quantity,
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
        },
    )
    if primary is not None:
        return primary
    return _request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": px,
            "workingType": "MARK_PRICE",
            "closePosition": "true",
        },
    )


def get_top_usdt_perpet_by_quote_volume(
    limit: int = 300,
    extra_exclude: Optional[Tuple[str, ...]] = None,
) -> List[str]:
    """
    24h quoteVolume 기준 상위 limit개 USDT 무기한 선물 심볼 (소문자).
    EXCLUDE_SYMBOLS + extra_exclude 제외.
    """
    ex: set[str] = set(s.upper() for s in EXCLUDE_SYMBOLS)
    if extra_exclude:
        ex.update(s.upper() for s in extra_exclude)

    info_resp = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=15)
    info_data = info_resp.json()
    tradable: set[str] = set()
    for s in info_data.get("symbols", []):
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol", "")
        if not sym or sym in ex:
            continue
        tradable.add(sym)

    tick_resp = requests.get(f"{BASE_URL}/fapi/v1/ticker/24hr", timeout=15)
    tick_data = tick_resp.json()
    rows: List[Tuple[str, float]] = []
    for row in tick_data:
        sym = row.get("symbol", "")
        if sym not in tradable:
            continue
        try:
            qv = float(row.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        rows.append((sym, qv))

    rows.sort(key=lambda x: x[1], reverse=True)
    out = [sym.lower() for sym, _ in rows[:limit]]
    logger.info(
        "Universe (volume top %d): count=%d exclude=%s",
        limit,
        len(out),
        sorted(ex),
    )
    return out

