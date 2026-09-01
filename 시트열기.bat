@echo off
cd /d "%~dp0"
echo.
echo   시트 열기 (영상이 화면 안에서 재생됩니다)
echo   =========================================
echo.
python adult_local.py serve %1
pause
