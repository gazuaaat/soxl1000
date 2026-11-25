@echo off
REM 매일 07:10 KST 작업 스케줄러로 실행: 어제 세션 수집 후 GitHub에 올림
cd /d %~dp0
python collector\collect.py || exit /b 1
git add data
git commit -m "data: %date%"
git push
