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
# Strategy constants
# ─────────────────────────────────────────────

RSI_PERIOD = 14

# 1m candle body threshold
BODY_MOVE_PCT = 0.015  # +/- 1.5%

# Tail exception (priority #2)
TAIL_MIN_BODY_PCT = 0.002  # body must be at least 0.2% for tail-exception to apply
TAIL_RATIO = 0.7  # tail / body >= 0.7

# RSI extreme exception (priority #1)
RSI_EXTREME_SHORT = 90  # RSI >= 90 => short
RSI_EXTREME_LONG = 12  # RSI <= 12 => long

# Default RSI (priority #3)
RSI_LONG = 70  # RSI >= 70
RSI_SHORT = 32  # RSI <= 32


# ATR-based exits (priority: maximize holding)
ATR_INTERVAL = os.getenv("ATR_INTERVAL", "5m")
ATR_PERIOD = _get_env_int("ATR_PERIOD", 14)

# Recommended from earlier discussion
SL_ATR_MULT = _get_env_float("SL_ATR_MULT", 2.0)  # fixed stop distance at entry

# Trailing: activate after profit >= ATR * TRAIL_ACT_MULT, then trail at ATR * TRAIL_DIST_MULT
TRAIL_ACT_ATR_MULT = _get_env_float("TRAIL_ACT_ATR_MULT", 1.2)
TRAIL_DIST_ATR_MULT = _get_env_float("TRAIL_DIST_ATR_MULT", 3.0)

# Optional clamp so SL doesn't become too tight in low-volatility regimes.
SL_MIN_DIST_PCT = _get_env_float("SL_MIN_DIST_PCT", 0.0035)  # 0.35%


# ─────────────────────────────────────────────
# Risk / execution
# ─────────────────────────────────────────────

MAX_CONCURRENT_POSITIONS = _get_env_int("MAX_CONCURRENT_POSITIONS", 10)

# Invest 1.5% of current equity as isolated margin per position
POSITION_MARGIN_PCT = _get_env_float("POSITION_MARGIN_PCT", 0.015)

DESIRED_LEVERAGE = _get_env_int("DESIRED_LEVERAGE", 7)
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

