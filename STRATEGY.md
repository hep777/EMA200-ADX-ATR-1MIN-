# A-Strategy 최종 정리 (코드 반영 기준)

## 1. 유니버스 (감시 코인) — **총합 ≤ 300**

1. **24h 거래대금(quoteVolume) 상위 20** (`UNIVERSE_VOLUME_TOP_N`): **틱 필터 없이** 무조건 먼저 포함 (실제로 거래소에 있으면).
2. **남은 슬롯**까지 **총 `UNIVERSE_MAX_TOTAL`(기본 300)** 을 넘지 않게 채움.
3. 채울 때 후보는 **24h 상승률 상위 300** (`UNIVERSE_GAINER_POOL`) **풀**만 사용 (전체 시장이 아님).
4. 그 풀을 상승률 순으로 돌면서 **`tickSize / 현재가` ≥ `UNIVERSE_MAX_TICK_PCT`(기본 0.4%)** 이면 **제외**.
5. 거래대금 20에 이미 들어간 심볼은 상승률 쪽에서 **중복 제외**.

→ 실제 개수는 보통 **200~299** 근처(틱에 많이 걸리면 더 적음). **절대 300을 넘지 않음.**

## 2. 타임프레임·지표

- **1분봉 마감** 기준.
- **EMA200**, **ATR14**, **ADX14**, **RSI14** (Wilder).

## 3. 기준봉 (BREAKOUT 이벤트)

한 봉에서 아래를 **모두** 만족하면 그 봉이 **롱/숏 기준봉**이 됨 (RSI 50선 크로스 **없음**).

### 공통

- **변동성 필터:** 현재 봉 `ATR` > **`VOL_ATR_MEDIAN_MULT` × median(직전 `VOL_ATR_MEDIAN_WINDOW`개 봉의 ATR)**.  
  직전 샘플이 창보다 적으면(부트스트랩 직후 등) **통과**.  
  `VOL_ATR_MIN_PCT_OF_CLOSE` > 0 이면 추가로 **`ATR/종가` ≥ 이 값** (초저변동 컷). `0`이면 하한 없음.
- **EMA200**·**RSI**·**ADX** 는 **5봉 전 값과 순수 대소 비교** (최소 0.1% 기울기 없음).
- **ADX** 현재값 ≥ **25** (`ADX_MIN`).
- **ADX**는 롱·숏 모두 **5봉 전보다 큼**: `ADX > ADX(5봉 전)`.

### 롱 기준봉

- `종가 > EMA200`
- `EMA200 > EMA200(5봉 전)`
- `RSI ≥ 60` (`RSI_LONG_MIN`)
- `RSI > RSI(5봉 전)`

### 숏 기준봉

- `종가 < EMA200`
- `EMA200 < EMA200(5봉 전)`
- `RSI ≤ 32` (`RSI_SHORT_MAX`)
- `RSI < RSI(5봉 전)`

기준봉에서 **고가·저가·종가**를 저장하고, 이후 **최대 봉 수 안에서만** 진입 가능 — **일반** 기준봉은 **`STATE_TIMEOUT_BARS`**(기본 **7**), **폭발** 기준봉(`explosive_basis`)은 **`EXPLOSIVE_TIMEOUT_BARS`**(기본 **12**, 대기봉 포함).

## 4. 진입 (기준봉 이후)

- **눌림·추격 없음** — 상태는 **IDLE ↔ BREAKOUT** 만 사용.
- **폭발 기준봉:** 등록 시 `ATR > 직전 N봉 ATR 중앙값 × EXPLOSIVE_ATR_MULT` 이면 `explosive_basis=True`, `explosive_wait_bars=EXPLOSIVE_WAIT_BARS`. **기준봉 이후 그 봉 수만큼** 돌파여도 **진입 무시**. 타임아웃은 **`EXPLOSIVE_TIMEOUT_BARS`**(기본 12) — **대기봉이 이 안에 포함**(예: 대기 2 + 이후 10봉 안 미진입 시 리셋). **일반** 기준봉은 타임아웃 **`STATE_TIMEOUT_BARS`(7)** 유지.
- **과열 진입:** 진입 직전(현재봉) `ATR > 중앙값 × OVERHEATED_ATR_MULT` 이면 해당 진입만 `POSITION_RISK_PCT × OVERHEATED_RISK_RATIO` 로 **마진 축소**.
- **롱:** `종가 > 기준봉 고가` 이고 **그 봉도 변동성 필터 통과** / **숏:** `종가 < 기준봉 저가` 이고 동일.
- 타임아웃·EMA 무효·반대 기준봉 → `STATE_RESET`

## 5. 초기 손절

- **구조 SL:** **롱** `기준봉 저가 − BASIS_SL_ATR_MULT × ATR` / **숏** `기준봉 고가 + BASIS_SL_ATR_MULT × ATR` (기본 `BASIS_SL_ATR_MULT` = 0.5).
- **진입가 최소 거리:** **롱** `진입가 − ENTRY_SL_MIN_ATR_MULT × ATR` / **숏** `진입가 + ENTRY_SL_MIN_ATR_MULT × ATR` (기본 1.0).
- **최종 초기 SL:** 둘 중 **손절이 더 넓은 쪽** — 롱은 가격이 **더 낮은** 값, 숏은 **더 높은** 값 (`min` / `max`).
- 진입가(또는 종가) 반대편으로 SL이 넘어가면 `max(BASIS×ATR, ENTRY_MIN×ATR, 가격×0.1%)` 로 한 번 더 보정.

## 6. 청산 (`main.py` 모니터 루프)

- **마크 가격:** 기본은 WS kline 종가. **끊김·지연**으로 종가가 `MARK_PRICE_WS_STALE_SEC`(초) 이상 갱신되지 않으면 **REST 마크**로 보완(심볼당 `MARK_PRICE_REST_MIN_INTERVAL_SEC` 간격으로 호출 제한). `MARK_PRICE_WS_STALE_SEC=0`이면 WS 값이 있을 때만 WS를 쓰고, 없을 때만 REST.
- **초기 SL** 위반 또는 **트레일 SL** 위반 시 시장가 청산.
- **본절 잠금:** 마크가 진입가에서 **유리한 방향으로 `R × BREAKEVEN_LOCK_R_MULT`**(기본 0.5) 이상 움직이면, 손절선을 **진입가 + `BREAKEVEN_ATR_MULT × ATR`** 근처까지 끌어올림 (롱·숏 동일 레벨) → 이익 후 초기 SL까지 전부 반납하는 경우 완화.
- **트레일 ON:** **`R × TRAIL_ACTIVATE_R_MULT`**(기본 0.6) 도달 시부터 고점/저점 기준 **`TRAILING_ATR_MULT × ATR`** 트레일 적용.

## 7. 환경변수 요약

| 변수 | 의미 |
|------|------|
| `UNIVERSE_MAX_TOTAL` | 감시 코인 **최대 개수** (기본 300) |
| `UNIVERSE_VOLUME_TOP_N` | 24h **거래대금** 상위 N개 고정 포함 (기본 20) |
| `UNIVERSE_GAINER_POOL` | **상승률** 상위 풀 크기 (기본 300) |
| `UNIVERSE_MAX_TICK_PCT` | 상승률 필러만: 틱 ≥ 이 비율이면 제외 (기본 0.004) |
| `STATE_TIMEOUT_BARS` | 기준봉 이후 진입 대기 최대 봉 수 (기본 7) |
| `ADX_MIN` | ADX 하한 (기본 25) |
| `RSI_LONG_MIN` / `RSI_SHORT_MAX` | 60 / 32 |
| `VOL_ATR_MEDIAN_WINDOW` | ATR 중앙값 창 (기본 20) |
| `VOL_ATR_MEDIAN_MULT` | 현재 ATR / 중앙값 배수 (기본 1.15) |
| `VOL_ATR_MIN_PCT_OF_CLOSE` | ATR/종가 최소 (기본 0.0002, 0=끔) |
| `EXPLOSIVE_ATR_MULT` | 폭발 기준봉: ATR > 중앙값×배수 (기본 2.5) |
| `EXPLOSIVE_WAIT_BARS` | 폭발 시 기준봉 이후 진입 금지 봉 수 (기본 2) |
| `EXPLOSIVE_TIMEOUT_BARS` | 폭발 기준봉 전용 진입 대기 최대 봉 수, 대기 포함 (기본 12) |
| `OVERHEATED_ATR_MULT` | 과열: ATR > 중앙값×배수 시 위험 축소 (기본 3) |
| `OVERHEATED_RISK_RATIO` | 과열 시 `POSITION_RISK_PCT`에 곱함 (기본 0.5) |
| `BASIS_SL_ATR_MULT` | 기준봉 구조 SL에 쓰는 ATR 배수 (기본 0.5) |
| `ENTRY_SL_MIN_ATR_MULT` | 진입가에서 최소 손절 거리 ATR 배수 (기본 1.0) |
| `TRAIL_ACTIVATE_R_MULT` | 트레일 시작까지 필요한 R 비율 (기본 0.6) |
| `BREAKEVEN_LOCK_R_MULT` | 본절 잠금까지 필요한 R 비율 (기본 0.5) |
| `BREAKEVEN_ATR_MULT` | 본절 손절선 ≈ 진입가 + 이 배수×ATR (기본 0.25) |
| `TRAILING_ATR_MULT` | 트레일 거리 = ATR×배수 (기본 3) |
| `MARK_PRICE_WS_STALE_SEC` | WS 종가가 이 초 이상 안 오면 REST 마크 사용 (0=WS 없을 때만 REST) |
| `MARK_PRICE_REST_MIN_INTERVAL_SEC` | 심볼당 REST 마크 최소 간격(초) |
| `WS_DISCONNECT_TELEGRAM_COOLDOWN_SEC` | WS 끊김 텔레그램 알림 최소 간격(초, 0=매번) |
| `WEBSOCKET_PING_INTERVAL` / `WEBSOCKET_PING_TIMEOUT` | `run_forever` ping 설정(초) |
