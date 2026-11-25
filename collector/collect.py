#!/usr/bin/env python3
"""
SOXL 하루 $1,000 챌린지 - 바이낸스 거래내역 수집기

바이낸스 USDⓈ-M 선물(서브계정)에서 하루치 체결 내역과 손익(실현손익, 수수료, 펀딩비)을
읽기 전용 API 키로 가져와서 site/data/ 아래 JSON으로 저장한다.

사용법
  # 실제 수집 (어제 세션: 전날 07:00 KST ~ 오늘 07:00 KST)
  python collect.py

  # 특정 날짜 세션 수집
  python collect.py --date 2026-11-24

  # 샘플 데이터 생성 (API 키 불필요)
  python collect.py --sample

API 키는 collector/.env 파일에 넣는다 (.env.example 참고, 절대 커밋 금지).
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
SITE_DATA = HERE.parent / "data"
DAYS_DIR = SITE_DATA / "days"
CONFIG_PATH = HERE / "config.json"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_env():
    env_path = HERE / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    # 서버에서 이미 돌고 있는 portfolio_bot 의 config.json 키를 그대로 쓰는 경우
    #   collector/.env 에  BINANCE_CONFIG_JSON=/home/bot/binance/portfolio_bot/config.json
    cfg_path = os.environ.get("BINANCE_CONFIG_JSON")
    if cfg_path and not os.environ.get("BINANCE_API_KEY"):
        with open(cfg_path, encoding="utf-8") as f:
            c = json.load(f)
        os.environ["BINANCE_API_KEY"] = c["binance_api_key"]
        os.environ["BINANCE_API_SECRET"] = c["binance_api_secret"]


def session_window(date_str, cutoff_hour):
    """date_str 세션 = date cutoff_hour:00 KST ~ 다음날 cutoff_hour:00 KST"""
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=cutoff_hour, tzinfo=KST)
    return d, d + timedelta(days=1)


def default_session_date(cutoff_hour):
    now = datetime.now(KST)
    # 오늘 cutoff 이전이면 이틀 전 세션이 마지막으로 끝난 세션
    last_end = now.replace(hour=cutoff_hour, minute=0, second=0, microsecond=0)
    if now < last_end:
        last_end -= timedelta(days=1)
    return (last_end - timedelta(days=1)).strftime("%Y-%m-%d")


def iso_kst(ms):
    return datetime.fromtimestamp(ms / 1000, KST).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 실제 수집
def fetch_day(cfg, date_str):
    import ccxt  # pip install -U ccxt

    key, secret = os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET")
    if not key or not secret:
        sys.exit("BINANCE_API_KEY / BINANCE_API_SECRET 가 collector/.env 에 없습니다.")

    ex = ccxt.binanceusdm({"apiKey": key, "secret": secret, "enableRateLimit": True})
    start, end = session_window(date_str, cfg["session_cutoff_hour_kst"])
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1
    symbol = cfg["symbol"]

    # 1) 체결 내역 (GET /fapi/v1/userTrades)
    raw_trades, from_id = [], None
    while True:
        params = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000}
        if from_id is not None:
            params = {"symbol": symbol, "fromId": from_id, "limit": 1000}
        batch = ex.fapiPrivateGetUserTrades(params)
        batch = [t for t in batch if start_ms <= int(t["time"]) <= end_ms]
        raw_trades += batch
        if len(batch) < 1000:
            break
        from_id = int(batch[-1]["id"]) + 1

    trades = [{
        "time": iso_kst(int(t["time"])),
        "side": t["side"],                     # BUY / SELL
        "position_side": t.get("positionSide"),
        "price": float(t["price"]),
        "qty": float(t["qty"]),
        "quote_qty": float(t["quoteQty"]),
        "realized_pnl": float(t["realizedPnl"]),
        "fee": float(t["commission"]),
        "fee_asset": t["commissionAsset"],
        "maker": bool(t["maker"]),
        "order_id": str(t["orderId"]),
        "trade_id": str(t["id"]),
    } for t in sorted(raw_trades, key=lambda x: int(x["id"]))]

    # 2) 손익 내역 (GET /fapi/v1/income) : REALIZED_PNL / COMMISSION / FUNDING_FEE ...
    income_rows, cursor = [], start_ms
    while True:
        batch = ex.fapiPrivateGetIncome({"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000})
        income_rows += batch
        if len(batch) < 1000:
            break
        cursor = int(batch[-1]["time"]) + 1

    income = {"realized_pnl": 0.0, "commission": 0.0, "funding": 0.0, "other": 0.0}
    for r in income_rows:
        v = float(r["income"])
        t = r["incomeType"]
        if t == "REALIZED_PNL":
            income["realized_pnl"] += v
        elif t == "COMMISSION":
            income["commission"] += v
        elif t == "FUNDING_FEE":
            income["funding"] += v
        else:
            income["other"] += v

    net = sum(income.values())
    if cfg.get("equity_source") == "account":
        # 전용 서브계정일 때만: 실제 선물 계좌 잔고를 그대로 공개
        acct = ex.fapiPrivateV2GetAccount()
        equity = float(acct["totalMarginBalance"])
        wallet = float(acct["totalWalletBalance"])
        unrealized = float(acct["totalUnrealizedProfit"])
    else:
        # 본계정(기본): 다른 자산이 노출되지 않도록 계좌 잔고는 읽지 않고
        # "SOXL 운용 시드 = start_equity + 이전까지 SOXL 누적 순손익 + 오늘 순손익" 으로 계산
        equity = cfg["start_equity"] + previous_cum_pnl(date_str) + net
        wallet, unrealized = equity, 0.0

    return build_day(cfg, date_str, trades, income, equity, wallet, unrealized, sample=False)


def previous_cum_pnl(date_str):
    total = 0.0
    for p in sorted(DAYS_DIR.glob("*.json")):
        if p.stem < date_str:
            total += json.loads(p.read_text(encoding="utf-8"))["net_pnl"]
    return total


def build_day(cfg, date_str, trades, income, equity, wallet, unrealized, sample):
    start, end = session_window(date_str, cfg["session_cutoff_hour_kst"])
    net = income["realized_pnl"] + income["commission"] + income["funding"] + income["other"]
    closes = [t for t in trades if abs(t["realized_pnl"]) > 0]
    return {
        "date": date_str,
        "session_start": start.isoformat(),
        "session_end": end.isoformat(),
        "symbol": cfg["symbol"],
        "trades": trades,
        "income": {k: round(v, 4) for k, v in income.items()},
        "net_pnl": round(net, 2),
        "wins": sum(1 for t in closes if t["realized_pnl"] > 0),
        "losses": sum(1 for t in closes if t["realized_pnl"] < 0),
        "volume": round(sum(t["quote_qty"] for t in trades), 2),
        "equity_end": round(equity, 2),
        "wallet_end": round(wallet, 2),
        "unrealized_end": round(unrealized, 2),
        "collected_at": datetime.now(KST).isoformat(timespec="seconds"),
        "sample": sample,
    }


# ---------------------------------------------------------------- 샘플 데이터
def make_samples(cfg, start_date, end_date, seed=7):
    rng = random.Random(seed)
    price = 44.0
    equity = cfg["start_equity"]
    d = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    days = []
    while d <= end:
        if d.weekday() < 5:  # 미국장 기준 평일만
            date_str = d.strftime("%Y-%m-%d")
            s, _ = session_window(date_str, cfg["session_cutoff_hour_kst"])
            t0 = s + timedelta(hours=16, minutes=30)  # 23:30 KST 미국장 개장
            trades, income = [], {"realized_pnl": 0.0, "commission": 0.0, "funding": 0.0, "other": 0.0}
            for _ in range(rng.randint(2, 5)):
                long_ = rng.random() < 0.7
                qty = rng.choice([300, 400, 500, 600, 800])
                entry = round(price * (1 + rng.uniform(-0.01, 0.01)), 2)
                move = rng.gauss(0.006, 0.018)
                exit_ = round(entry * (1 + move), 2)
                pnl = round((exit_ - entry) * qty * (1 if long_ else -1), 4)
                t_in = t0 + timedelta(minutes=rng.randint(1, 40))
                t_out = t_in + timedelta(minutes=rng.randint(4, 35))
                for i, (tt, side, px, rp) in enumerate([
                    (t_in, "BUY" if long_ else "SELL", entry, 0.0),
                    (t_out, "SELL" if long_ else "BUY", exit_, pnl),
                ]):
                    fee = round(px * qty * 0.0004, 4)
                    trades.append({
                        "time": tt.isoformat(timespec="seconds"), "side": side, "position_side": "BOTH",
                        "price": px, "qty": qty, "quote_qty": round(px * qty, 2), "realized_pnl": rp,
                        "fee": fee, "fee_asset": "USDT", "maker": i == 0,
                        "order_id": str(rng.randint(10**9, 10**10)), "trade_id": str(rng.randint(10**8, 10**9)),
                    })
                    income["commission"] -= fee
                income["realized_pnl"] += pnl
                price = exit_
                t0 = t_out
            income["funding"] = round(rng.uniform(-6, 2), 4)
            trades.sort(key=lambda t: t["time"])
            net = sum(income.values())
            equity += net
            days.append(build_day(cfg, date_str, trades, income, equity, equity, 0.0, sample=True))
        price *= 1 + rng.gauss(0, 0.01)
        d += timedelta(days=1)
    return days


# ---------------------------------------------------------------- 저장
def save_day(day):
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    (DAYS_DIR / f"{day['date']}.json").write_text(json.dumps(day, ensure_ascii=False, indent=2), encoding="utf-8")


def rebuild_index(cfg):
    videos_path = SITE_DATA / "videos.json"
    videos = json.loads(videos_path.read_text(encoding="utf-8")) if videos_path.exists() else {}
    rows = []
    for p in sorted(DAYS_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        rows.append({
            "date": d["date"], "net_pnl": d["net_pnl"],
            "realized_pnl": d["income"]["realized_pnl"],
            "fees": d["income"]["commission"], "funding": d["income"]["funding"],
            "trades": len(d["trades"]), "wins": d["wins"], "losses": d["losses"],
            "volume": d["volume"], "equity_end": d["equity_end"],
            "video_url": videos.get(d["date"]), "sample": d.get("sample", False),
        })
    index = {
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "config": {k: cfg[k] for k in ("title", "symbol", "goal_per_day", "start_date", "start_equity",
                                         "session_cutoff_hour_kst", "youtube_url", "exchange")},
        "days": rows,
    }
    (SITE_DATA / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="세션 날짜 YYYY-MM-DD (기본: 마지막으로 끝난 세션)")
    ap.add_argument("--sample", action="store_true", help="샘플 데이터 생성")
    ap.add_argument("--sample-from", default="2026-11-02")
    ap.add_argument("--sample-to", default="2026-11-24")
    args = ap.parse_args()

    cfg = load_config()
    if args.sample:
        for p in DAYS_DIR.glob("*.json"):
            p.unlink()
        for day in make_samples(cfg, args.sample_from, args.sample_to):
            save_day(day)
    else:
        load_env()
        date_str = args.date or default_session_date(cfg["session_cutoff_hour_kst"])
        day = fetch_day(cfg, date_str)
        save_day(day)
        print(f"{date_str}: 순손익 {day['net_pnl']:+,.2f} USDT, 체결 {len(day['trades'])}건, 잔고 {day['equity_end']:,.2f}")
    idx = rebuild_index(cfg)
    print(f"index.json 갱신: {len(idx['days'])}일")


if __name__ == "__main__":
    main()
