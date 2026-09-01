@echo off
cd /d "%~dp0"
echo.
echo   1단계 - 자막 받아서 붙여넣을 글 만들기
echo   =====================================
echo.
set "URL=%~1"
if not defined URL set /p "URL=유튜브 주소를 붙여넣고 엔터: "
python adult_local.py prep "%URL%"
pause
