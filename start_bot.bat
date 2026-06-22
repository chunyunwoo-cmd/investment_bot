@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================
echo  투자 봇 시작
echo ============================
set PYTHONIOENCODING=utf-8
python main.py
pause
