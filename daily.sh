#!/bin/bash
# 매일 07:10 KST cron 으로 실행: 10 7 * * * /경로/soxl1000/daily.sh
set -e
cd "$(dirname "$0")"
python3 collector/collect.py
git add data
git commit -m "data: $(date +%F)" || true
git push
