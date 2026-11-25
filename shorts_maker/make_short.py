#!/usr/bin/env python3
"""
SOXL 하루 $1,000 - 30초 쇼츠 자동 편집기 (Windows / ffmpeg)

OBS 로 녹화한 바이낸스 화면에서, 그날 체결 기록(GitHub 에 올라간 data/days/날짜.json)의
하이라이트 순간만 잘라 세로 30초 쇼츠(1080x1920 mp4)로 만든다.

  구성: 3초 오프닝(오늘 결과) + 7초 x 3 하이라이트(최대 수익 2 + 최대 손실 1) + 6초 결과 카드

사용법 (PowerShell):
  python make_short.py                  # 어제 세션 (07:00 KST 기준)
  python make_short.py 2026-11-24       # 특정 날짜
  python make_short.py --frame          # 녹화본 한 장면을 PNG로 저장 (자를 영역 좌표 확인용)

설정은 같은 폴더의 config.json (녹화 폴더, 자를 영역 등).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

KST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
W, H, FPS = 1080, 1920, 30
GREEN, RED, YELLOW, WHITE, MUTED = (0, 255, 140), (255, 77, 94), (255, 225, 0), (233, 255, 244), (143, 181, 163)
BG_WIN, BG_LOSS = (6, 18, 13), (18, 6, 10)

HOOK_SEC, END_SEC, N_CLIPS = 3, 6, 3   # 하이라이트 3개면 7초씩


# ------------------------------------------------------------------ 설정 / 데이터
def load_config():
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    cfg["rec_dir"] = Path(cfg["rec_dir"]).expanduser()
    cfg["out_dir"] = Path(cfg["out_dir"]).expanduser()
    return cfg


def default_date():
    now = datetime.now(KST)
    end = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if now < end:
        end -= timedelta(days=1)
    return (end - timedelta(days=1)).strftime("%Y-%m-%d")


def get_json(cfg, rel):
    local = HERE.parent / rel
    if local.exists() and not cfg.get("prefer_remote"):
        return json.loads(local.read_text(encoding="utf-8"))
    url = cfg["data_base_url"].rstrip("/") + "/" + rel
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def load_day(cfg, date):
    day = get_json(cfg, f"data/days/{date}.json")
    idx = get_json(cfg, "data/index.json")
    dates = [d["date"] for d in idx["days"]]
    n = dates.index(date) + 1 if date in dates else len(dates) + 1
    cum = sum(d["net_pnl"] for d in idx["days"] if d["date"] <= date)
    return day, idx["config"], n, cum


def closing_orders(day):
    """부분 체결을 주문 단위로 묶어 실현손익이 있는(청산) 주문만 돌려준다."""
    by = defaultdict(lambda: {"pnl": 0.0, "qty": 0.0, "notional": 0.0, "time": None, "side": None})
    for t in day["trades"]:
        if not t["realized_pnl"]:
            continue
        o = by[t["order_id"]]
        o["pnl"] += t["realized_pnl"]
        o["qty"] += t["qty"]
        o["notional"] += t["price"] * t["qty"]
        o["time"] = max(o["time"] or t["time"], t["time"])
        o["side"] = t["side"]
    out = []
    for oid, o in by.items():
        o["id"] = oid
        o["price"] = o["notional"] / o["qty"]
        o["dt"] = datetime.fromisoformat(o["time"])
        out.append(o)
    return out


# ------------------------------------------------------------------ 녹화 파일
NAME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})-(\d{2})-(\d{2})")


def ffprobe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def recordings(cfg):
    recs = []
    for p in sorted(cfg["rec_dir"].iterdir()) if cfg["rec_dir"].exists() else []:
        if p.suffix.lower() not in (".mkv", ".mp4", ".mov", ".flv"):
            continue
        m = NAME_RE.search(p.name)
        if not m:
            continue
        start = datetime(*map(int, m.groups()), tzinfo=KST) + timedelta(seconds=cfg.get("clock_offset_sec", 0))
        dur = ffprobe_duration(p)
        if dur > 0:
            recs.append({"path": p, "start": start, "end": start + timedelta(seconds=dur)})
    return recs


def locate(recs, t0, t1):
    for r in recs:
        if r["start"] <= t0 and t1 <= r["end"]:
            return r, (t0 - r["start"]).total_seconds()
    return None, None


def pick_highlights(cfg, orders, recs):
    lb, af = cfg["lookback_sec"], cfg["after_sec"]
    covered = []
    for o in orders:
        r, ss = locate(recs, o["dt"] - timedelta(seconds=lb), o["dt"] + timedelta(seconds=af))
        if r:
            covered.append({**o, "rec": r, "ss": ss})
    wins = sorted([o for o in covered if o["pnl"] > 0], key=lambda o: -o["pnl"])
    losses = sorted([o for o in covered if o["pnl"] < 0], key=lambda o: o["pnl"])
    picked = wins[:2] + losses[:1]
    rest = [o for o in wins[2:] + losses[1:] if o not in picked]
    picked += rest[: N_CLIPS - len(picked)]
    return sorted(picked, key=lambda o: o["dt"]), len(covered)


# ------------------------------------------------------------------ 그래픽 (Pillow)
def font(cfg, kind, size):
    for p in cfg["fonts"][kind]:
        fp = (HERE / p) if not Path(p).is_absolute() else Path(p)
        if fp.exists():
            return ImageFont.truetype(str(fp), size)
    return ImageFont.load_default(size)


def money(v, sign=True, d=0):
    s = f"${abs(v):,.{d}f}"
    return ("+" if v > 0 and sign else "-" if v < 0 else "") + s


def text_c(draw, y, s, f, fill, stroke=0, sfill=(0, 0, 0)):
    w = draw.textlength(s, font=f)
    draw.text(((W - w) / 2, y), s, font=f, fill=fill, stroke_width=stroke, stroke_fill=sfill)


def fit_font(cfg, kind, s, size, maxw):
    f = font(cfg, kind, size)
    while ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(s, font=f) > maxw and size > 40:
        size -= 6
        f = font(cfg, kind, size)
    return f


def grid_bg(color):
    im = Image.new("RGB", (W, H), color)
    d = ImageDraw.Draw(im)
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=tuple(min(255, c + 10) for c in color))
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=tuple(min(255, c + 10) for c in color))
    return im


def day_badge(d, cfg, n, color):
    f = font(cfg, "display", 84)
    d.rounded_rectangle([70, 140, 70 + d.textlength(f"DAY {n}", font=f) + 60, 260], 20, fill=WHITE)
    d.text((100, 150), f"DAY {n}", font=f, fill=(6, 18, 13))
    fs = font(cfg, "display", 76)
    d.text((W - 70 - d.textlength("$SOXL", font=fs), 160), "$SOXL", font=fs, fill=WHITE)


def make_hook(cfg, day, n, path):
    pnl = day["net_pnl"]
    im = grid_bg(BG_WIN if pnl >= 0 else BG_LOSS)
    d = ImageDraw.Draw(im)
    day_badge(d, cfg, n, GREEN)
    dt = datetime.strptime(day["date"], "%Y-%m-%d")
    text_c(d, 560, f"{dt.month}월 {dt.day}일 SOXL 매매 결과", font(cfg, "body", 64), MUTED)
    s = money(pnl)
    text_c(d, 700, s, fit_font(cfg, "display", s, 260, W - 100), GREEN if pnl >= 0 else RED, 10)
    msg = "목표 $1,000 달성!" if pnl >= cfg["goal"] else ("수익 마감" if pnl > 0 else "손실 마감")
    text_c(d, 1030, msg, font(cfg, "display", 110), YELLOW if pnl >= cfg["goal"] else WHITE, 6)
    text_c(d, 1260, "어떻게 매매했는지 보여드림 ↓", font(cfg, "body", 54), WHITE)
    im.save(path)


def make_overlay(cfg, day, n, i, o, path):
    """하이라이트 클립 위에 얹는 투명 PNG (위: DAY/제목, 아래: 손익)"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 430], fill=(6, 18, 13, 235))
    d.rectangle([0, 1490, W, H], fill=(6, 18, 13, 235))
    day_badge(d, cfg, n, GREEN)
    win = o["pnl"] > 0
    title = ("최대 수익" if win else "최대 손실") if i < 2 or not win else "수익"
    title = f"#{i + 1} {title}"
    d.text((70, 310), title, font=font(cfg, "display", 80), fill=YELLOW if win else RED)
    side = "매도 체결" if o["side"] == "SELL" else "매수 체결"
    sub = f"{o['dt'].strftime('%H:%M:%S')} KST · {side} ${o['price']:,.2f}"
    text_c(d, 1530, sub, font(cfg, "body", 48), WHITE)
    s = money(o["pnl"], d=2)
    text_c(d, 1610, s, fit_font(cfg, "display", s, 190, W - 100), GREEN if win else RED, 8)
    im.save(path)


def make_end(cfg, day, n, cum, path):
    pnl = day["net_pnl"]
    im = grid_bg(BG_WIN if pnl >= 0 else BG_LOSS)
    d = ImageDraw.Draw(im)
    day_badge(d, cfg, n, GREEN)
    d.text((80, 340), "오늘 순손익", font=font(cfg, "display", 76), fill=WHITE)
    s = money(pnl)
    d.text((70, 440), s, font=fit_font(cfg, "display", s, 230, W - 140), fill=GREEN if pnl >= 0 else RED,
           stroke_width=8, stroke_fill=(0, 0, 0))
    # 목표 바
    pct = pnl / cfg["goal"]
    d.rounded_rectangle([80, 760, W - 80, 814], 27, fill=(0, 0, 0), outline=(60, 80, 70), width=3)
    if pct > 0:
        d.rounded_rectangle([80, 760, 80 + (W - 160) * min(1, pct), 814], 27, fill=GREEN)
    fb = font(cfg, "body", 40)
    d.text((80, 830), f"{pct * 100:.0f}%", font=fb, fill=WHITE)
    d.text((W - 80 - d.textlength("목표 $1,000", font=fb), 830), "목표 $1,000", font=fb, fill=YELLOW)
    if pct >= 1:
        fs = font(cfg, "display", 84)
        d.rounded_rectangle([700, 330, 1010, 450], 24, outline=YELLOW, width=10)
        d.text((735, 345), "달성!", font=fs, fill=YELLOW)
    closes = [t for t in day["trades"] if t["realized_pnl"]]
    fees = day["income"]["commission"] + day["income"]["funding"]
    boxes = [("체결 / 승패", f"{len(day['trades'])}건 · {day['wins']}승{day['losses']}패", WHITE),
             ("수수료+펀딩", money(fees), RED if fees < 0 else GREEN),
             ("누적 손익", money(cum), GREEN if cum >= 0 else RED),
             ("운용 시드", money(day["equity_end"], sign=False), WHITE)]
    fl, fv = font(cfg, "body", 34), font(cfg, "body", 56)
    for k, (lab, val, col) in enumerate(boxes):
        x, y = 80 + (k % 2) * 470, 940 + (k // 2) * 220
        d.rounded_rectangle([x, y, x + 450, y + 190], 28, fill=(0, 0, 0), outline=(50, 70, 60), width=3)
        d.text((x + 32, y + 26), lab, font=fl, fill=MUTED)
        d.text((x + 32, y + 86), val, font=fit_font(cfg, "body", val, 56, 400), fill=col)
    text_c(d, 1440, "전체 매매일지는 프로필 링크", font(cfg, "body", 44), WHITE)
    text_c(d, 1500, cfg["site_url"].replace("https://", ""), font(cfg, "body", 36), MUTED)
    im.save(path)


# ------------------------------------------------------------------ ffmpeg
def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg 실패:\n" + " ".join(map(str, cmd)) + "\n" + r.stderr[-2000:])


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]


def still_clip(png, sec, out):
    run(["ffmpeg", "-y", "-loop", "1", "-t", str(sec), "-i", str(png),
         "-vf", f"scale={W}:{H},fps={FPS}", *ENC, str(out)])


def highlight_clip(cfg, o, overlay_png, out, clip_sec):
    x, y, cw, ch = cfg["crop"]
    src = cfg["lookback_sec"] + cfg["after_sec"]
    speed = src / clip_sec
    vh = int(round(ch * W / cw / 2) * 2)
    vy = 430 + max(0, (1490 - 430 - vh) // 2)
    fc = (f"[0:v]crop={cw}:{ch}:{x}:{y},scale={W}:{vh},setpts=PTS/{speed:.4f},fps={FPS}[v];"
          f"color=c=0x06120d:s={W}x{H}:r={FPS}:d={clip_sec}[bg];"
          f"[bg][v]overlay=0:{vy}:shortest=1[b];[b][1:v]overlay=0:0")
    run(["ffmpeg", "-y", "-ss", f"{o['ss']:.2f}", "-t", str(src), "-i", str(o["rec"]["path"]),
         "-loop", "1", "-i", str(overlay_png), "-filter_complex", fc, "-t", f"{clip_sec:.3f}", *ENC, str(out)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="세션 날짜 YYYY-MM-DD (기본: 어제 세션)")
    ap.add_argument("--frame", action="store_true", help="최근 녹화본에서 한 장면을 PNG로 저장")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg 가 없습니다. PowerShell 에서  winget install Gyan.FFmpeg  실행 후 창을 새로 여세요.")
    cfg = load_config()
    recs = recordings(cfg)

    if args.frame:
        if not recs:
            sys.exit(f"녹화 파일이 없습니다: {cfg['rec_dir']}")
        r = recs[-1]
        out = cfg["out_dir"] / "frame.png"
        cfg["out_dir"].mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", "-ss", "5", "-i", str(r["path"]), "-frames:v", "1", str(out)])
        print(f"저장: {out}\n그림판으로 열어 차트 영역의 왼쪽 위 좌표(x,y)와 너비/높이를 config.json 의 crop 에 넣으세요.")
        return

    date = args.date or default_date()
    day, site_cfg, n, cum = load_day(cfg, date)
    cfg["goal"] = site_cfg["goal_per_day"]
    orders = closing_orders(day)
    picks, covered = pick_highlights(cfg, orders, recs)
    print(f"{date} DAY {n}: 순손익 {money(day['net_pnl'], d=2)} / 청산 주문 {len(orders)}건 중 녹화된 것 {covered}건")
    if not picks:
        sys.exit("녹화본에 들어 있는 청산 체결이 없습니다. 녹화 폴더/파일명/시계(clock_offset_sec)를 확인하세요.")
    for o in picks:
        print(f"  - {o['dt'].strftime('%H:%M:%S')} {o['side']} {money(o['pnl'], d=2)}  ({o['rec']['path'].name})")

    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        parts = []
        make_hook(cfg, day, n, td / "hook.png")
        still_clip(td / "hook.png", HOOK_SEC, td / "p0.mp4"); parts.append(td / "p0.mp4")
        clip_sec = (30 - HOOK_SEC - END_SEC) / len(picks)   # 하이라이트가 적으면 클립을 길게
        for i, o in enumerate(picks):
            make_overlay(cfg, day, n, i, o, td / f"ov{i}.png")
            highlight_clip(cfg, o, td / f"ov{i}.png", td / f"p{i + 1}.mp4", clip_sec); parts.append(td / f"p{i + 1}.mp4")
        make_end(cfg, day, n, cum, td / "end.png")
        still_clip(td / "end.png", END_SEC, td / "p9.mp4"); parts.append(td / "p9.mp4")
        (td / "list.txt").write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        out = cfg["out_dir"] / f"SOXL_DAY{n}_{date}.mp4"
        bgm = cfg.get("bgm")
        if bgm and Path(bgm).exists():
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(td / "list.txt"), "-i", bgm,
                 "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)])
        else:
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(td / "list.txt"), "-c", "copy", str(out)])
        shutil.copy(td / "end.png", cfg["out_dir"] / f"SOXL_DAY{n}_{date}_card.png")
    print(f"완성: {out}  ({ffprobe_duration(out):.1f}초)")


if __name__ == "__main__":
    main()
