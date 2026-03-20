# ATR 봇 퀵스타트 (집컴/회사컴 공용)

이 문서는 초보자 기준으로, 현재 봇을 운영/업데이트하는 최소 절차만 정리한 문서입니다.

## 1) 현재 상태 (완료된 것)

- 서버: `66.42.38.34`
- 서비스: `atr_bot.service`
- 실행 상태: `systemd` 자동실행(enabled) + 실행중(active)
- 코드 배포 방식: GitHub 연동 완료 (`git push` -> 서버 `git pull`)

---

## 2) 서버 상태 확인 명령

서버 접속:

```powershell
ssh root@66.42.38.34
```

실행 상태 확인:

```bash
systemctl status atr_bot.service
```

자동실행 확인:

```bash
systemctl is-enabled atr_bot.service
```

로그 실시간 보기:

```bash
journalctl -u atr_bot.service -f --no-pager
```

---

## 3) 코드 수정 후 반영 (가장 자주 쓰는 루틴)

### A. PC(Cursor)에서

```powershell
cd C:\Users\hep77\atr_trading_bot
git add .
git commit -m "수정 내용"
git push
```

### B. 서버에서

```bash
cd /opt/atr_bot
git pull origin main
systemctl restart atr_bot.service
systemctl status atr_bot.service
```

---

## 4) 텔레그램 명령어

- `/status` : 현재 상태 확인
- `/stop` : 신규 진입 중지
- `/restart` : 신규 진입 재개
- `/closeall` : 전체 포지션 청산

---

## 5) 문제 생겼을 때

서비스가 실패하면 아래 두 개를 먼저 확인:

```bash
systemctl status atr_bot.service
journalctl -u atr_bot.service -n 100 --no-pager
```

---

## 6) 안전 주의사항

- `.env` 파일(키/시크릿)은 GitHub에 올리지 말 것
- 서버에서 코드 갱신 후 반드시 `systemctl restart` 수행
- 전략 튜닝 시 `.env`의 `EMA_SLOPE_MIN_PCT`(기본 `0.001`)로 EMA200 기울기 민감도 조정 가능
- 중복 실행 의심 시:

```bash
ps -ef | grep -E "/opt/atr_bot/main.py" | grep -v grep
```

정상은 1줄만 출력됨.
