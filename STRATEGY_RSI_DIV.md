# RSI 다이버전스 역추세 (1분봉)

## 실행

- 메인: `python3 -u bot.py` 또는 `python3 -u main.py` (동일)
- 잠금 파일: `LOCK_FILE` (기본 `/tmp/bot.lock`)
- 로그: `LOG_FILE` (기본 `bot.log`)

## 유니버스

- USDT 무기한 선물, **24h 거래대금(quoteVolume) 상위 N** (기본 300, `UNIVERSE_TOP_VOLUME`)
- **BTC·ETH 제외** (`EXCLUDE_SYMBOLS` 기본 `BTCUSDT,ETHUSDT`)
- 매일 **KST 09:00** 유니버스 갱신, 텔레그램으로 증감 요약
- 기존 포지션 심볼은 유니버스에서 빠져도 **웹소켓 감시 유지**

## 리스크

- 진입: **가용 잔고의 `POSITION_RISK_PCT`** (기본 1%)를 마진으로 사용 → `calculate_quantity`로 수량
- 최대 동시 포지션: `MAX_CONCURRENT_POSITIONS` (20)
- 레버리지: `DEFAULT_LEVERAGE` / `LEVERAGE_*` — `set_isolated_and_leverage` (ISOLATED)

## 전략 요약

- RSI(14), ATR(14), 1분봉 확정봉(`x`)
- **숏**: RSI≥`RSI_SHORT_TRIGGER`(80) 트리거 → 20봉 이내 다이버전스 → 종가 < 직전 20봉 종가 최저 → **다음 봉 시가** 시장가
- **롱**: RSI≤`RSI_LONG_TRIGGER`(20) → 미러 → 종가 > 직전 20봉 종가 최고 → 다음 봉 시가

## TP / SL (거래소 주문)

- 진입 신호 봉의 ATR로:
  - 숏: SL = 진입가 + `SL_ATR_MULT`×ATR, TP = 진입가 − `TP_ATR_MULT`×ATR
  - 롱: SL = 진입가 − …, TP = 진입가 + …
- `ENABLE_SERVER_STOP=true`(기본): `STOP_MARKET` + `TAKE_PROFIT_MARKET` (reduceOnly)
- 미지원 계정은 텔레그램 경고 후 수동 관리

## 환경 변수 (주요)

| 변수 | 기본 | 설명 |
|------|------|------|
| `UNIVERSE_TOP_VOLUME` | 300 | 거래대금 상위 개수 |
| `RSI_SHORT_TRIGGER` | 80 | 숏 트리거 RSI |
| `RSI_LONG_TRIGGER` | 20 | 롱 트리거 RSI |
| `DIV_WINDOW_BARS` | 20 | 트리거 후 다이버전스 허용 봉 수 |
| `BREAKOUT_LOOKBACK_BARS` | 20 | 브레이크 이전 종가 창 |
| `SL_ATR_MULT` | 1.5 | 손절 ATR 배수 |
| `TP_ATR_MULT` | 3.0 | 익절 ATR 배수 |
| `POSITION_RISK_PCT` | 0.01 | 잔고 대비 마진 비율 |
| `LOCK_FILE` | /tmp/bot.lock | 단일 인스턴스 잠금 |

## 이전 EMA/ADX 전략

- `strategy.py` / 구 `main` 로직은 더 이상 `main.py`에서 사용하지 않습니다.
- 필요 시 Git 히스토리에서 복구하세요.
