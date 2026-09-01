@echo off
cd /d "%~dp0"
echo.
echo   2단계 - 응답.json 으로 시트 만들기
echo   ==================================
echo.
python adult_local.py build
pause
