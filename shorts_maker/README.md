# 30초 쇼츠 자동 편집 (Windows)

OBS로 녹화한 바이낸스 PC 앱 화면에서, 그날 **청산 체결 중 최대 수익 2개 + 최대 손실 1개** 순간을 잘라 세로 30초 쇼츠를 만든다.

| 구간 | 내용 |
|---|---|
| 0~3초 | 오늘 결과 (DAY N, 순손익, 달성 여부) |
| 3~24초 | 하이라이트 3개 × 7초. 체결 40초 전부터 3초 후까지를 약 6배속으로 재생하고, 체결 시각·가격·손익을 자막으로 넣는다 |
| 24~30초 | 결과 카드 (순손익, 목표 바, 체결/승패, 누적, 운용 시드) |

녹화본에 들어 있는 하이라이트가 2개 이하면 클립 길이를 늘려서 30초를 맞춘다.

## 1. 설치 (한 번만)
PowerShell에서 실행한다.
```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install OBSProject.OBSStudio
```
설치가 끝나면 PowerShell 창을 새로 열고 아래를 실행한다.
```powershell
git clone https://github.com/gazuaaat/soxl1000.git $HOME\soxl1000
cd $HOME\soxl1000\shorts_maker
pip install -r requirements.txt
```

## 2. OBS 설정 (한 번만)
1. **설정 → 출력 → 녹화**
   - 녹화 경로: `C:\Users\<이름>\Videos` (config.json의 `rec_dir`과 같게)
   - 녹화 형식: **mkv** (녹화 중 PC가 꺼져도 파일이 살아남는다)
   - 인코더: 하드웨어(NVENC/QuickSync/AMF)가 있으면 그것을 고른다
2. **설정 → 고급 → 녹화 → 파일 이름 형식**: 기본값 `%CCYY-%MM-%DD %hh-%mm-%ss`를 **바꾸지 않는다.** 스크립트가 파일 이름에서 녹화 시작 시각을 읽는다.
3. **설정 → 비디오**: 해상도 1920×1080, FPS **15** (차트에는 충분하고 용량이 1/2~1/4로 준다)
4. **소스 추가**
   - **윈도우 캡처 → Binance** (모니터 전체가 아닌 바이낸스 창만 녹화. 카톡이나 다른 계좌가 찍히지 않는다)
   - **브라우저 → URL** `https://gazuaaat.github.io/soxl1000/clock.html`, 너비 520, 높이 90 → 화면 구석에 배치. 초 단위 KST 시계가 영상에 박혀서 웹페이지 체결 시각과 대조할 수 있다
5. 바이낸스 앱에서 **잔고 숨기기(눈 아이콘)** 를 켠다. 본계정 전체 자산이 영상에 나오지 않게 한다.
6. 매매를 시작할 때 녹화 시작, 끝나면 중지. 여러 파일로 나뉘어도 된다.

## 3. 자를 영역 정하기 (한 번만)
```powershell
python make_short.py --frame
```
`Videos\soxl_shorts\frame.png`가 생긴다. 그림판으로 열어 **차트와 호가가 보이는 영역**의 왼쪽 위 좌표(x, y)와 너비, 높이를 확인하고, `config.json`의 `"crop": [x, y, 너비, 높이]`에 넣는다.
- 세로 쇼츠라서 **너비:높이가 1:1에 가까울수록** 화면에 크게 나온다 (예: `[0, 80, 1100, 1000]`).

## 4. 매일
서버가 07:10 KST에 거래 데이터를 올린 뒤 아래를 실행한다.
```powershell
cd $HOME\soxl1000\shorts_maker
python make_short.py              # 어제 세션
python make_short.py 2026-11-24   # 특정 날짜
```
또는 `make_short.bat`를 더블클릭한다. 결과는 `Videos\soxl_shorts\SOXL_DAY{N}_{날짜}.mp4`(30초, 1080×1920)와 결과 카드 PNG다.

## config.json
| 키 | 설명 |
|---|---|
| `rec_dir` | OBS 녹화 폴더 |
| `out_dir` | 완성 쇼츠 저장 폴더 |
| `crop` | 녹화 화면에서 오려낼 영역 `[x, y, 너비, 높이]` |
| `lookback_sec` / `after_sec` | 체결 몇 초 전부터 몇 초 후까지 보여줄지 (기본 40 / 3) |
| `clock_offset_sec` | PC 시계가 틀릴 때 보정값 (초). 영상 속 체결이 늦게 나오면 +, 빠르면 - |
| `bgm` | 배경음악 mp3 경로 (비우면 무음). 저작권 없는 음원만 쓴다 |

---

# 차트 타임랩스 쇼츠 (녹화 없이)
녹화 없이 **거래 데이터만으로** 만드는 30초 영상이다. 그날 SOXLUSDT 5분봉 차트 위에 내 매수(▲)와 매도(▼)를 찍으면서(연속된 같은 방향 체결은 1개로 묶음) 하루를 24초로 빨리 감는다(×3600). 청산하는 순간에는 "매도 $31.42 +$359"처럼 가격과 손익을 띄우고, 아래쪽에는 수수료를 포함한 누적 손익이 올라간다. 마지막 6초는 결과 카드다.

```powershell
python chart_short.py              # 어제 세션
python chart_short.py 2026-11-25   # 특정 날짜
```
- 1분봉은 서버 수집기가 `data/klines/날짜.json`에 함께 저장한다. 2026-11-26 세션부터 자동으로 쌓인다.
- 그 전 날짜는 서버에서 `.venv/bin/python collector/collect.py --klines-only --date 2026-11-25`로 채운다.
- `--fake-klines`를 붙이면 1분봉 없이 체결가로 만든 가짜 차트로 미리보기를 만든다. 공개하면 안 된다.
