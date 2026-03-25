#!/usr/bin/env python3
"""
Post-Pump Short Bot
────────────────────────────────────────────────────────────────────
전략:
  1. 전체 USDT 선물 심볼 중 7일 상승률 > PUMP_THRESHOLD_7D 인 심볼을 선별
  2. 선별된 심볼들을 1분봉으로 실시간 모니터링
  3. 최근 고점 대비 DUMP_FROM_HIGH_PCT% 이상 하락 + 음봉 + 거래량 급증 시 숏 진입
  4. TP/SL 내부 mark price 폴링으로 관리
"""

import os, sys, time, math, json, logging, fcntl, requests
from datetime import datetime, timezone
from threading import Thread, Lock
from collections import deque

# ─── 설정 ────────────────────────────────────────────────────────────────────
LEVERAGE           = 3        # 레버리지 (안정 운용: 고정 3x)
RISK_PER_TRADE     = 0.01     # 트레이드당 잔고 비율 (1%)
MAX_POSITIONS      = 30       # 최대 동시 포지션 수
TP_PCT             = 2.5      # 테이크프로핏 %
SL_PCT             = 1.5      # 스탑로스 %
MARGIN_TYPE        = "ISOLATED"

# 펌프 스크리너 설정
PUMP_THRESHOLD_7D  = 40.0     # 7일 상승률 최소 임계값 (%)
PUMP_RESCAN_SEC    = 3600     # 펌프 목록 재스캔 주기 (초, 1시간)
EXCLUDE_SYMBOLS    = {"BTCUSDT", "ETHUSDT"}

# 덤프 감지 설정
LOOKBACK_CANDLES   = 60       # 최근 고점 탐색 캔들 수 (1분봉 기준 60분)
DUMP_FROM_HIGH_PCT = 4.0      # 최근 고점 대비 하락 % 이상
BEARISH_BODY_PCT   = 1.0      # 음봉 바디 최소 크기 %
VOLUME_MULTIPLIER  = 2.0      # 평균 거래량 대비 배율
VOL_AVG_PERIOD     = 20       # 거래량 평균 산출 기간

# 포지션 관리
POLL_INTERVAL_SEC  = 3        # TP/SL 폴링 주기 (초)
CANDLE_INTERVAL    = "1m"

# Binance API
BASE_URL           = "https://fapi.binance.com"
API_KEY            = os.getenv("BINANCE_API_KEY", "")
API_SECRET         = os.getenv("BINANCE_API_SECRET", "")

# Telegram
TG_TOKEN           = os.getenv("TG_TOKEN", "")
TG_CHAT_ID         = os.getenv("TG_CHAT_ID", "")

LOCKFILE           = "/tmp/pump_short_bot.lock"

# ─── 로깅 ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ─── 유틸 ────────────────────────────────────────────────────────────────────
import hmac, hashlib, urllib.parse

def sign(params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    sig   = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

def get(path, params=None, signed=False):
    if params is None: params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params = sign(params)
    r = requests.get(BASE_URL + path, params=params,
                     headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()

def post(path, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params = sign(params)
    r = requests.post(BASE_URL + path, params=params,
                      headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()

def delete(path, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params = sign(params)
    r = requests.delete(BASE_URL + path, params=params,
                        headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()

def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        log.warning(f"TG 전송 실패: {e}")

def floor_qty(qty: float, step: float) -> float:
    if step == 0: return qty
    decimals = max(0, round(-math.log10(step)))
    return math.floor(qty / step * 10**decimals) / 10**decimals

def get_mark_price(symbol: str) -> float:
    data = get("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(data["markPrice"])

# ─── 거래소 정보 캐시 ──────────────────────────────────────────────────────
_exchange_info = None
_symbol_info   = {}

def load_exchange_info():
    global _exchange_info, _symbol_info
    _exchange_info = get("/fapi/v1/exchangeInfo")
    for s in _exchange_info["symbols"]:
        if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL" and s["status"] == "TRADING":
            step = price_prec = None
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                if f["filterType"] == "PRICE_FILTER":
                    price_prec = float(f["tickSize"])
            _symbol_info[s["symbol"]] = {
                "qty_step":   step or 0.001,
                "price_tick": price_prec or 0.01,
                "min_qty":    float(next(f["minQty"] for f in s["filters"] if f["filterType"] == "LOT_SIZE"))
            }
    log.info(f"거래소 정보 로드 완료: {len(_symbol_info)}개 심볼")

def all_usdt_perp_symbols() -> list:
    return [s for s in _symbol_info.keys() if s not in EXCLUDE_SYMBOLS]

# ─── 레버리지 설정 ─────────────────────────────────────────────────────────
def set_leverage(symbol: str):
    try:
        post("/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
    except Exception as e:
        log.warning(f"{symbol} 레버리지 설정 실패: {e}")

def set_margin_type(symbol: str):
    try:
        post("/fapi/v1/marginType", {"symbol": symbol, "marginType": MARGIN_TYPE})
    except Exception:
        pass  # 이미 설정된 경우 에러 무시

# ─── 잔고 조회 ────────────────────────────────────────────────────────────
def get_balance() -> float:
    data = get("/fapi/v2/balance", signed=True)
    for asset in data:
        if asset["asset"] == "USDT":
            return float(asset["availableBalance"])
    return 0.0

# ─── 현재 포지션 조회 ─────────────────────────────────────────────────────
def get_open_positions() -> dict:
    data = get("/fapi/v2/positionRisk", signed=True)
    result = {}
    for p in data:
        amt = float(p["positionAmt"])
        if abs(amt) > 1e-9:
            result[p["symbol"]] = {
                "amt":       amt,
                "entry":     float(p["entryPrice"]),
                "side":      "SHORT" if amt < 0 else "LONG",
                "leverage":  float(p["leverage"])
            }
    return result

# ─── 주문 ─────────────────────────────────────────────────────────────────
def place_market_short(symbol: str, usdt_amount: float) -> dict:
    info  = _symbol_info[symbol]
    price = get_mark_price(symbol)
    raw   = (usdt_amount * LEVERAGE) / price
    qty   = floor_qty(raw, info["qty_step"])
    if qty < info["min_qty"]:
        raise ValueError(f"{symbol} 수량 부족: {qty} < {info['min_qty']}")
    order_resp = post("/fapi/v1/order", {
        "symbol":   symbol,
        "side":     "SELL",
        "type":     "MARKET",
        "quantity": qty,
        "positionSide": "BOTH"
    })
    # 실행된 수량 대신, 우리가 주문에 넣은 qty를 기준으로 저장(응답값 누락/0 방지)
    return {"order": order_resp, "qty": qty}

def close_position_market(symbol: str, qty: float):
    side = "BUY"  # 숏 포지션 청산
    abs_qty = abs(qty)
    info = _symbol_info[symbol]
    abs_qty = floor_qty(abs_qty, info["qty_step"])
    if abs_qty <= 0:
        raise ValueError(f"{symbol} 청산 수량이 0입니다. qty={qty}")
    return post("/fapi/v1/order", {
        "symbol":     symbol,
        "side":       side,
        "type":       "MARKET",
        "quantity":   abs_qty,
        "reduceOnly": "true"
    })

# ─── 펌프 스크리너 ─────────────────────────────────────────────────────────
def get_7d_change(symbol: str) -> float:
    """7일 가격 변화율 계산 (1일봉 8개 사용)"""
    try:
        klines = get("/fapi/v1/klines", {
            "symbol":   symbol,
            "interval": "1d",
            "limit":    8
        })
        if len(klines) < 8:
            return 0.0
        open_price  = float(klines[0][1])
        close_price = float(klines[-1][4])
        if open_price == 0:
            return 0.0
        return (close_price - open_price) / open_price * 100
    except Exception:
        return 0.0

def scan_pumped_symbols() -> list:
    """7일 상승률 > PUMP_THRESHOLD_7D 인 심볼 목록 반환"""
    symbols  = all_usdt_perp_symbols()
    pumped   = []
    log.info(f"펌프 스캔 시작: {len(symbols)}개 심볼")
    for i, sym in enumerate(symbols):
        chg = get_7d_change(sym)
        if chg >= PUMP_THRESHOLD_7D:
            pumped.append((sym, chg))
            log.info(f"  펌핑 감지: {sym} +{chg:.1f}%")
        if (i + 1) % 50 == 0:
            log.info(f"  스캔 진행: {i+1}/{len(symbols)}")
        time.sleep(0.05)  # rate limit 방지
    pumped.sort(key=lambda x: x[1], reverse=True)
    log.info(f"펌핑 심볼 {len(pumped)}개 발견")
    return pumped

# ─── 덤프 감지 ────────────────────────────────────────────────────────────
def get_klines(symbol: str, limit: int = 100) -> list:
    """1분봉 캔들 리스트 반환 [[o, h, l, c, v], ...]"""
    raw = get("/fapi/v1/klines", {
        "symbol":   symbol,
        "interval": CANDLE_INTERVAL,
        "limit":    limit
    })
    return [[float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
            for k in raw]

def detect_dump(symbol: str) -> bool:
    """
    덤프 진입 신호 감지
    조건:
      1. 현재 가격이 최근 LOOKBACK_CANDLES 내 고점 대비 DUMP_FROM_HIGH_PCT% 이상 하락
      2. 직전 완성 캔들이 BEARISH_BODY_PCT% 이상 음봉
      3. 직전 완성 캔들 거래량이 평균 거래량의 VOLUME_MULTIPLIER배 이상
    """
    try:
        needed = max(LOOKBACK_CANDLES, VOL_AVG_PERIOD) + 5
        klines = get_klines(symbol, needed)
        if len(klines) < needed:
            return False

        # 최근 완성된 캔들들 (마지막 캔들은 미완성이므로 제외)
        closed = klines[:-1]

        # 최근 고점 (LOOKBACK_CANDLES 내)
        lookback = closed[-LOOKBACK_CANDLES:]
        recent_high = max(c[1] for c in lookback)  # high 기준

        # 현재 가격 (마지막 완성 캔들 종가)
        current_price = closed[-1][3]

        # 조건 1: 고점 대비 하락률
        drop_pct = (recent_high - current_price) / recent_high * 100
        if drop_pct < DUMP_FROM_HIGH_PCT:
            return False

        # 조건 2: 직전 캔들 음봉 크기
        last = closed[-1]
        o, h, l, c, v = last
        if c >= o:  # 양봉이면 패스
            return False
        body_pct = (o - c) / o * 100
        if body_pct < BEARISH_BODY_PCT:
            return False

        # 조건 3: 거래량 급증
        vol_avg = sum(c[4] for c in closed[-VOL_AVG_PERIOD-1:-1]) / VOL_AVG_PERIOD
        if vol_avg == 0:
            return False
        if v < vol_avg * VOLUME_MULTIPLIER:
            return False

        log.info(
            f"  [{symbol}] 덤프 신호! "
            f"고점대비={drop_pct:.2f}% "
            f"음봉={body_pct:.2f}% "
            f"거래량배율={v/vol_avg:.1f}x"
        )
        return True

    except Exception as e:
        log.warning(f"{symbol} 덤프 감지 오류: {e}")
        return False

# ─── 핵심 상태 ───────────────────────────────────────────────────────────
lock               = Lock()
pumped_watchlist   = {}    # {symbol: 7d_chg}
open_positions     = {}    # {symbol: {entry, tp, sl, qty}}
last_rescan_time   = 0
tg_ctrl_running    = True  # Telegram 원격 제어용
traded_symbols     = set() # {symbol} 한 번 숏 진입한 심볼은 재진입하지 않음

STATE_FILE = os.path.join(os.path.dirname(__file__), "traded_symbols.json")

def load_traded_symbols():
    global traded_symbols
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            traded_symbols = set(map(str, data))
            log.info(f"진입 완료 심볼 로드: {len(traded_symbols)}개")
    except Exception as e:
        log.warning(f"진입 완료 심볼 로드 실패: {e}")

def save_traded_symbols():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(traded_symbols), f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"진입 완료 심볼 저장 실패: {e}")

# ─── TP/SL 폴링 스레드 ──────────────────────────────────────────────────
def poll_tp_sl():
    """열린 포지션에 대해 mark price 폴링으로 TP/SL 체크"""
    log.info("TP/SL 폴링 스레드 시작")
    while True:
        time.sleep(POLL_INTERVAL_SEC)
        with lock:
            symbols = list(open_positions.keys())

        for sym in symbols:
            try:
                mark = get_mark_price(sym)
                with lock:
                    pos = open_positions.get(sym)
                    if pos is None:
                        continue
                    tp, sl, qty = pos["tp"], pos["sl"], pos["qty"]

                hit = None
                if mark <= tp:
                    hit = "TP"
                elif mark >= sl:
                    hit = "SL"

                if hit:
                    closed_ok = False
                    try:
                        close_position_market(sym, qty)
                        closed_ok = True
                        pnl_pct = (pos["entry"] - mark) / pos["entry"] * 100
                        msg = (
                            f"{'✅' if hit=='TP' else '❌'} <b>{hit} 청산</b>\n"
                            f"심볼: {sym}\n"
                            f"진입가: {pos['entry']:.6g}\n"
                            f"청산가: {mark:.6g}\n"
                            f"손익: {pnl_pct:+.2f}%"
                        )
                        tg(msg)
                        log.info(f"{sym} {hit} 청산 @ {mark}")
                    except Exception as e:
                        log.error(f"{sym} 청산 실패: {e}")
                    if closed_ok:
                        with lock:
                            open_positions.pop(sym, None)

            except Exception as e:
                log.warning(f"{sym} 폴링 오류: {e}")

# ─── 펌프 재스캔 스레드 ─────────────────────────────────────────────────
def rescan_loop():
    """주기적으로 펌핑 심볼 목록 갱신"""
    global last_rescan_time
    while True:
        log.info("펌프 목록 스캔 시작...")
        result = scan_pumped_symbols()
        with lock:
            pumped_watchlist.clear()
            for sym, chg in result:
                pumped_watchlist[sym] = chg
        last_rescan_time = time.time()
        tg(
            f"🔍 <b>펌프 목록 갱신</b>\n"
            f"감시 중: {len(pumped_watchlist)}개 심볼\n"
            f"상위 5: " + ", ".join(f"{s}(+{c:.0f}%)" for s, c in result[:5])
        )
        time.sleep(PUMP_RESCAN_SEC)

# ─── Telegram 원격 제어 ─────────────────────────────────────────────────
def tg_control():
    """
    지원 명령어:
      /status  - 현재 상태 조회
      /list    - 감시 목록 상위 10개
      /close <심볼> - 수동 청산
      /stop    - 봇 종료
    """
    offset = None
    log.info("Telegram 제어 스레드 시작")
    while tg_ctrl_running:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            resp = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                params=params, timeout=35
            ).json()
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message", {}).get("text", "")
                if not msg.startswith("/"):
                    continue

                parts = msg.split()
                cmd   = parts[0].lower()

                if cmd == "/status":
                    with lock:
                        n_pos = len(open_positions)
                        n_watch = len(pumped_watchlist)
                    bal = get_balance()
                    tg(
                        f"📊 <b>봇 상태</b>\n"
                        f"잔고: ${bal:.2f} USDT\n"
                        f"오픈 포지션: {n_pos}/{MAX_POSITIONS}\n"
                        f"감시 심볼: {n_watch}개"
                    )

                elif cmd == "/list":
                    with lock:
                        items = sorted(pumped_watchlist.items(), key=lambda x: x[1], reverse=True)[:10]
                    text = "📋 <b>감시 목록 (7일 상승률 상위 10)</b>\n"
                    for s, c in items:
                        in_pos = "🔴" if s in open_positions else "⚪"
                        text += f"{in_pos} {s}: +{c:.1f}%\n"
                    tg(text)

                elif cmd == "/close" and len(parts) >= 2:
                    sym = parts[1].upper()
                    with lock:
                        pos = open_positions.get(sym)
                    if pos:
                        try:
                            close_position_market(sym, pos["qty"])
                            with lock:
                                open_positions.pop(sym, None)
                            tg(f"✅ {sym} 수동 청산 완료")
                        except Exception as e:
                            tg(f"❌ {sym} 청산 실패: {e}")
                    else:
                        tg(f"⚠️ {sym} 열린 포지션 없음")

                elif cmd == "/stop":
                    tg("🛑 봇 종료 중...")
                    log.info("원격 종료 명령 수신")
                    os._exit(0)

        except Exception as e:
            log.warning(f"TG 제어 오류: {e}")
            time.sleep(5)

# ─── 메인 신호 루프 ─────────────────────────────────────────────────────
def signal_loop():
    """감시 목록 심볼들을 순회하며 덤프 신호 체크 및 진입"""
    log.info("신호 루프 시작")
    while True:
        with lock:
            watchlist = dict(pumped_watchlist)
            current_pos = dict(open_positions)

        if not watchlist:
            time.sleep(10)
            continue

        for sym, chg_7d in watchlist.items():
            # 이미 포지션 보유 중인 심볼 스킵
            if sym in current_pos:
                continue
            # 한 번 진입한 심볼은 끝(재진입 금지)
            if sym in traded_symbols:
                continue

            # 최대 포지션 수 체크
            with lock:
                if len(open_positions) >= MAX_POSITIONS:
                    break

            # 덤프 신호 체크
            if not detect_dump(sym):
                time.sleep(0.1)
                continue

            # 진입 시도
            try:
                # 레버리지 / 마진 타입 설정
                set_margin_type(sym)
                set_leverage(sym)

                bal     = get_balance()
                usdt_in = bal * RISK_PER_TRADE
                entry   = get_mark_price(sym)
                tp      = entry * (1 - TP_PCT / 100)
                sl      = entry * (1 + SL_PCT / 100)

                place_result = place_market_short(sym, usdt_in)
                # 주문에 넣은 qty를 그대로 사용(청산 시 quantity=0.0 문제 방지)
                qty = -abs(float(place_result["qty"]))

                with lock:
                    open_positions[sym] = {
                        "entry": entry,
                        "tp":    tp,
                        "sl":    sl,
                        "qty":   qty
                    }
                    traded_symbols.add(sym)
                    save_traded_symbols()

                msg = (
                    f"🔴 <b>숏 진입</b>\n"
                    f"심볼: {sym} (7일 +{chg_7d:.0f}%)\n"
                    f"진입가: {entry:.6g}\n"
                    f"TP: {tp:.6g} (-{TP_PCT}%)\n"
                    f"SL: {sl:.6g} (+{SL_PCT}%)\n"
                    f"사용금액: ${usdt_in:.2f} USDT"
                )
                tg(msg)
                log.info(f"숏 진입: {sym} @ {entry}")

            except Exception as e:
                log.error(f"{sym} 진입 실패: {e}")

            time.sleep(0.2)

        # 한 사이클 완료 후 잠시 대기
        time.sleep(5)

# ─── 시작 시 포지션 복구 ─────────────────────────────────────────────────
def recover_positions():
    """봇 재시작 시 기존 오픈 포지션 복구"""
    log.info("포지션 복구 스캔...")
    positions = get_open_positions()
    recovered = 0
    for sym, pos in positions.items():
        if pos["side"] == "SHORT":
            entry = pos["entry"]
            tp    = entry * (1 - TP_PCT / 100)
            sl    = entry * (1 + SL_PCT / 100)
            with lock:
                open_positions[sym] = {
                    "entry": entry,
                    "tp":    tp,
                    "sl":    sl,
                    "qty":   pos["amt"]
                }
                # 복구된 포지션도 "이미 진입한 코인"으로 간주(재진입 방지)
                traded_symbols.add(sym)
            recovered += 1
            log.info(f"  복구: {sym} @ {entry}")
    if recovered:
        save_traded_symbols()
        tg(f"♻️ {recovered}개 숏 포지션 복구 완료")

# ─── 중복 실행 방지 ──────────────────────────────────────────────────────
def acquire_lock():
    fl = open(LOCKFILE, "w")
    try:
        fcntl.flock(fl, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("이미 실행 중인 봇 인스턴스 감지. 종료합니다.")
        sys.exit(1)
    return fl

# ─── 메인 ────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("Post-Pump Short Bot 시작")
    log.info(f"  7일 상승 임계값 : {PUMP_THRESHOLD_7D}%")
    log.info(f"  최근 고점 대비 하락 : {DUMP_FROM_HIGH_PCT}%")
    log.info(f"  음봉 최소 크기 : {BEARISH_BODY_PCT}%")
    log.info(f"  거래량 배율 : {VOLUME_MULTIPLIER}x")
    log.info(f"  레버리지 : {LEVERAGE}x | TP: {TP_PCT}% | SL: {SL_PCT}%")
    log.info("=" * 60)

    # 중복 방지 락
    lock_file = acquire_lock()

    # 거래소 정보 로드
    load_exchange_info()

    # 이전에 진입 완료한 심볼 로드(재진입 방지용)
    load_traded_symbols()

    # 포지션 복구
    recover_positions()

    tg("🚀 <b>Post-Pump Short Bot 시작</b>\n초기 펌프 스캔을 시작합니다...")

    # 스레드 시작
    Thread(target=rescan_loop, daemon=True).start()

    # 첫 스캔 완료 대기
    log.info("첫 번째 펌프 스캔 완료 대기 중...")
    while not pumped_watchlist:
        time.sleep(5)

    Thread(target=poll_tp_sl,   daemon=True).start()
    Thread(target=tg_control,   daemon=True).start()

    # 신호 루프 (메인 스레드)
    signal_loop()

if __name__ == "__main__":
    main()
