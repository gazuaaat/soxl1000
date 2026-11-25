@echo off
REM 더블클릭: 어제 세션 쇼츠 만들기.  특정 날짜는  make_short.bat 2026-11-24
cd /d %~dp0
python make_short.py %*
pause
