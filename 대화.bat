@echo off
cd /d "%~dp0"
echo.
echo   말로 해 보기 - AI 와 음성으로 대화하기
echo   ==========================================
echo   상황을 고르면 복사됩니다.
echo   Claude 앱을 열고 음성모드에 그대로 붙여넣으세요.
echo.
python adult_local.py talk %1
pause
