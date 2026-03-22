"""
레거시 진입점 — RSI 다이버전스 전략은 `bot.py`에서 실행합니다.
systemd ExecStart가 main.py를 가리키는 경우 그대로 동작합니다.
"""

from bot import main

if __name__ == "__main__":
    main()
