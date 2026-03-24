#!/usr/bin/env bash
# 봇 디렉터리에서 실행. lockfile은 config LOCK_FILE (기본 /tmp/bot.lock)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
nohup python3 -u main.py >> bot.log 2>&1 &
echo "started pid $!"
