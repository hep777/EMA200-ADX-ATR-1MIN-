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

# Filters
ADX_MIN = _get_env_float("ADX_MIN", 25.0)
RSI_LONG_MIN = _get_env_float("RSI_LONG_MIN", 60.0)
RSI_SHORT_MAX = _get_env_float("RSI_SHORT_MAX", 32.0)
ATR_SPIKE_CAP_MULT = _get_env_float("ATR_SPIKE_CAP_MULT", 1.8)

# Default ATR floor (for symbols not listed in ATR_MIN_BY_SYMBOL)
DEFAULT_ATR_MIN = _get_env_float("DEFAULT_ATR_MIN", 0.0)

# ATR floor: 코인별로 다르게 두지 않음
# (요청하신 대로 BTC/ETH/SOL/XRP도 다른 코인처럼 동일하게 처리)
ATR_MIN_BY_SYMBOL = {}

# Stops / trailing
INITIAL_SL_ATR_MULT = _get_env_float("INITIAL_SL_ATR_MULT", 1.5)
TRAILING_ATR_MULT = _get_env_float("TRAILING_ATR_MULT", 3.0)


# ─────────────────────────────────────────────
# Risk / execution
# ─────────────────────────────────────────────

MAX_CONCURRENT_POSITIONS = _get_env_int("MAX_CONCURRENT_POSITIONS", 20)

# Risk 1% of available balance per trade
POSITION_RISK_PCT = _get_env_float("POSITION_RISK_PCT", 0.01)

# Per-symbol leverage policy
DEFAULT_LEVERAGE = _get_env_int("DEFAULT_LEVERAGE", 10)
LEVERAGE_BY_SYMBOL = {
    "BTCUSDT": _get_env_int("LEVERAGE_BTCUSDT", 20),
    "ETHUSDT": _get_env_int("LEVERAGE_ETHUSDT", 15),
    "SOLUSDT": _get_env_int("LEVERAGE_SOLUSDT", 15),
    "XRPUSDT": _get_env_int("LEVERAGE_XRPUSDT", 15),
}

DESIRED_LEVERAGE = DEFAULT_LEVERAGE
MARGIN_TYPE = "ISOLATED"

# Skip symbols (optional)
EXCLUDE_SYMBOLS = [s.strip().upper() for s in os.getenv("EXCLUDE_SYMBOLS", "").split(",") if s.strip()]


# ─────────────────────────────────────────────
# Websocket settings
# ─────────────────────────────────────────────

STREAM_BATCH_SIZE = _get_env_int("STREAM_BATCH_SIZE", 200)


# ─────────────────────────────────────────────
# Runtime
# ─────────────────────────────────────────────

LOCK_FILE = os.getenv("LOCK_FILE", "/tmp/atr_bot.lock")
STATE_FILE = os.getenv("STATE_FILE", "state.json")
LOG_FILE = os.getenv("LOG_FILE", "bot.log")

# Binance 계정/모드에서 STOP_MARKET 엔드포인트 미지원(-4120)일 수 있어
# 기본값은 OFF로 두고 로컬 트레일링 청산만 사용합니다.
ENABLE_SERVER_STOP = _get_env_bool("ENABLE_SERVER_STOP", False)

