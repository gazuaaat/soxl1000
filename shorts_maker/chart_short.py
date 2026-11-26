#!/usr/bin/env python3
"""
SOXL 하루 $1,000 - 거래내역 차트 타임랩스 쇼츠 (녹화 없이 데이터만으로 생성)

그날 SOXLUSDT 1분봉 위에 내 매수/매도 체결을 표시하면서 하루를 24초로 빨리 감고,
마지막 6초는 결과 카드로 끝나는 30초 세로 영상(1080x1920 mp4)을 만든다.

  python chart_short.py 2026-11-25
  python chart_short.py 2026-11-25 --fake-klines   # 1분봉 데이터가 없을 때 체결가로 만든 가짜 차트 (미리보기용)

필요 데이터: data/days/날짜.json (체결), data/klines/날짜.json (1분봉, 서버 수집기가 함께 저장)
"""
import argparse
import random
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

import make_short as M

W, H, FPS = M.W, M.H, M.FPS
ANIM_SEC, END_SEC = 24, 6
CANDLE_MIN = 5                       # 화면에 그릴 봉 길이(분)
CX0, CX1, CY0, CY1 = 70, 1010, 520, 1330   # 차트 영역
UP, DOWN = (0, 200, 120), (220, 60, 80)


# ------------------------------------------------------------------ 데이터
def load_klines(cfg, date, day, fake):
    if not fake:
        try:
            return M.get_json(cfg, f"data/klines/{date}.json")["klines"]
        except Exception as e:  # noqa
            sys.exit(f"1분봉 데이터(data/klines/{date}.json)가 없습니다. 서버 수집기를 업데이트하거나 --fake-klines 로 미리보기하세요. ({e})")
    # 미리보기용: 체결가를 잇는 경로 + 잡음으로 만든 가짜 1분봉
    rng = random.Random(1)
    s = datetime.fromisoformat(day["session_start"])
    pts = sorted((datetime.fromisoformat(t["time"]), t["price"]) for t in day["trades"])
    out, j, last = [], 0, pts[0][1]
    for m in range(1440):
        t = s + timedelta(minutes=m)
        while j < len(pts) and pts[j][0] <= t + timedelta(minutes=1):
            last = pts[j][1]; j += 1
        o = last * (1 + rng.gauss(0, 0.0012)); c = last * (1 + rng.gauss(0, 0.0012))
        out.append([int(t.timestamp() * 1000), o, max(o, c) * (1 + abs(rng.gauss(0, .001))),
                    min(o, c) * (1 - abs(rng.gauss(0, .001))), c, 0])
    return out


def to_candles(klines, minutes):
    out = []
    for i in range(0, len(klines), minutes):
        g = klines[i:i + minutes]
        out.append({"t": g[0][0] / 1000, "o": float(g[0][1]), "h": max(float(k[2]) for k in g),
                    "l": min(float(k[3]) for k in g), "c": float(g[-1][4])})
    return out


def orders(day):
    by = defaultdict(lambda: {"qty": 0.0, "notional": 0.0, "pnl": 0.0, "fee": 0.0})
    for t in day["trades"]:
        o = by[t["order_id"]]
        o["qty"] += t["qty"]; o["notional"] += t["price"] * t["qty"]; o["pnl"] += t["realized_pnl"]
        o["fee"] += t["fee"]; o["side"] = t["side"]
        o["ts"] = max(o.get("ts", 0), datetime.fromisoformat(t["time"]).timestamp())
    out = [{**o, "price": o["notional"] / o["qty"]} for o in by.values()]
    return sorted(out, key=lambda o: o["ts"])


# ------------------------------------------------------------------ 그리기
def render(cfg, day, n, cum, candles, ords, out_path):
    t0 = datetime.fromisoformat(day["session_start"]).timestamp()
    t1 = t0 + 86400
    lo = min(c["l"] for c in candles); hi = max(c["h"] for c in candles)
    pad = (hi - lo) * 0.08 or 0.5; lo -= pad; hi += pad
    xs = lambda ts: CX0 + (ts - t0) / (t1 - t0) * (CX1 - CX0)
    ys = lambda p: CY1 - (p - lo) / (hi - lo) * (CY1 - CY0)
    cw = max(2, (CX1 - CX0) / len(candles) * 0.7)

    # 배경(고정 요소)은 한 번만 그린다
    base = M.grid_bg(M.BG_WIN)
    d = ImageDraw.Draw(base)
    M.day_badge(d, cfg, n, M.GREEN)
    dt = datetime.strptime(day["date"], "%Y-%m-%d")
    d.text((70, 300), f"{dt.month}월 {dt.day}일 내 매매 타임랩스", font=M.font(cfg, "display", 68), fill=M.WHITE)
    fl = M.font(cfg, "body", 30)
    d.rectangle([CX0 - 10, CY0 - 20, CX1 + 10, CY1 + 20], outline=(40, 70, 55), width=2)
    for k in range(5):
        p = lo + (hi - lo) * k / 4
        d.line([(CX0, ys(p)), (CX1, ys(p))], fill=(25, 50, 38))
        d.text((CX1 - 110, ys(p) - 34), f"${p:,.2f}", font=fl, fill=M.MUTED)
    for h in range(0, 25, 6):
        x = xs(t0 + h * 3600)
        lab = datetime.fromtimestamp(t0 + h * 3600, M.KST).strftime("%H:%M")
        d.text((x - 40, CY1 + 30), lab, font=fl, fill=M.MUTED)
    fleg = M.font(cfg, "body", 34)
    d.polygon([(80, 1440), (100, 1405), (120, 1440)], fill=M.GREEN); d.text((132, 1402), "매수", font=fleg, fill=M.WHITE)
    d.polygon([(250, 1405), (270, 1440), (290, 1405)], fill=M.RED); d.text((302, 1402), "매도", font=fleg, fill=M.WHITE)
    d.text((440, 1402), "숫자 = 청산 손익", font=fleg, fill=M.MUTED)

    fclock, fbig, fsmall = M.font(cfg, "body", 46), M.font(cfg, "display", 120), M.font(cfg, "body", 40)
    flab = M.font(cfg, "display", 36)
    total = ANIM_SEC * FPS
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", *M.ENC, str(out_path)], stdin=subprocess.PIPE)
    for f in range(total + FPS):                     # 마지막 1초는 완성된 차트에서 멈춤
        now = t0 + (t1 - t0) * min(1.0, f / total)
        im = base.copy(); d = ImageDraw.Draw(im)
        # 봉 (진행 중인 봉은 현재 시각까지만)
        for c in candles:
            if c["t"] > now:
                break
            col = UP if c["c"] >= c["o"] else DOWN
            x = xs(c["t"] + CANDLE_MIN * 30)
            d.line([(x, ys(c["h"])), (x, ys(c["l"]))], fill=col, width=2)
            yo, yc = ys(c["o"]), ys(c["c"])
            d.rectangle([x - cw / 2, min(yo, yc), x + cw / 2, max(yo, yc) + 1], fill=col)
        # 체결 표시
        net, cnt = 0.0, 0
        for o in ords:
            if o["ts"] > now:
                break
            net += o["pnl"] - o["fee"]; cnt += 1
            x, y = xs(o["ts"]), ys(o["price"])
            r = 13 if o["pnl"] else 9
            if o["side"] == "BUY":
                d.polygon([(x - r, y + r * 1.6), (x, y + 4), (x + r, y + r * 1.6)], fill=M.GREEN, outline=(0, 0, 0))
            else:
                d.polygon([(x - r, y - r * 1.6), (x, y - 4), (x + r, y - r * 1.6)], fill=M.RED, outline=(0, 0, 0))
            if o["pnl"]:
                age = (now - o["ts"]) / (t1 - t0) * ANIM_SEC        # 화면상 경과 초
                txt = M.money(o["pnl"])
                col = M.GREEN if o["pnl"] > 0 else M.RED
                ty = y - 70 if o["side"] == "SELL" else y + 34
                if age < 1.2:                                         # 막 체결: 크게 + 가격 표시
                    lab = f"{'매도' if o['side'] == 'SELL' else '매수'} ${o['price']:,.2f}  {txt}"
                    tw = d.textlength(lab, font=flab)
                    tx = min(max(CX0, x - tw / 2), CX1 - tw)
                    d.rounded_rectangle([tx - 12, ty - 6, tx + tw + 12, ty + 46], 10, fill=(0, 0, 0))
                    d.text((tx, ty), lab, font=flab, fill=col)
                else:
                    tw = d.textlength(txt, font=fl)
                    d.text((x - tw / 2, ty + 4), txt, font=fl, fill=col)
        # 아래 패널: 시각 / 누적 손익
        d.rectangle([0, 1490, W, H], fill=(4, 12, 9))
        clock = datetime.fromtimestamp(now, M.KST).strftime("%m/%d %H:%M KST")
        d.text((80, 1520), clock, font=fclock, fill=M.MUTED)
        speed = "×3600 배속"
        d.text((W - 80 - d.textlength(speed, font=fclock), 1520), speed, font=fclock, fill=M.YELLOW)
        s = M.money(net, d=2)
        M.text_c(d, 1600, s, M.fit_font(cfg, "display", s, 150, W - 120), M.GREEN if net >= 0 else M.RED, 6)
        M.text_c(d, 1780, f"수수료 포함 손익 · 주문 {cnt}건", fsmall, M.WHITE)
        ff.stdin.write(im.tobytes())
    ff.stdin.close(); ff.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?")
    ap.add_argument("--fake-klines", action="store_true")
    a = ap.parse_args()
    cfg = M.load_config()
    date = a.date or M.default_date()
    day, site_cfg, n, cum = M.load_day(cfg, date)
    cfg["goal"] = site_cfg["goal_per_day"]
    candles = to_candles(load_klines(cfg, date, day, a.fake_klines), CANDLE_MIN)
    ords = orders(day)
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    tmp = cfg["out_dir"] / f"_anim_{date}.mp4"
    print(f"{date} DAY {n}: 주문 {len(ords)}건, 봉 {len(candles)}개 렌더링 중...")
    render(cfg, day, n, cum, candles, ords, tmp)
    end_png = cfg["out_dir"] / f"_end_{date}.png"
    M.make_end(cfg, day, n, cum, end_png)
    endv = cfg["out_dir"] / f"_end_{date}.mp4"
    M.still_clip(end_png, END_SEC - 1, endv)
    lst = cfg["out_dir"] / "_list.txt"
    lst.write_text(f"file '{tmp.as_posix()}'\nfile '{endv.as_posix()}'\n", encoding="utf-8")
    suffix = "_PREVIEW" if a.fake_klines else ""
    out = cfg["out_dir"] / f"SOXL_DAY{n}_{date}_chart{suffix}.mp4"
    M.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    for p in (tmp, end_png, endv, lst):
        p.unlink(missing_ok=True)
    print(f"완성: {out}  ({M.ffprobe_duration(out):.1f}초)")


if __name__ == "__main__":
    main()
