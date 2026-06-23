@echo off
chcp 65001 > nul
echo.
echo ============================================
echo   투자봇 초기 설정
echo ============================================
echo.

REM config.yaml 이미 있으면 스킵
if exist config.yaml (
    echo [확인] config.yaml 이미 존재합니다.
    goto START
)

REM 템플릿 복사
copy config.yaml.example config.yaml > nul
echo [완료] config.yaml 생성됨

echo.
echo ============================================
echo   config.yaml 을 메모장으로 엽니다.
echo   투자봇_키모음.txt 에서 각 항목을 복사해
echo   아래 4가지를 채운 뒤 저장하세요:
echo.
echo   telegram:
echo     bot_token: "여기에 입력"
echo     chat_id:   "여기에 입력"
echo.
echo   kis:
echo     app_key:    "여기에 입력"
echo     app_secret: "여기에 입력"
echo     account_no: "여기에 입력"
echo ============================================
echo.
pause
notepad config.yaml

:START
echo.
echo ============================================
echo   패키지 설치 확인 중...
echo ============================================
pip install -r requirements.txt --quiet
echo [완료] 패키지 준비됨
echo.
echo ============================================
echo   투자봇을 시작합니다!
echo ============================================
echo.
set PYTHONIOENCODING=utf-8
python main.py
pause
