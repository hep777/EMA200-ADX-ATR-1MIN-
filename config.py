"""
Trendline breakout/fakeout 1m bot (USDT Perp).
환경변수(.env) 로드.
"""

import os


def _get_env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    v = v.strip()
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
    if len(v) >= 2 and ((v[0] == v[-1]) and v[0] in ("'", '"')):
        v = v[1:-1].strip()
    return v


# Secrets
BINANCE_API_KEY = _get_env_str("BINANCE_API_KEY")
BINANCE_API_SECRET = _get_env_str("BINANCE_API_SECRET")
TELEGRAM_BOT_TOKEN = _get_env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get_env_str("TELEGRAM_CHAT_ID")


def validate_secrets() -> None:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise RuntimeError("Missing BINANCE_API_KEY / BINANCE_API_SECRET")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")


# Risk / execution
LEVERAGE = _get_env_int("LEVERAGE", 10)
DEFAULT_LEVERAGE = LEVERAGE
DESIRED_LEVERAGE = DEFAULT_LEVERAGE
LEVERAGE_BY_SYMBOL: dict = {}

POSITION_SIZE_PCT = _get_env_float("POSITION_SIZE_PCT", 0.01)
POSITION_RISK_PCT = _get_env_float("POSITION_RISK_PCT", POSITION_SIZE_PCT)

MAX_POSITIONS = _get_env_int("MAX_POSITIONS", 30)
MAX_CONCURRENT_POSITIONS = _get_env_int("MAX_CONCURRENT_POSITIONS", MAX_POSITIONS)

EXCLUDE_SYMBOLS = [
    s.strip().upper()
    for s in _get_env_str("EXCLUDE_SYMBOLS", "").split(",")
    if s.strip()
]

# 1단계: 마감 봉 고저폭 / 종가 ≥ 이 값일 때만 추세선·신호 처리
VOLATILITY_MIN_BAR_RANGE_PCT = _get_env_float("VOLATILITY_MIN_BAR_RANGE_PCT", 0.012)

# 추세선 (최외곽 접선, 스윙 좌우 30, 룩백 110)
SWING_LOOKBACK_BARS = _get_env_int("SWING_LOOKBACK_BARS", 110)
SWING_LEFT_BARS = _get_env_int("SWING_LEFT_BARS", 30)
SWING_RIGHT_BARS = _get_env_int("SWING_RIGHT_BARS", 30)
TRENDLINE_MIN_POINTS = _get_env_int("TRENDLINE_MIN_POINTS", 3)
TRENDLINE_TOUCH_TOLERANCE_PCT = _get_env_float("TRENDLINE_TOUCH_TOLERANCE_PCT", 0.001)

VOLUME_AVG_PERIOD = _get_env_int("VOLUME_AVG_PERIOD", 20)

# 트레일링: (신호 봉 고가-저가) × 비율
TRAILING_RANGE_RATIO = _get_env_float("TRAILING_RANGE_RATIO", 0.5)

# 마크 폴링 (초)
MARK_PRICE_POLL_INTERVAL = _get_env_float("MARK_PRICE_POLL_INTERVAL", 3.0)
MARK_POLL_INTERVAL_SEC = MARK_PRICE_POLL_INTERVAL

SYMBOL_REFRESH_INTERVAL = _get_env_int("SYMBOL_REFRESH_INTERVAL", 3600)
UNIVERSE_TOP_N = _get_env_int("UNIVERSE_TOP_N", 300)

API_MAX_RETRIES = _get_env_int("API_MAX_RETRIES", 3)

# Runtime
LOCK_FILE = _get_env_str("LOCK_FILE", "/tmp/bot.lock")
STATE_FILE = _get_env_str("STATE_FILE", "state.json")
LOG_FILE = _get_env_str("LOG_FILE", "bot.log")

STREAM_BATCH_SIZE = _get_env_int("STREAM_BATCH_SIZE", 100)
WEBSOCKET_PING_INTERVAL = _get_env_int("WEBSOCKET_PING_INTERVAL", 30)
WEBSOCKET_PING_TIMEOUT = _get_env_int("WEBSOCKET_PING_TIMEOUT", 20)

KLINES_BOOTSTRAP_LIMIT = _get_env_int("KLINES_BOOTSTRAP_LIMIT", 320)
