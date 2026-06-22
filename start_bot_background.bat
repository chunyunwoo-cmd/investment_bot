@echo off
chcp 65001 > nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
start "투자봇" /min cmd /c "python main.py >> bot_log.txt 2>&1"
echo 투자봇이 백그라운드로 시작됐습니다.
echo 로그: %~dp0bot_log.txt
