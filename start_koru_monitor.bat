@echo off
chcp 65001 > nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
start "KORU모니터" /min cmd /c "python koru_monitor.py >> koru_monitor_log.txt 2>&1"
echo KORU 모니터가 백그라운드로 시작됐습니다.
echo 로그: %~dp0koru_monitor_log.txt
pause
