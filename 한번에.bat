@echo off
cd /d "%~dp0"
echo.
echo   성인 영어 - 자막받기부터 시트까지
echo   =================================
echo.
set "URL=%~1"
if not defined URL set /p "URL=유튜브 주소를 붙여넣고 엔터: "
python adult_local.py auto "%URL%"
pause
