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

## 운영 명령어(텔레그램)
- `/status` : 현재 상태
- `/stop` : 신규 진입 중지(기존 포지션은 유지)
- `/resume` : 신규 진입 재개
- `/closeall` : 모든 포지션 시장가 청산 + 로컬 상태 초기화
- `/restart` : 봇 프로세스 재시작

