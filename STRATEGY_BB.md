# BB 스퀴즈 + 밴드 돌파 + RSI 기울기 + ATR SL (15분)

## SL 규칙 (요약)

- **초기 SL**: LONG = 돌파봉 저가 − ATR×0.5, SHORT = 돌파봉 고가 + ATR×0.5 (ATR=Wilder 14)
- **최대 SL 캡**: 진입가(마크 추정) 대비 SL까지 손실 비율이 **MAX_SL_PCT(기본 3%)** 초과 시 **진입 SKIP** (텔레그램)
- **체결 후**에도 캡 초과면 즉시 시장가 청산
- **트레일**: 15분 봉마다 마감 기준으로 LONG은 저가−ATR×0.5, SHORT는 고가+ATR×0.5 (SL은 롱만 올림·숏만 내림)
- **청산**: 마크 가격 폴링(기본 3초), 거래소 SL 주문 없음

## 설정 (config.py / .env)

| 변수 | 기본 | 설명 |
|------|------|------|
| `LEVERAGE` | 10 | 레버리지 |
| `POSITION_SIZE_PCT` | 0.01 | 잔고 대비 마진 비율 (1%) |
| `MAX_POSITIONS` | 30 | 최대 동시 포지션 |
| `SQUEEZE_PERIOD` | 50 | 스퀴즈 밴드폭 최소 구간 |
| `SQUEEZE_THRESHOLD` | 1.2 | 현재 밴드폭 ≤ 최솟값×이 값 |
| `RSI_SLOPE_PERIOD` | 5 | RSI 기울기 분모 |
| `ATR_PERIOD` | 14 | ATR |
| `ATR_MULTIPLIER` | 0.5 | SL 거리 = ATR×배수 |
| `MAX_SL_PCT` | 0.03 | 진입가 대비 최대 손실 비율 (캡) |
| `MARK_PRICE_POLL_INTERVAL` | 3 | 마크 체크(초) |
| `SYMBOL_REFRESH_INTERVAL` | 86400 | 유니버스 갱신(초) |

## 실행

`python3 -u bot.py` — 상단 주석에 재시작 절차·`ps aux` 포함.
