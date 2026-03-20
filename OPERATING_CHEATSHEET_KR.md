# 운영 치트시트 (초간단)

## 1) PC(Cursor)에서 코드 수정 후 GitHub 업로드

```powershell
cd C:\Users\hep77\atr_trading_bot
git add .
git commit -m "수정 내용"
git push
```

---

## 2) 서버에 반영

```bash
ssh root@66.42.38.34
cd /opt/atr_bot
git pull origin main
systemctl restart atr_bot.service
systemctl status atr_bot.service
```

정상 기준: `Active: active (running)`

---

## 3) 상태/로그 확인

```bash
systemctl status atr_bot.service
journalctl -u atr_bot.service -f --no-pager
```

---

## 4) 텔레그램 명령

- `/status` : 현재 상태 확인
- `/stop` : 신규 진입 중지
- `/restart` : 신규 진입 재개
- `/closeall` : 전체 포지션 청산

---

## 5) 중복 실행 확인

```bash
ps -ef | grep -E "/opt/atr_bot/main.py" | grep -v grep
```

정상 기준: 1줄만 출력
