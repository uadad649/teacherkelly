@echo off
cd /d "%~dp0"
echo.
echo   1단계에서 받아 둔 자막으로 시트까지 만들기
echo   ==========================================
echo   (자막을 다시 받지 않습니다. Claude 에게 바로 물어봅니다.)
echo.
python adult_local.py ask
pause
