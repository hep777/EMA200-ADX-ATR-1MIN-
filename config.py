import os


def _get_env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    v = v.strip()
    # allow quoting like BINANCE_API_KEY="xxxx" (for robustness)
    if len(v) >= 2 and ((v[0] == v[-1]) and v[0] in ("'", '"')):
        v = v[1:-1].strip()
    return float(v)


def _get_env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    v = v.strip()
    if len(v) >= 2 and ((v[0] == v[-1]) and v[0] in ("'", '"')):
        v = v[1:-1].strip()
    return int(v)


def _get_env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    # .env values sometimes get written as BINANCE_API_KEY="xxxxx"
    # If we don't strip quotes, Binance returns "API-key format invalid (401)".
    if len(v) >= 2 and ((v[0] == v[-1]) and v[0] in ("'", '"')):
        v = v[1:-1].strip()
    return v


def _get_env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    s = v.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


# ─────────────────────────────────────────────
# Secrets (must be set via environment)
# ─────────────────────────────────────────────

BINANCE_API_KEY = _get_env_str("BINANCE_API_KEY")
BINANCE_API_SECRET = _get_env_str("BINANCE_API_SECRET")

TELEGRAM_BOT_TOKEN = _get_env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get_env_str("TELEGRAM_CHAT_ID")


def validate_secrets() -> None:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise RuntimeError("Missing BINANCE_API_KEY / BINANCE_API_SECRET in environment variables.")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in environment variables.")


# ─────────────────────────────────────────────
# Strategy constants (A strategy)
# ─────────────────────────────────────────────

EMA_PERIOD = _get_env_int("EMA_PERIOD", 200)
ATR_PERIOD = _get_env_int("ATR_PERIOD", 14)
ADX_PERIOD = _get_env_int("ADX_PERIOD", 14)

# Basis candle threshold: close >= EMA + ATR*k (long), close <= EMA - ATR*k (short)
EMA_ATR_OFFSET_MULT = _get_env_float("EMA_ATR_OFFSET_MULT", 0.9)

# Confirmation must happen within N closed candles after basis.
CONFIRM_WITHIN_BARS = _get_env_int("CONFIRM_WITHIN_BARS", 5)

# 기준봉 이후 BREAKOUT(진입 대기) 만료: (bar_index - basis_bar) > 이 값 (기본 7봉).
STATE_TIMEOUT_BARS = _get_env_int("STATE_TIMEOUT_BARS", 7)

# Filters
ADX_MIN = _get_env_float("ADX_MIN", 25.0)
RSI_LONG_MIN = _get_env_float("RSI_LONG_MIN", 60.0)
RSI_SHORT_MAX = _get_env_float("RSI_SHORT_MAX", 32.0)
ATR_SPIKE_CAP_MULT = _get_env_float("ATR_SPIKE_CAP_MULT", 1.8)

# 변동성 필터: 직전 N봉 ATR의 중앙값 대비 확장 + (선택) ATR/종가 하한
VOL_ATR_MEDIAN_WINDOW = _get_env_int("VOL_ATR_MEDIAN_WINDOW", 20)
VOL_ATR_MEDIAN_MULT = _get_env_float("VOL_ATR_MEDIAN_MULT", 1.15)
# ATR/종가 ≥ 이 값일 때만 진입 (초저변동 컷). 0 이면 비활성.
VOL_ATR_MIN_PCT_OF_CLOSE = _get_env_float("VOL_ATR_MIN_PCT_OF_CLOSE", 0.0002)

# Default ATR floor (for symbols not listed in ATR_MIN_BY_SYMBOL)
DEFAULT_ATR_MIN = _get_env_float("DEFAULT_ATR_MIN", 0.0)

# ATR floor: 코인별로 다르게 두지 않음
# (요청하신 대로 BTC/ETH/SOL/XRP도 다른 코인처럼 동일하게 처리)
ATR_MIN_BY_SYMBOL = {}

# Stops / trailing
# 기준봉 저/고에서 구조 SL 버퍼: 저가 − BASIS_SL_ATR_MULT×ATR (롱) 등
BASIS_SL_ATR_MULT = _get_env_float("BASIS_SL_ATR_MULT", 0.5)
# 진입가에서 최소 이 ATR 배수만큼은 손절 거리 보장 (롱: entry − k×ATR, 숏: entry + k×ATR)
ENTRY_SL_MIN_ATR_MULT = _get_env_float("ENTRY_SL_MIN_ATR_MULT", 1.0)
# 고점/저점에서 이 ATR 배수만큼 떨어진 곳에 트레일 SL (main.py 모니터 루프)
TRAILING_ATR_MULT = _get_env_float("TRAILING_ATR_MULT", 3.0)

# 트레일 ON: 진입가 ± (R × 이 배수) 도달 시부터 ATR 트레일 계산
TRAIL_ACTIVATE_R_MULT = _get_env_float("TRAIL_ACTIVATE_R_MULT", 0.6)
# 본절 잠금: 진입가 ± (R × 이 배수) 도달 시 손절선을 진입가 근처로 끌어올림 (수익 후 전부 반납 완화)
BREAKEVEN_LOCK_R_MULT = _get_env_float("BREAKEVEN_LOCK_R_MULT", 0.5)
# 본절 SL을 진입가 + (이 배수 × ATR) 근처로 (롱·숏 동일: 진입가 위쪽에 본절 라인)
BREAKEVEN_ATR_MULT = _get_env_float("BREAKEVEN_ATR_MULT", 0.25)


# ─────────────────────────────────────────────
# Risk / execution
# ─────────────────────────────────────────────

MAX_CONCURRENT_POSITIONS = _get_env_int("MAX_CONCURRENT_POSITIONS", 20)

# Risk 1% of available balance per trade
POSITION_RISK_PCT = _get_env_float("POSITION_RISK_PCT", 0.01)

# Per-symbol leverage policy (ISOLATED). Unlisted symbols use DEFAULT_LEVERAGE.
DEFAULT_LEVERAGE = _get_env_int("DEFAULT_LEVERAGE", 5)
LEVERAGE_BY_SYMBOL = {
    "BTCUSDT": _get_env_int("LEVERAGE_BTCUSDT", 50),
    "ETHUSDT": _get_env_int("LEVERAGE_ETHUSDT", 30),
    "XRPUSDT": _get_env_int("LEVERAGE_XRPUSDT", 30),
    "SOLUSDT": _get_env_int("LEVERAGE_SOLUSDT", 30),
    "BNBUSDT": _get_env_int("LEVERAGE_BNBUSDT", 30),
}

DESIRED_LEVERAGE = DEFAULT_LEVERAGE
MARGIN_TYPE = "ISOLATED"

# Skip symbols (optional)
EXCLUDE_SYMBOLS = [s.strip().upper() for s in os.getenv("EXCLUDE_SYMBOLS", "").split(",") if s.strip()]

# 유니버스: 총합 ≤ UNIVERSE_MAX_TOTAL
# 1) 24h 거래대금(quoteVolume) 상위 UNIVERSE_VOLUME_TOP_N — 틱 필터 없이 무조건 포함
# 2) 나머지 슬롯: 24h 상승률 상위 UNIVERSE_GAINER_POOL개 풀에서만 고름, 틱≥UNIVERSE_MAX_TICK_PCT 제외
UNIVERSE_MAX_TOTAL = _get_env_int("UNIVERSE_MAX_TOTAL", 300)
UNIVERSE_VOLUME_TOP_N = _get_env_int("UNIVERSE_VOLUME_TOP_N", 20)
UNIVERSE_GAINER_POOL = _get_env_int("UNIVERSE_GAINER_POOL", 300)
UNIVERSE_MAX_TICK_PCT = _get_env_float("UNIVERSE_MAX_TICK_PCT", 0.004)

# Pull SL slightly past Binance liquidation toward the safe side (avoid liq before stop)
LIQ_STOP_BUFFER_PCT = _get_env_float("LIQ_STOP_BUFFER_PCT", 0.002)


# ─────────────────────────────────────────────
# Websocket settings
# ─────────────────────────────────────────────

STREAM_BATCH_SIZE = _get_env_int("STREAM_BATCH_SIZE", 200)

# WS 마크가 이 초 이상 갱신 없으면 포지션 감시에 REST 마크 사용 (0 = 갱신 없음일 때만 REST)
MARK_PRICE_WS_STALE_SEC = _get_env_float("MARK_PRICE_WS_STALE_SEC", 30.0)
# 심볼당 REST 마크 최소 호출 간격(초) — 레이트리밋 완화
MARK_PRICE_REST_MIN_INTERVAL_SEC = _get_env_float("MARK_PRICE_REST_MIN_INTERVAL_SEC", 3.0)
# 연결 끊김 텔레그램 알람 최소 간격(초). 0 이면 매번 전송
WS_DISCONNECT_TELEGRAM_COOLDOWN_SEC = _get_env_float("WS_DISCONNECT_TELEGRAM_COOLDOWN_SEC", 180.0)
WEBSOCKET_PING_INTERVAL = _get_env_int("WEBSOCKET_PING_INTERVAL", 30)
WEBSOCKET_PING_TIMEOUT = _get_env_int("WEBSOCKET_PING_TIMEOUT", 20)


# ─────────────────────────────────────────────
# Runtime
# ─────────────────────────────────────────────

LOCK_FILE = os.getenv("LOCK_FILE", "/tmp/atr_bot.lock")
STATE_FILE = os.getenv("STATE_FILE", "state.json")
LOG_FILE = os.getenv("LOG_FILE", "bot.log")

# Binance 계정/모드에서 STOP_MARKET 엔드포인트 미지원(-4120)일 수 있어
# 기본값은 OFF로 두고 로컬 트레일링 청산만 사용합니다.
ENABLE_SERVER_STOP = _get_env_bool("ENABLE_SERVER_STOP", False)

