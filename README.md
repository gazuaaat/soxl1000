# SOXL로 하루 $1,000 벌기: 실계좌 매매일지

바이낸스 **본계정**의 SOXLUSDT 무기한 선물 거래만 골라서 매일 자동으로 수집해서 웹페이지로 공개하고, 쇼츠용 결과 카드도 만든다.

```
collector/collect.py   바이낸스 → data/*.json  (읽기 전용 API)
index.html             공개 대시보드 (GitHub Pages)
shorts.html            쇼츠 마지막 화면용 결과 카드 (1080x1920)
```

> 지금 `data/` 안의 데이터는 **샘플**이다. 실제 수집을 시작하기 전에 지운다(아래 3단계 참고).

## 0. 추천: 기존 싱가포르 서버(portfolio_bot)에서 돌리기
이미 바이낸스 API가 동작하는 DigitalOcean 서버(`ubuntu-s-1vcpu-1gb-sgp1`)에서 같은 키로 수집한다. 집 PC를 켜둘 필요가 없다.

```bash
# 1) bot 사용자로 저장소 받기 (push 용 배포키는 아래 3번)
su - bot
git clone git@github.com:gazuaaat/soxl1000.git ~/soxl1000
cd ~/soxl1000
python3 -m venv .venv && .venv/bin/pip install -r collector/requirements.txt

# 2) portfolio_bot 키 재사용 (config.json 위치는 ls 로 확인)
ls ~/binance/portfolio_bot/config.json
echo "BINANCE_CONFIG_JSON=$HOME/binance/portfolio_bot/config.json" > collector/.env

# 3) 테스트 (어제 세션 수집)
.venv/bin/python collector/collect.py
```
- **push 권한:** 서버에서 `ssh-keygen -t ed25519 -f ~/.ssh/soxl1000 -N ""`로 키를 만든다. `~/.ssh/soxl1000.pub` 내용을 저장소 Settings → Deploy keys에 **Allow write access**를 체크해서 등록한다. `~/.ssh/config`에 아래를 넣는다.
  ```
  Host github.com
    IdentityFile ~/.ssh/soxl1000
  ```
- **매일 자동 실행:** 서버 시간이 UTC라면 07:10 KST는 22:10 UTC다. `crontab -e`에 아래를 추가한다.
  `10 22 * * * cd ~/soxl1000 && .venv/bin/python collector/collect.py && git add data && git commit -qm "data: $(date +\%F)" && git push -q`
- API 키에 **선물(Futures) 읽기 권한**이 있어야 SOXLUSDT 기록이 나온다. 현물 전용 키라면 401/권한 에러가 난다.

## 1. 바이낸스 준비 (서버 대신 집 PC를 쓸 때)
1. 본계정에서 이 용도로 쓸 **읽기 전용 API 키를 새로 발급**한다(권장). 기존 매매용 키를 써도 동작하지만, 키가 유출되면 주문까지 가능해진다.
   - 출금 권한은 절대 켜지 않는다.
   - IP 제한: 스크립트를 돌리는 집 PC의 공인 IP만 허용한다.
2. 본계정의 다른 자산이 공개되지 않도록 `config.json`의 `equity_source`는 `"virtual"`로 둔다.
   - 잔고 = `start_equity` + SOXL 누적 순손익으로 계산하고, 실제 계좌 잔고는 읽지 않는다.
   - 나중에 전용 서브계정을 쓰면 `"account"`로 바꿔 실제 잔고를 공개할 수 있다.
3. `collector/config.json`에서 `start_date`와 `start_equity`(시작 잔고)를 실제 값으로 바꾼다.

## 2. 집 PC 설정 (Windows / Mac)
```bash
cd soxl1000/collector
pip install -r requirements.txt
cp .env.example .env      # .env 에 API 키 입력 (절대 커밋 금지)
```

## 3. 실제 데이터로 시작
```bash
rm data/days/*.json          # 샘플 삭제
python collector/collect.py       # 어제 세션 수집 (07:00 KST 기준)
python collector/collect.py --date 2026-12-01   # 특정 날짜
```
- 하루의 기준은 **07:00 KST부터 다음날 07:00 KST까지**다. 미국장이 끝난 뒤(서머타임 05:00, 평시 06:00 KST)에 집계한다.
- 매일 **07:10 KST**에 작업 스케줄러(Windows)나 cron(Mac)으로 `collect.py`를 실행하고 `git add/commit/push` 한다.
- GitHub Actions는 쓰지 않는다. 바이낸스가 미국 IP를 차단하는데, Actions 서버가 미국에 있다.

## 4. 웹페이지 공개 (GitHub Pages)
- 이 저장소의 Settings → Pages → Source를 `Deploy from a branch`, `main` / `/ (root)`로 설정한다.
- 주소: https://gazuaaat.github.io/soxl1000/
- 쇼츠 영상 링크는 `data/videos.json`에 `"날짜": "URL"` 형식으로 추가한다. 다음 수집 때 반영된다.
- 로컬에서 미리 보기: `python -m http.server` 실행 후 http://localhost:8000

## 5. 매일 자동 실행
- Windows: 작업 스케줄러에서 매일 07:10에 `daily.bat`을 실행한다.
- Mac/Linux: `crontab -e`에 `10 7 * * * /경로/soxl1000/daily.sh`를 추가한다.

## 6. 쇼츠 결과 카드
- 브라우저에서 `shorts.html?date=2026-12-01`을 연다.
- 개발자도구(F12) → 기기 모드에서 1080×1920으로 맞추고 "Capture screenshot"을 누른다.
- 영상 마지막 2~3초에 넣는다. 수익이면 초록, 손실이면 빨강, 목표를 달성하면 "달성!" 도장이 자동으로 바뀐다.
