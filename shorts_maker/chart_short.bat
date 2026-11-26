@echo off
REM 더블클릭: 어제 세션 차트 타임랩스 쇼츠
cd /d %~dp0
python chart_short.py %*
pause
