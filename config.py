import os


def _get_env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return float(v)


def _get_env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return int(v)


def _get_env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return v


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
ADX_MIN = _get_env_float("ADX_MIN", 21.0)
ATR_SPIKE_CAP_MULT = _get_env_float("ATR_SPIKE_CAP_MULT", 1.8)

# Default ATR floor (for symbols not listed in ATR_MIN_BY_SYMBOL)
DEFAULT_ATR_MIN = _get_env_float("DEFAULT_ATR_MIN", 0.0)

# Symbol specific ATR floors
ATR_MIN_BY_SYMBOL = {
    "BTCUSDT": _get_env_float("ATR_MIN_BTCUSDT", 130.0),
    "ETHUSDT": _get_env_float("ATR_MIN_ETHUSDT", 40.0),
    "SOLUSDT": _get_env_float("ATR_MIN_SOLUSDT", 1.8),
    "XRPUSDT": _get_env_float("ATR_MIN_XRPUSDT", 0.04),
}

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

