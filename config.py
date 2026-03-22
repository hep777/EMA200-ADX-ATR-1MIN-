"""
BB 스퀴즈 + 밴드 돌파 + RSI + ATR SL 봇.
환경변수(.env) 또는 systemd EnvironmentFile.
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


# Risk / execution (스펙 이름 + 하위 호환)
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

# BB
BB_PERIOD = _get_env_int("BB_PERIOD", 20)
BB_STD = _get_env_float("BB_STD", 2.0)
SQUEEZE_PERIOD = _get_env_int("SQUEEZE_PERIOD", 50)
BB_SQUEEZE_LOOKBACK = _get_env_int("BB_SQUEEZE_LOOKBACK", SQUEEZE_PERIOD)
SQUEEZE_THRESHOLD = _get_env_float("SQUEEZE_THRESHOLD", 1.2)
BB_SQUEEZE_MAX_MULT = _get_env_float("BB_SQUEEZE_MAX_MULT", SQUEEZE_THRESHOLD)

# RSI
RSI_PERIOD = _get_env_int("RSI_PERIOD", 14)
RSI_SLOPE_PERIOD = _get_env_int("RSI_SLOPE_PERIOD", 5)
RSI_SLOPE_BARS = _get_env_int("RSI_SLOPE_BARS", RSI_SLOPE_PERIOD)

# ATR SL
ATR_PERIOD = _get_env_int("ATR_PERIOD", 14)
ATR_MULTIPLIER = _get_env_float("ATR_MULTIPLIER", 0.5)
MAX_SL_PCT = _get_env_float("MAX_SL_PCT", 0.03)

# Polling / universe
MARK_PRICE_POLL_INTERVAL = _get_env_float("MARK_PRICE_POLL_INTERVAL", 3.0)
MARK_POLL_INTERVAL_SEC = MARK_PRICE_POLL_INTERVAL

SYMBOL_REFRESH_INTERVAL = _get_env_int("SYMBOL_REFRESH_INTERVAL", 86400)
UNIVERSE_TOP_N = _get_env_int("UNIVERSE_TOP_N", 300)

API_MAX_RETRIES = _get_env_int("API_MAX_RETRIES", 3)

# Runtime
LOCK_FILE = _get_env_str("LOCK_FILE", "/tmp/bot.lock")
STATE_FILE = _get_env_str("STATE_FILE", "state.json")
LOG_FILE = _get_env_str("LOG_FILE", "bot.log")

STREAM_BATCH_SIZE = _get_env_int("STREAM_BATCH_SIZE", 100)
WEBSOCKET_PING_INTERVAL = _get_env_int("WEBSOCKET_PING_INTERVAL", 30)
WEBSOCKET_PING_TIMEOUT = _get_env_int("WEBSOCKET_PING_TIMEOUT", 20)

KLINES_BOOTSTRAP_LIMIT = _get_env_int("KLINES_BOOTSTRAP_LIMIT", 250)
