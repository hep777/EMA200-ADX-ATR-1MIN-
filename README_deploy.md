## Vultr 도쿄 서버 배포 절차 (systemd)

아래는 봇 폴더를 `/opt/atr_bot`으로 올린다고 가정합니다.

### 1) 서버에 업로드
- 서버에 접속해서 `/opt` 아래 폴더를 만들고 업로드합니다. 예:
```sh
sudo mkdir -p /opt/atr_bot
scp -r "C:/Users/hep77/atr_trading_bot"/* user@SERVER_IP:/opt/atr_bot/
```

### 2) 파이썬/패키지 설치
```sh
sudo apt-get update
sudo apt-get install -y python3 python3-pip
cd /opt/atr_bot
pip3 install --upgrade pip
pip3 install -r requirements.txt
```

### 3) .env 세팅
```sh
cp .env.example .env
nano .env  # BINANCE_API_KEY / BINANCE_API_SECRET / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 입력
```

추가 전략 튜닝 값:
- 기준봉: EMA/RSI/ADX는 5봉 전 값과 순수 대소 비교 (최소 0.1% 기울기 없음)
- `STATE_TIMEOUT_BARS` : 기준봉 이후 `BREAKOUT`(진입 대기) 유지 최대 봉 수(초과 시 `STATE_RESET`). 기본 `7`

### 4) systemd 서비스 등록
```sh
sudo cp atr_bot.service /etc/systemd/system/atr_bot.service
sudo systemctl daemon-reload
sudo systemctl enable atr_bot.service
sudo systemctl restart atr_bot.service
```

### 5) 로그 확인
```sh
sudo journalctl -u atr_bot.service -f --no-pager
```

### 6) GitHub에 올린 뒤 Vultr에 반영하기 (초보자용)

**한 줄 요약:** PC에서 `git push` → 서버에서 `git pull` → 봇 재시작. 텔레그램 토큰은 그대로 `.env`에 있으면 추가 작업 없음.

1. **로컬(PC)** 에서 변경사항 커밋 후 GitHub에 푸시  
   (이미 푸시돼 있으면 생략)

2. **Vultr 서버 SSH 접속** (Windows는 PowerShell 또는 PuTTY)
   ```sh
   ssh 사용자이름@서버IP
   ```

3. **봇 폴더로 이동 후 최신 코드 받기**
   ```sh
   cd /opt/atr_bot
   git pull
   ```
   - `.env`는 Git에 안 올라가므로 **API 키·텔레그램은 서버의 `.env` 그대로** 유지됩니다.

4. **서비스 재시작** (코드 반영)
   ```sh
   sudo systemctl restart atr_bot.service
   ```

5. **정상 동작 확인**
   ```sh
   sudo journalctl -u atr_bot.service -n 50 --no-pager
   ```
   에러 없이 기동되면 OK.

**텔레그램:** 봇이 재시작되면 기존과 같이 알림이 갑니다. 토큰/채팅 ID를 바꾼 게 아니면 **별도 “텔레그램 적용” 설정은 없음** (재시작만 하면 됨).

## 운영 명령어(텔레그램)
- `/status` : 현재 상태
- `/stop` : 신규 진입 중지(기존 포지션은 유지)
- `/restart` : 신규 진입 재개
- `/closeall` : 모든 포지션 시장가 청산 + 로컬 상태 초기화

## 실거래 전 점검 체크리스트
1) API 키 권한 확인
- Futures 거래 권한 ON
- IP 화이트리스트 사용 권장

2) 최소 수량/리스크 확인
- 소액으로 1회 진입/청산 테스트
- `/status`에서 포지션/봇상태 정상 확인

3) 보호주문 확인
- 진입 직후 Binance 주문 탭에서 `STOP_MARKET`(reduceOnly, closePosition) 생성 확인
- 봇 재시작 후 보호주문이 유지/복구되는지 확인

4) CLOSEALL 동작 확인
- 미체결 주문 취소 후 전체 시장가 청산되는지 확인
- 청산 완료 후 포지션 0 확인

5) 운영 모니터링
- `journalctl -u atr_bot.service -f --no-pager`
- 텔레그램 연결 끊김/재연결 알림 수신 확인

