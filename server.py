# server.py - 웹 대시보드 서버
from flask import Flask, jsonify, render_template_string
import json
import csv
import os
import glob
import requests
from datetime import datetime
from config import INITIAL_BALANCE

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_current_price(coin="BTC"):
    try:
        res = requests.get(f"https://api.bithumb.com/public/ticker/{coin}_KRW", timeout=5)
        data = res.json()
        if data["status"] == "0000":
            return float(data["data"]["closing_price"])
    except:
        return None

def load_portfolio():
    """통합 포트폴리오 로드"""
    path = os.path.join(BASE_DIR, "data/portfolio.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {
        "cash": INITIAL_BALANCE,
        "positions": {},
        "buy_orders": 0,
        "sell_orders": 0,
        "closed_trades": 0,
        "win_trades": 0,
        "total_profit": 0.0,
    }

def load_trades(coin):
    path = os.path.join(BASE_DIR, f"data/trades_{coin}.csv")
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades

def get_daily_trend_for_symbol(coin):
    """심볼별 1D 추세 (CSV 기반, 빠른 조회)"""
    csv_path = os.path.join(BASE_DIR, "data/ohlcv", f"{coin}_1h.csv")
    if not os.path.exists(csv_path):
        return None
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        df["time"] = pd.to_datetime(df["time"])
        for c in ["open", "close", "high", "low"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["time", "close"]).sort_values("time").tail(1500)
        if len(df) < 50 * 24:
            return None
        df = df.set_index("time")
        daily = df.resample("1D").agg({
            "open": "first", "close": "last",
            "high": "max", "low": "min",
        }).dropna()
        if len(daily) < 50:
            return None
        daily["ema20"] = daily["close"].ewm(span=20, adjust=False).mean()
        daily["ema50"] = daily["close"].ewm(span=50, adjust=False).mean()
        last = daily.iloc[-1]
        p, e20, e50 = last["close"], last["ema20"], last["ema50"]
        if p > e50 and e20 > e50:
            return "UP"
        if p < e50 and e20 < e50:
            return "DOWN"
        return "SIDEWAYS"
    except Exception:
        return None


def get_btc_dominance_trend():
    """현재 BTC 추세 (시장 환경 표시용)"""
    return get_daily_trend_for_symbol("BTC")


def get_data_stats():
    """OHLCV 총 수집 건수 + 마지막 수집 시각"""
    ohlcv_dir = os.path.join(BASE_DIR, "data/ohlcv")
    total = 0
    last_mtime = 0
    if not os.path.exists(ohlcv_dir):
        return {"total": total, "last_fetch": "-"}
    try:
        for fname in sorted(os.listdir(ohlcv_dir)):
            if not fname.endswith(".csv"):
                continue
            path = os.path.join(ohlcv_dir, fname)
            try:
                mtime = os.path.getmtime(path)
                if mtime > last_mtime:
                    last_mtime = mtime
                with open(path, "r") as f:
                    total += max(0, sum(1 for _ in f) - 1)
            except:
                continue
    except:
        pass
    last_fetch = "-"
    if last_mtime > 0:
        last_fetch = datetime.fromtimestamp(last_mtime).strftime("%H:%M:%S")
    return {"total": total, "last_fetch": last_fetch}


def get_cycle_info():
    path = os.path.join(BASE_DIR, "data/cycle_info.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"cycle": 0, "tickers": [], "last_check": "-"}

def get_strategy_info():
    try:
        import importlib.util
        config_path = os.path.join(BASE_DIR, "config.py")
        spec = importlib.util.spec_from_file_location("config", config_path)
        cfg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cfg)
        return {
            # Swing v3.0
            "candle_interval": getattr(cfg, "CANDLE_INTERVAL", "1h"),
            "ema_fast": getattr(cfg, "EMA_FAST", 9),
            "ema_mid": getattr(cfg, "EMA_MID", 21),
            "ema_long": getattr(cfg, "EMA_LONG", 50),
            "donchian_period": getattr(cfg, "DONCHIAN_PERIOD", 24),
            "stop_loss": getattr(cfg, "STOP_LOSS", -0.05) * 100,
            "take_profit_base": getattr(cfg, "TAKE_PROFIT_BASE", 0.15) * 100,
            "trade_ratio": getattr(cfg, "TRADE_RATIO", 1.0) * 100,
            "fee_rate": getattr(cfg, "FEE_RATE", 0.0025) * 100,
            "slippage": getattr(cfg, "SLIPPAGE_RATE", 0.002) * 100,
            "interval": getattr(cfg, "INTERVAL_SECONDS", 1800),
            "max_tickers": getattr(cfg, "TOP_TICKER_LIMIT", 4),
            "alloc_per_ticker": getattr(cfg, "ALLOC_PER_TICKER", 0.25) * 100,
            "force_sell_hours": getattr(cfg, "FORCE_SELL_HOURS", 14*24),
            "trailing_trigger": getattr(cfg, "TRAILING_STOP_TRIGGER", 0.07) * 100,
            "trailing_drop": getattr(cfg, "TRAILING_STOP_DROP", 0.03) * 100,
            "atr_stop_min": getattr(cfg, "ATR_STOP_MIN", -0.04) * 100,
            "atr_stop_max": getattr(cfg, "ATR_STOP_MAX", -0.08) * 100,
            "atr_mult": getattr(cfg, "ATR_STOP_MULTIPLIER", 3.0),
            "global_stop_loss": getattr(cfg, "GLOBAL_STOP_LOSS", -0.10) * 100,
            "rsi_pb_low": getattr(cfg, "RSI_PULLBACK_LOW", 45),
            "rsi_pb_high": getattr(cfg, "RSI_PULLBACK_HIGH", 60),
            "breakout_vol": getattr(cfg, "BREAKOUT_VOLUME_MIN", 2.0),
            "adx_min": getattr(cfg, "ADX_BREAKOUT_MIN", 25),
        }
    except Exception as e:
        return {
            "candle_interval": "1h", "ema_fast": 9, "ema_mid": 21, "ema_long": 50,
            "donchian_period": 24, "stop_loss": -5.0, "take_profit_base": 15.0,
            "trade_ratio": 100.0, "fee_rate": 0.25, "slippage": 0.2,
            "interval": 1800, "max_tickers": 4, "alloc_per_ticker": 25.0,
            "force_sell_hours": 336, "trailing_trigger": 7.0, "trailing_drop": 3.0,
            "atr_stop_min": -4.0, "atr_stop_max": -8.0, "atr_mult": 3.0,
            "global_stop_loss": -10.0, "rsi_pb_low": 45, "rsi_pb_high": 60,
            "breakout_vol": 2.0, "adx_min": 25,
        }

def get_closed_trades():
    """
    종목별 청산 완료 기록 계산
    매수→매도 쌍을 매칭해서 종목당 요약 반환
    """
    trade_files = glob.glob(os.path.join(BASE_DIR, "data/trades_*.csv"))
    history = []

    for path in trade_files:
        coin = os.path.basename(path).replace("trades_", "").replace(".csv", "")
        trades = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(row)

        # 매수/매도 분리
        buys = [t for t in trades if t["액션"] == "매수"]
        sells = [t for t in trades if t["액션"] == "매도"]

        if not buys or not sells:
            continue

        # 수수료 합산
        total_fee = 0

        # 매수 평균가 계산
        total_buy_amount = 0
        total_buy_qty = 0
        for b in buys:
            price_str = b["가격"].replace(",", "")
            qty_str = b["수량"].replace(",", "")
            try:
                p = float(price_str)
                q = float(qty_str)
                total_buy_amount += p * q
                total_buy_qty += q
                total_fee += float(b.get("수수료", "0").replace(",", ""))
            except:
                pass
        avg_buy = total_buy_amount / total_buy_qty if total_buy_qty > 0 else 0

        # 매도 평균가 & 총 손익 계산
        total_sell_amount = 0
        total_sell_qty = 0
        total_pnl = 0
        last_sell_time = "-"
        for s in sells:
            price_str = s["가격"].replace(",", "")
            qty_str = s["수량"].replace(",", "")
            pnl_str = s.get("손익", "0").replace(",", "")
            try:
                p = float(price_str)
                q = float(qty_str)
                total_sell_amount += p * q
                total_sell_qty += q
                if pnl_str and pnl_str != "-":
                    total_pnl += float(pnl_str)
                total_fee += float(s.get("수수료", "0").replace(",", ""))
            except:
                pass
            last_sell_time = s.get("날짜시간", "-")

        avg_sell = total_sell_amount / total_sell_qty if total_sell_qty > 0 else 0

        # 수익률
        pnl_rate = (avg_sell - avg_buy) / avg_buy * 100 if avg_buy > 0 else 0

        # 첫 매수 시각
        first_buy_time = buys[0].get("날짜시간", "-") if buys else "-"

        history.append({
            "coin": coin,
            "avg_buy": avg_buy,
            "avg_sell": avg_sell,
            "total_pnl": total_pnl,
            "total_fee": total_fee,
            "pnl_rate": pnl_rate,
            "buy_count": len(buys),
            "sell_count": len(sells),
            "first_buy_time": first_buy_time,
            "last_sell_time": last_sell_time,
        })

    # 청산 시각 최신순 정렬
    history.sort(key=lambda x: x["last_sell_time"], reverse=True)
    return history



@app.route("/api/data-stats")
def api_data_stats():
    return jsonify(get_data_stats())


@app.route("/api/history")
def api_history():
    return jsonify(get_closed_trades())


@app.route("/api/status")
def api_status():
    portfolio = load_portfolio()
    strategy = get_strategy_info()
    cycle_info = get_cycle_info()
    coins = []

    positions = portfolio.get("positions", {})
    cash = portfolio.get("cash", INITIAL_BALANCE)

    active_tickers = [s for s, p in positions.items() if p.get("quantity", 0) > 0]
    all_tickers = list(set(active_tickers + cycle_info.get("tickers", [])))

    total_coin_value = 0

    for coin in all_tickers:
        price = get_current_price(coin)
        pos = positions.get(coin, {})
        trades = load_trades(coin)

        quantity = pos.get("quantity", 0)
        avg_buy_price = pos.get("avg_buy_price", 0)
        buy_count = pos.get("buy_count", 0)
        buy_time = pos.get("buy_time", None)
        total_invested = pos.get("total_invested", 0)

        coin_value = quantity * price if price and quantity > 0 else 0
        total_coin_value += coin_value

        profit = 0
        profit_rate = 0
        if avg_buy_price > 0 and price and quantity > 0:
            profit = (price - avg_buy_price) * quantity
            profit_rate = (price - avg_buy_price) / avg_buy_price * 100

        holding_hours = 0
        if buy_time:
            try:
                bt = datetime.fromisoformat(buy_time)
                holding_hours = (datetime.now() - bt).total_seconds() / 3600
            except:
                pass

        coins.append({
            "coin": coin,
            "price": price,
            "quantity": quantity,
            "avg_buy_price": avg_buy_price,
            "coin_value": coin_value,
            "profit": profit,
            "profit_rate": profit_rate,
            "buy_count": buy_count,
            "holding_hours": holding_hours,
            "total_invested": total_invested,
            "daily_trend": get_daily_trend_for_symbol(coin),
            "trades": trades[-10:],
        })

    total_assets = cash + total_coin_value
    total_profit = total_assets - INITIAL_BALANCE
    total_profit_rate = total_profit / INITIAL_BALANCE * 100

    # 전체 누적 수수료 계산
    total_fee_all = 0
    for tf in glob.glob(os.path.join(BASE_DIR, "data/trades_*.csv")):
        with open(tf, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    total_fee_all += float(row.get("수수료", "0").replace(",", ""))
                except:
                    pass

    return jsonify({
        "coins": coins,
        "summary": {
            "total_assets": total_assets,
            "total_profit": total_profit,
            "total_profit_rate": total_profit_rate,
            "cash": cash,
            "buy_orders": portfolio.get("buy_orders", 0),
            "sell_orders": portfolio.get("sell_orders", 0),
            "closed_trades": portfolio.get("closed_trades", 0),
            "win_trades": portfolio.get("win_trades", 0),
            "total_profit_realized": portfolio.get("total_profit", 0),
            "total_fee": total_fee_all,
        },
        "strategy": strategy,
        "cycle": cycle_info.get("cycle", 0),
        "last_check": cycle_info.get("last_check", "-"),
        "market": {
            "btc_trend": get_btc_dominance_trend(),
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TRADER J</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Noto+Sans+KR:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
  :root {
    /* Dark theme (TRADER_J Swing v3.0) */
    --bg: #0a0e14;
    --panel: #141821;
    --panel2: #0f131b;
    --border: #1f2733;
    --border2: #2a3441;
    --accent: #00d68f;
    --accent2: #4d8eff;
    --red: #ff5566;
    --yellow: #f5b342;
    --text: #e8eef5;
    --muted: #8a96a8;
    --mono: 'Space Mono', monospace;
    --sans: 'Noto Sans KR', sans-serif;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:var(--sans); min-height:100vh; }
  body::before {
    content:''; position:fixed; inset:0;
    background-image:
      linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
    background-size:48px 48px; pointer-events:none; z-index:0;
  }
  .container { position:relative; z-index:1; max-width:1600px; margin:0 auto; padding:16px 20px; }

  header {
    display:flex; justify-content:space-between; align-items:center;
    margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid var(--border);
  }
  .logo { font-family:var(--mono); font-size:18px; font-weight:700; color:var(--accent); letter-spacing:3px; }
  .logo span { color:var(--muted); }
  .header-right { display:flex; align-items:center; gap:12px; font-family:var(--mono); font-size:11px; color:var(--muted); }
  .live-dot { width:7px; height:7px; border-radius:50%; background:var(--accent); animation:pulse 2s infinite; }
  @keyframes pulse {
    0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(0,168,107,0.4)}
    50%{opacity:.7;box-shadow:0 0 0 6px rgba(0,168,107,0)}
  }

  .summary-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:10px; }
  .summary-card {
    background:var(--panel); border:1px solid var(--border);
    border-radius:10px; padding:14px 16px; position:relative; overflow:hidden;
  }
  .summary-card::after {
    content:''; position:absolute; top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg, var(--accent), var(--accent2));
    border-radius:10px 10px 0 0;
  }
  .card-label { font-size:9px; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-bottom:6px; font-family:var(--mono); }
  .card-value { font-family:var(--mono); font-size:20px; font-weight:700; }
  .card-sub { font-size:10px; color:var(--muted); margin-top:4px; font-family:var(--mono); }
  .up { color:var(--accent) !important; }
  .down { color:var(--red) !important; }

  /* 체크 현황 + 전략을 한 줄로 */
  .info-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px; }

  .cycle-bar {
    background:var(--panel); border:1px solid var(--border);
    border-radius:10px; padding:10px 14px;
    display:flex; align-items:center; gap:0; flex-wrap:wrap;
  }
  .cycle-item { display:flex; flex-direction:column; gap:2px; padding:0 12px; }
  .cycle-item:first-child { padding-left:0; }
  .cycle-label { font-family:var(--mono); font-size:8px; color:var(--muted); text-transform:uppercase; letter-spacing:1px; }
  .cycle-value { font-family:var(--mono); font-size:12px; font-weight:700; color:var(--accent); }
  .cycle-divider { width:1px; height:26px; background:var(--border); flex-shrink:0; }

  /* 전략 패널 */
  .strategy-panel {
    background:var(--panel); border:1px solid var(--border);
    border-radius:10px; padding:10px 14px;
    display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  }
  .strategy-title { font-family:var(--mono); font-size:11px; color:var(--accent); font-weight:700; white-space:nowrap; display:flex; align-items:center; gap:6px; }
  .active-badge { background:rgba(0,255,136,0.1); border:1px solid rgba(0,255,136,0.3); color:var(--accent); font-family:var(--mono); font-size:8px; padding:1px 6px; border-radius:20px; }
  .strat-params { display:flex; gap:5px; flex-wrap:wrap; flex:1; }
  .strat-chip { background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:6px; padding:4px 8px; text-align:center; min-width:54px; }
  .strat-chip-label { font-size:8px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:1px; font-family:var(--mono); }
  .strat-chip-value { font-family:var(--mono); font-size:11px; font-weight:700; color:var(--text); }
  .green { color:var(--accent) !important; }
  .red { color:var(--red) !important; }
  .blue { color:var(--accent2) !important; }
  .yellow { color:var(--yellow) !important; }

  /* 섹션 타이틀 */
  .section-title {
    font-family:var(--mono); font-size:10px; color:var(--muted);
    text-transform:uppercase; letter-spacing:2px; margin-bottom:8px;
    display:flex; align-items:center; gap:6px;
  }
  .section-title::before { content:''; width:3px; height:12px; background:var(--accent2); border-radius:2px; }

  /* 2단 테이블 레이아웃 (PC) */
  .tables-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }

  /* 수익률 테이블 */
  .profit-table-wrap {
    background:var(--panel); border:1px solid var(--border);
    border-radius:10px; overflow-x:auto;
    -webkit-overflow-scrolling:touch;
  }
  .profit-table { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:11px; }
  .profit-table thead th {
    background:rgba(255,255,255,0.03); padding:8px 10px;
    text-align:left; color:var(--muted); font-size:9px;
    text-transform:uppercase; letter-spacing:1px;
    border-bottom:1px solid var(--border); font-weight:400;
    position:sticky; top:0;
  }
  .profit-table thead th:not(:first-child) { text-align:right; }
  .profit-table tbody tr { border-bottom:1px solid var(--border); transition:background 0.15s; }
  .profit-table tbody tr:last-child { border-bottom:none; }
  .profit-table tbody tr:hover { background:rgba(255,255,255,0.03); }
  .profit-table td { padding:10px; vertical-align:middle; }
  .profit-table td:not(:first-child) { text-align:right; }

  .coin-badge { display:inline-flex; align-items:center; gap:8px; }
  .coin-dot { width:6px; height:6px; border-radius:50%; }
  .coin-sym { font-size:13px; font-weight:700; color:var(--accent2); }
  .coin-label { font-size:9px; color:var(--muted); margin-top:1px; }

  .rate-badge { display:inline-block; padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; }
  .rate-up { background:rgba(0,255,136,0.1); color:var(--accent); border:1px solid rgba(0,255,136,0.2); }
  .rate-down { background:rgba(255,68,85,0.1); color:var(--red); border:1px solid rgba(255,68,85,0.2); }
  .rate-zero { background:rgba(255,255,255,0.04); color:var(--muted); border:1px solid var(--border); }

  .holding-bar-wrap { display:flex; align-items:center; gap:6px; justify-content:flex-end; }
  .holding-bar { width:40px; height:3px; background:var(--border); border-radius:2px; overflow:hidden; }
  .holding-bar-fill { height:100%; border-radius:2px; transition:width 0.4s; }

  /* 종목 거래내역 - 토글 가능 */
  .toggle-section { margin-bottom:14px; }
  .toggle-btn {
    font-family:var(--mono); font-size:10px; color:var(--muted);
    text-transform:uppercase; letter-spacing:2px;
    display:flex; align-items:center; gap:6px; cursor:pointer;
    background:none; border:none; padding:0; margin-bottom:8px;
  }
  .toggle-btn::before { content:''; width:3px; height:12px; background:var(--accent2); border-radius:2px; }
  .toggle-btn .arrow { transition:transform 0.2s; display:inline-block; }
  .toggle-btn.open .arrow { transform:rotate(90deg); }
  .coins-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(340px, 1fr)); gap:10px; }
  .coins-grid.collapsed { display:none; }
  .coin-card { background:var(--panel); border:1px solid var(--border); border-radius:10px; overflow:hidden; }
  .coin-card-header {
    display:flex; justify-content:space-between; align-items:center;
    padding:10px 14px; border-bottom:1px solid var(--border);
    background:rgba(255,255,255,0.02);
  }
  .coin-card-name { font-family:var(--mono); font-size:13px; font-weight:700; color:var(--accent2); }
  .coin-card-meta { font-family:var(--mono); font-size:9px; color:var(--muted); margin-top:2px; }

  .stats-row { display:grid; grid-template-columns:repeat(3,1fr); border-bottom:1px solid var(--border); }
  .stat-item { padding:8px 12px; border-right:1px solid var(--border); }
  .stat-item:last-child { border-right:none; }
  .stat-label { font-family:var(--mono); font-size:8px; color:var(--muted); text-transform:uppercase; letter-spacing:1px; margin-bottom:2px; }
  .stat-val { font-family:var(--mono); font-size:11px; font-weight:700; color:var(--text); }

  .trades-section { padding:10px 12px; }
  .trades-title { font-family:var(--mono); font-size:8px; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-bottom:6px; }
  .trade-table { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:10px; }
  .trade-table th { text-align:left; color:var(--muted); padding:4px 4px; border-bottom:1px solid var(--border); font-weight:400; font-size:8px; text-transform:uppercase; letter-spacing:1px; }
  .trade-table th:not(:first-child) { text-align:right; }
  .trade-table td { padding:5px 4px; border-bottom:1px solid rgba(255,255,255,0.05); }
  .trade-table td:not(:first-child) { text-align:right; }
  .trade-table tr:last-child td { border-bottom:none; }
  .badge { display:inline-block; padding:1px 5px; border-radius:3px; font-size:9px; font-weight:700; }
  .badge-buy { background:rgba(0,255,136,0.15); color:var(--accent); }
  .badge-sell { background:rgba(255,68,85,0.15); color:var(--red); }

  .footer { text-align:center; font-family:var(--mono); font-size:10px; color:var(--muted); padding-top:10px; border-top:1px solid var(--border); }

  /* 청산 히스토리 */
  .history-table-wrap {
    background:var(--panel); border:1px solid var(--border);
    border-radius:10px; overflow-x:auto;
    -webkit-overflow-scrolling:touch;
  }
  .history-table { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:11px; }
  .history-table thead th {
    background:rgba(255,255,255,0.03); padding:8px 10px;
    text-align:left; color:var(--muted); font-size:9px;
    text-transform:uppercase; letter-spacing:1px;
    border-bottom:1px solid var(--border); font-weight:400;
    position:sticky; top:0;
  }
  .history-table thead th:not(:first-child) { text-align:right; }
  .history-table tbody tr { border-bottom:1px solid var(--border); transition:background 0.15s; }
  .history-table tbody tr:last-child { border-bottom:none; }
  .history-table tbody tr:hover { background:rgba(255,255,255,0.03); }
  .history-table td { padding:8px 10px; vertical-align:middle; }
  .history-table td:not(:first-child) { text-align:right; }
  .pnl-pill {
    display:inline-block; padding:3px 8px; border-radius:14px;
    font-size:11px; font-weight:700;
  }
  .pnl-up { background:rgba(0,255,136,0.1); color:var(--accent); border:1px solid rgba(0,255,136,0.25); }
  .pnl-down { background:rgba(255,68,85,0.1); color:var(--red); border:1px solid rgba(255,68,85,0.25); }
  .pnl-zero { background:rgba(255,255,255,0.04); color:var(--muted); border:1px solid var(--border); }
  .arrow-icon { color:var(--muted); margin:0 4px; font-size:10px; }

  /* PC에서 테이블 max-height로 스크롤 */
  @media (min-width: 769px) {
    .profit-table-wrap, .history-table-wrap { max-height:420px; overflow-y:auto; }
  }

  /* ===== 반응형 (모바일) ===== */
  @media (max-width: 768px) {
    .container { padding:10px; }

    header { flex-direction:column; align-items:flex-start; gap:4px; margin-bottom:10px; padding-bottom:8px; }
    .logo { font-size:15px; }
    .header-right { font-size:10px; gap:8px; }

    .summary-grid { grid-template-columns:repeat(2,1fr); gap:8px; margin-bottom:8px; }
    .summary-card { padding:10px; }
    .card-value { font-size:16px; }
    .card-sub { font-size:9px; }

    .info-row { grid-template-columns:1fr; gap:8px; margin-bottom:8px; }

    /* cycle-bar 모바일 */
    .cycle-bar { display:grid; grid-template-columns:repeat(4,1fr); padding:0; gap:0; border-radius:8px; overflow:hidden; }
    .cycle-divider { display:none; }
    .cycle-item { padding:8px 8px; border-bottom:1px solid var(--border); border-right:1px solid var(--border); }
    .cycle-item:nth-child(4n) { border-right:none; }
    .cycle-item:nth-last-child(-n+4) { border-bottom:none; }
    .cycle-label { font-size:7px; }
    .cycle-value { font-size:10px; }

    .strategy-panel { padding:8px 10px; gap:6px; }
    .strategy-title { font-size:10px; }
    .strat-params { gap:4px; }
    .strat-chip { min-width:42px; padding:4px 6px; }
    .strat-chip-value { font-size:10px; }
    .strat-chip-label { font-size:7px; }

    .section-title { font-size:9px; margin-bottom:6px; }

    /* 2단 → 1단 */
    .tables-grid { grid-template-columns:1fr; gap:10px; }

    /* 모바일 테이블: 핵심 컬럼만 */
    .profit-table .hide-mobile, .history-table .hide-mobile { display:none; }
    .profit-table { min-width:auto; font-size:10px; }
    .profit-table thead th { padding:6px 6px; font-size:8px; }
    .profit-table td { padding:8px 6px; }

    .history-table { min-width:auto; font-size:10px; }
    .history-table thead th { padding:6px 6px; font-size:8px; }
    .history-table td { padding:6px 6px; }
    .pnl-pill { font-size:10px; padding:2px 6px; }

    .coins-grid { grid-template-columns:1fr; gap:8px; }
    .stats-row { grid-template-columns:repeat(3,1fr); }
    .stat-item { padding:6px 8px; }
    .stat-label { font-size:7px; }
    .stat-val { font-size:10px; }
    .coin-card-header { padding:8px 10px; }
    .coin-card-name { font-size:12px; }
    .trades-section { padding:8px 10px; }
    .trade-table { font-size:9px; }

    .footer { font-size:9px; }
  }

  @media (max-width: 480px) {
    .summary-grid { grid-template-columns:1fr 1fr; }
    .card-value { font-size:15px; }
    .cycle-bar { grid-template-columns:repeat(2,1fr); }
    .strat-params { gap:3px; }
    .strat-chip { min-width:40px; }
  }
</style>
</head>
<body>
<div class="container">

  <header>
    <div class="logo">TRADER<span>_</span>J <span style="font-size:11px;letter-spacing:1px;color:var(--accent2);margin-left:8px;font-weight:400">Swing v3.0</span></div>
    <div class="header-right">
      <div class="live-dot"></div>
      <span id="updated">연결 중...</span>
    </div>
  </header>

  <!-- 요약 -->
  <div class="summary-grid">
    <div class="summary-card">
      <div class="card-label">전체 총 자산</div>
      <div class="card-value" id="sum-total" style="color:var(--text)">--</div>
      <div class="card-sub" id="sum-cash">보유 현금: --</div>
    </div>
    <div class="summary-card">
      <div class="card-label">자산 구성</div>
      <div style="display:flex;flex-direction:column;gap:5px;margin-top:2px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-family:var(--mono);font-size:9px;color:var(--muted)">현금</span>
          <span class="card-value" id="sum-cash2" style="font-size:14px;color:var(--accent)">--</span>
        </div>
        <div style="height:1px;background:var(--border)"></div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-family:var(--mono);font-size:9px;color:var(--muted)">평가</span>
          <span class="card-value" id="sum-invested" style="font-size:14px;color:var(--accent2)">--</span>
        </div>
        <div style="height:3px;background:var(--border);border-radius:2px;overflow:hidden">
          <div id="sum-bar" style="height:100%;background:linear-gradient(90deg,var(--accent2),var(--accent));border-radius:2px;transition:width 0.4s;width:0%"></div>
        </div>
        <div class="card-sub" id="sum-bar-label" style="text-align:right;margin-top:0">비중 --</div>
      </div>
    </div>
    <div class="summary-card">
      <div class="card-label">전체 총 손익</div>
      <div class="card-value" id="sum-profit">--</div>
      <div class="card-sub" id="sum-rate">전체 수익률 --</div>
    </div>
    <div class="summary-card">
      <div class="card-label">운용 종목 수</div>
      <div class="card-value" id="sum-coins" style="color:var(--accent2)">--</div>
      <div class="card-sub" id="sum-coins-sub">최대 4개 자동 선정</div>
    </div>
  </div>

  <!-- 체크 현황 + 전략 한 줄 -->
  <div class="info-row">
  <div class="cycle-bar">
    <div class="cycle-item">
      <div class="cycle-label">체크 주기</div>
      <div class="cycle-value" id="c-interval">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">체크 횟수</div>
      <div class="cycle-value" id="c-cycle">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">마지막 체크</div>
      <div class="cycle-value" style="color:var(--muted);font-size:12px" id="c-lastcheck">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">주문 집계</div>
      <div class="cycle-value" style="color:var(--yellow)" id="c-trades">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">청산 승률</div>
      <div class="cycle-value" id="c-winrate">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">실현 손익</div>
      <div class="cycle-value" id="c-realized">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">누적 수수료</div>
      <div class="cycle-value" style="color:var(--yellow)" id="c-fee">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">수집 횟수</div>
      <div class="cycle-value" style="color:var(--accent2)" id="c-datacnt">--</div>
      <div style="font-family:var(--mono);font-size:10px;color:var(--muted)" id="c-lastfetch">--</div>
    </div>
  </div>

  <div class="strategy-panel">
    <div class="strategy-title">📈 Swing v3.0 · Trend + Breakout <span class="active-badge">● ACTIVE</span></div>
    <div class="strat-params">
      <div class="strat-chip"><div class="strat-chip-label">시그널</div><div class="strat-chip-value blue" id="s-tf">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">EMA(F/M/L)</div><div class="strat-chip-value blue" id="s-ema">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">Donchian</div><div class="strat-chip-value blue" id="s-donchian">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">기본 익절</div><div class="strat-chip-value green" id="s-tp">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">트레일링</div><div class="strat-chip-value green" id="s-trail">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">손절(ATR)</div><div class="strat-chip-value red" id="s-sl">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">종목/배분</div><div class="strat-chip-value yellow" id="s-alloc">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">강제청산</div><div class="strat-chip-value red" id="s-forcesell">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">BTC 추세</div><div class="strat-chip-value" id="s-btctrend">--</div></div>
    </div>
  </div>
  </div>

  <!-- 종목별 수익률 + 청산 히스토리 2단 -->
  <div class="tables-grid">
  <div>
  <!-- 종목별 수익률 테이블 -->
  <div class="section-title">종목별 수익률 현황</div>
  <div class="profit-table-wrap">
    <table class="profit-table">
      <thead>
        <tr>
          <th>종목</th>
          <th class="hide-mobile">매수가</th>
          <th>현재가</th>
          <th>수익률</th>
          <th class="hide-mobile">평가금액</th>
          <th class="hide-mobile">분할</th>
          <th>보유</th>
        </tr>
      </thead>
      <tbody id="profit-tbody">
        <tr><td colspan="7" style="text-align:center;padding:28px;color:var(--muted);font-family:var(--mono);font-size:12px">데이터 로딩 중...</td></tr>
      </tbody>
    </table>
  </div>
  </div>

  <div>
  <!-- 청산 히스토리 -->
  <div class="section-title">청산 완료 히스토리</div>
  <div class="history-table-wrap">
    <table class="history-table">
      <thead>
        <tr>
          <th>종목</th>
          <th class="hide-mobile">매수가</th>
          <th class="hide-mobile">매도가</th>
          <th>수익률</th>
          <th>손익</th>
          <th class="hide-mobile">수수료</th>
          <th class="hide-mobile">횟수</th>
          <th class="hide-mobile">진입</th>
          <th>청산</th>
        </tr>
      </thead>
      <tbody id="history-tbody">
        <tr><td colspan="9" style="text-align:center;padding:20px;color:var(--muted);font-family:var(--mono);font-size:11px">데이터 로딩 중...</td></tr>
      </tbody>
    </table>
  </div>
  </div>
  </div>

  <!-- 종목별 거래내역 (토글) -->
  <div class="toggle-section">
  <button class="toggle-btn" id="toggle-trades" onclick="toggleTrades()">
    <span class="arrow">&#9654;</span> 종목별 거래 내역
  </button>
  <div class="coins-grid collapsed" id="coins-grid">
    <div style="color:var(--muted);font-family:var(--mono);padding:20px;text-align:center;font-size:11px">데이터 로딩 중...</div>
  </div>
  </div>

  <div class="footer" id="footer-time">마지막 업데이트: -- | 30초마다 자동 갱신</div>
</div>

<script>
function fmt(n) { return Number(n).toLocaleString('ko-KR'); }
function fmtRate(r) { return (r >= 0 ? '+' : '') + Number(r).toFixed(2) + '%'; }

function toggleTrades() {
  const grid = document.getElementById('coins-grid');
  const btn = document.getElementById('toggle-trades');
  grid.classList.toggle('collapsed');
  btn.classList.toggle('open');
}

function trendBadge(t) {
  if (t === 'UP') return '<span style="font-family:var(--mono);font-size:9px;background:rgba(0,168,107,0.12);color:var(--accent);border:1px solid rgba(0,168,107,0.3);padding:2px 6px;border-radius:4px">↑ UP</span>';
  if (t === 'DOWN') return '<span style="font-family:var(--mono);font-size:9px;background:rgba(229,62,62,0.12);color:var(--red);border:1px solid rgba(229,62,62,0.3);padding:2px 6px;border-radius:4px">↓ DOWN</span>';
  if (t === 'SIDEWAYS') return '<span style="font-family:var(--mono);font-size:9px;background:rgba(217,119,6,0.12);color:var(--yellow);border:1px solid rgba(217,119,6,0.3);padding:2px 6px;border-radius:4px">➡ SIDE</span>';
  return '<span style="font-family:var(--mono);font-size:9px;color:var(--muted)">-</span>';
}

function renderProfitTable(coins) {
  const tbody = document.getElementById('profit-tbody');
  if (!coins || coins.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:var(--muted);font-family:var(--mono);font-size:11px">보유 종목 없음</td></tr>';
    return;
  }

  tbody.innerHTML = coins.map(c => {
    const hasPos = c.quantity > 0;
    const rateClass = !hasPos ? 'rate-zero' : (c.profit_rate >= 0 ? 'rate-up' : 'rate-down');
    const rateText = !hasPos ? '미보유' : fmtRate(c.profit_rate);
    const profitAmt = hasPos && c.profit !== 0
      ? `<div style="font-size:9px;color:var(--muted);margin-top:2px">${c.profit >= 0 ? '+' : ''}${fmt(Math.round(c.profit))}원</div>`
      : '';

    // 스윙: max 14일 = 336h
    const maxH = 336;
    const barPct = Math.min((c.holding_hours / maxH) * 100, 100);
    const barColor = barPct > 80 ? '#ff4455' : barPct > 50 ? '#ffcc00' : '#0088ff';
    const holdStr = c.holding_hours > 0
      ? (c.holding_hours >= 24
          ? (c.holding_hours / 24).toFixed(1) + 'd'
          : c.holding_hours >= 1
            ? c.holding_hours.toFixed(1) + 'h'
            : Math.round(c.holding_hours * 60) + 'm')
      : '-';

    return `<tr>
      <td>
        <div class="coin-badge">
          <div class="coin-dot" style="background:${hasPos ? 'var(--accent)' : 'var(--muted)'}"></div>
          <div>
            <div class="coin-sym">${c.coin}</div>
            <div style="margin-top:2px">${trendBadge(c.daily_trend)}</div>
          </div>
        </div>
      </td>
      <td class="hide-mobile" style="color:var(--muted)">
        ${hasPos && c.avg_buy_price > 0 ? fmt(Math.round(c.avg_buy_price)) : '-'}
      </td>
      <td style="font-weight:700">
        ${c.price ? fmt(c.price) : '-'}
      </td>
      <td>
        <span class="rate-badge ${rateClass}">${rateText}</span>
        ${profitAmt}
      </td>
      <td class="hide-mobile">
        ${hasPos ? fmt(Math.round(c.coin_value)) + '원' : '-'}
      </td>
      <td class="hide-mobile" style="color:var(--accent2);font-weight:700">
        ${hasPos ? c.buy_count + '회' : '-'}
      </td>
      <td>
        ${hasPos ? `
          <div class="holding-bar-wrap">
            <div class="holding-bar">
              <div class="holding-bar-fill" style="width:${barPct}%;background:${barColor}"></div>
            </div>
            <span style="font-size:10px;color:var(--muted)">${holdStr}</span>
          </div>
        ` : '<span style="color:var(--muted);font-size:10px">-</span>'}
      </td>
    </tr>`;
  }).join('');
}

function renderCoinCards(coins) {
  const grid = document.getElementById('coins-grid');
  if (!coins || coins.length === 0) {
    grid.innerHTML = '<div style="color:var(--muted);font-family:var(--mono);padding:20px;text-align:center;font-size:11px">거래 내역 없음</div>';
    return;
  }

  grid.innerHTML = coins.map(c => {
    const hasPos = c.quantity > 0;
    const rateClass = !hasPos ? 'rate-zero' : (c.profit_rate >= 0 ? 'rate-up' : 'rate-down');

    const recentTrades = [...(c.trades || [])].reverse().slice(0, 5);
    const tradeRows = recentTrades.length > 0
      ? recentTrades.map(t => {
          const isBuy = t['액션'] === '매수';
          const pnl = t['손익'] && t['손익'] !== '-' ? t['손익'] + '원' : '-';
          const pnlClass = t['손익'] && t['손익'] !== '-'
            ? (!t['손익'].startsWith('-') ? 'up' : 'down') : '';
          return `<tr>
            <td style="color:var(--muted)">${t['날짜시간'] ? t['날짜시간'].substring(5,16) : '-'}</td>
            <td><span class="badge ${isBuy ? 'badge-buy' : 'badge-sell'}">${t['액션']}</span></td>
            <td>${t['가격'] || '-'}원</td>
            <td class="${pnlClass}">${pnl}</td>
          </tr>`;
        }).join('')
      : '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:16px;font-size:11px">거래 내역 없음</td></tr>';

    return `
      <div class="coin-card">
        <div class="coin-card-header">
          <div>
            <div class="coin-card-name">${c.coin} <span style="font-size:11px;color:var(--muted);font-weight:400">/KRW</span></div>
            <div class="coin-card-meta">${c.price ? fmt(c.price) + '원 현재가' : '--'}</div>
          </div>
          <span class="rate-badge ${rateClass}">
            ${hasPos ? fmtRate(c.profit_rate) : '미보유'}
          </span>
        </div>
        <div class="stats-row">
          <div class="stat-item">
            <div class="stat-label">평균 매수가</div>
            <div class="stat-val">${hasPos && c.avg_buy_price > 0 ? fmt(Math.round(c.avg_buy_price)) + '원' : '-'}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">평가금액</div>
            <div class="stat-val">${hasPos ? fmt(Math.round(c.coin_value)) + '원' : '-'}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">보유시간</div>
            <div class="stat-val">${hasPos && c.holding_hours > 0 ? c.holding_hours.toFixed(1) + 'h' : '-'}</div>
          </div>
        </div>
        <div class="trades-section">
          <div class="trades-title">최근 거래 내역</div>
          <table class="trade-table">
            <thead><tr><th>시각</th><th>구분</th><th>가격</th><th>손익</th></tr></thead>
            <tbody>${tradeRows}</tbody>
          </table>
        </div>
      </div>`;
  }).join('');
}

function renderHistory(history) {
  const tbody = document.getElementById('history-tbody');
  if (!history || history.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:20px;color:var(--muted);font-family:var(--mono);font-size:11px">청산 완료 종목 없음</td></tr>';
    return;
  }

  tbody.innerHTML = history.map(h => {
    const isUp = h.pnl_rate >= 0;
    const pillClass = h.pnl_rate > 0 ? 'pnl-up' : (h.pnl_rate < 0 ? 'pnl-down' : 'pnl-zero');
    const rateStr = (isUp ? '+' : '') + h.pnl_rate.toFixed(2) + '%';
    const pnlStr = (h.total_pnl >= 0 ? '+' : '') + fmt(Math.round(h.total_pnl)) + '원';
    const buyTime = h.first_buy_time ? h.first_buy_time.substring(5, 16) : '-';
    const sellTime = h.last_sell_time ? h.last_sell_time.substring(5, 16) : '-';

    return `<tr>
      <td>
        <div class="coin-badge">
          <div class="coin-dot" style="background:${isUp ? 'var(--accent)' : 'var(--red)'}"></div>
          <div><div class="coin-sym">${h.coin}</div></div>
        </div>
      </td>
      <td class="hide-mobile" style="color:var(--muted)">${h.avg_buy > 0 ? fmt(Math.round(h.avg_buy)) : '-'}</td>
      <td class="hide-mobile" style="font-weight:700">${h.avg_sell > 0 ? fmt(Math.round(h.avg_sell)) : '-'}</td>
      <td><span class="pnl-pill ${pillClass}">${rateStr}</span></td>
      <td style="color:${h.total_pnl >= 0 ? 'var(--accent)' : 'var(--red)'};font-weight:700">${pnlStr}</td>
      <td class="hide-mobile" style="color:var(--yellow)">-${fmt(Math.round(h.total_fee || 0))}</td>
      <td class="hide-mobile" style="color:var(--muted)">${h.buy_count}/${h.sell_count}</td>
      <td class="hide-mobile" style="color:var(--muted);font-size:10px">${buyTime}</td>
      <td style="color:var(--muted);font-size:10px">${sellTime}</td>
    </tr>`;
  }).join('');
}

async function fetchHistory() {
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    renderHistory(data);
  } catch(e) {
    console.error('히스토리 로드 실패:', e);
  }
}

async function fetchData() {
  try {
    const res = await fetch('/api/status');
    const d = await res.json();
    const s = d.summary;
    const sign = s.total_profit >= 0 ? '+' : '';

    // 요약
    document.getElementById('sum-total').textContent = fmt(Math.round(s.total_assets)) + '원';
    const pe = document.getElementById('sum-profit');
    pe.textContent = sign + fmt(Math.round(s.total_profit)) + '원';
    pe.className = 'card-value ' + (s.total_profit >= 0 ? 'up' : 'down');
    document.getElementById('sum-rate').textContent = '전체 수익률 ' + sign + s.total_profit_rate.toFixed(2) + '%';
    document.getElementById('sum-cash').textContent = '보유 현금: ' + fmt(Math.round(s.cash)) + '원';
    document.getElementById('sum-coins').textContent = d.coins.filter(c => c.quantity > 0).length + '개';

    // 자산 구성 카드
    const invested = s.total_assets - s.cash;
    const investRatio = s.total_assets > 0 ? (invested / s.total_assets * 100) : 0;
    document.getElementById('sum-cash2').textContent = fmt(Math.round(s.cash)) + '원';
    document.getElementById('sum-invested').textContent = fmt(Math.round(invested)) + '원';
    document.getElementById('sum-bar').style.width = investRatio.toFixed(1) + '%';
    document.getElementById('sum-bar-label').textContent = '평가 비중 ' + investRatio.toFixed(1) + '%';

    // 체크 현황
    const intervalSec = d.strategy.interval || 1800;
    const intervalLabel = intervalSec >= 60
      ? Math.round(intervalSec / 60) + '분'
      : intervalSec + '초';
    document.getElementById('c-interval').textContent = intervalLabel;
    document.getElementById('c-cycle').textContent = (d.cycle || 0).toLocaleString() + '회';
    document.getElementById('c-lastcheck').textContent = d.last_check || '-';
    document.getElementById('c-trades').textContent = `매수 ${s.buy_orders || 0}회 / 매도 ${s.sell_orders || 0}회`;
    const winRate = s.closed_trades > 0 ? (s.win_trades / s.closed_trades * 100).toFixed(1) : '0.0';
    const wr = document.getElementById('c-winrate');
    wr.textContent = winRate + '% (' + (s.closed_trades || 0) + '건)';
    wr.className = 'cycle-value ' + (parseFloat(winRate) >= 50 ? 'up' : 'down');
    const realized = s.total_profit_realized || 0;
    const re = document.getElementById('c-realized');
    re.textContent = (realized >= 0 ? '+' : '') + fmt(Math.round(realized)) + '원';
    re.className = 'cycle-value ' + (realized >= 0 ? 'up' : 'down');
    const feeEl = document.getElementById('c-fee');
    if (feeEl) feeEl.textContent = '-' + fmt(Math.round(s.total_fee || 0)) + '원';

    // 운용 종목 수 라벨
    const coinsSub = document.getElementById('sum-coins-sub');
    if (coinsSub) coinsSub.textContent = `최대 ${d.strategy.max_tickers || 4}개 · 종목당 ${(d.strategy.alloc_per_ticker || 25).toFixed(0)}%`;

    // 전략 (Swing v3.0)
    const st = d.strategy;
    document.getElementById('s-tf').textContent = (st.candle_interval || '1h').toUpperCase() + ' + 1D';
    document.getElementById('s-ema').textContent = `${st.ema_fast||9}/${st.ema_mid||21}/${st.ema_long||50}`;
    document.getElementById('s-donchian').textContent = (st.donchian_period||24) + '봉';
    document.getElementById('s-tp').textContent = '+' + (st.take_profit_base||15).toFixed(1) + '%';
    const trailTrig = st.trailing_trigger || 7;
    const trailDrop = st.trailing_drop || 3;
    document.getElementById('s-trail').textContent = `+${trailTrig.toFixed(0)}%/-${trailDrop.toFixed(0)}%`;
    const slMin = st.atr_stop_min || -4;
    const slMax = st.atr_stop_max || -8;
    document.getElementById('s-sl').textContent = `${slMax.toFixed(0)}~${slMin.toFixed(0)}%`;
    document.getElementById('s-alloc').textContent = `${st.max_tickers||4}종/${(st.alloc_per_ticker||25).toFixed(0)}%`;
    const fsHours = st.force_sell_hours || 336;
    document.getElementById('s-forcesell').textContent = fsHours >= 24
      ? Math.round(fsHours / 24) + '일'
      : fsHours + 'h';
    const btcTrend = d.market && d.market.btc_trend ? d.market.btc_trend : '-';
    const btcEl = document.getElementById('s-btctrend');
    btcEl.textContent = btcTrend === 'UP' ? '↑ UP' : btcTrend === 'DOWN' ? '↓ DOWN' : '➡ SIDE';
    btcEl.className = 'strat-chip-value ' + (btcTrend === 'UP' ? 'green' : btcTrend === 'DOWN' ? 'red' : 'yellow');

    // 테이블 & 카드
    renderProfitTable(d.coins);
    renderCoinCards(d.coins);

    document.getElementById('footer-time').textContent = '마지막 업데이트: ' + d.updated_at + ' | 30초마다 자동 갱신';
    document.getElementById('updated').textContent = d.updated_at;

  } catch(e) {
    console.error('데이터 로드 실패:', e);
  }
}


async function fetchDataStats() {
  try {
    const res = await fetch('/api/data-stats');
    const data = await res.json();
    const el = document.getElementById('c-datacnt');
    if (el) el.textContent = (data.total || 0).toLocaleString() + '건';
    const el2 = document.getElementById('c-lastfetch');
    if (el2) el2.textContent = data.last_fetch || '-';
  } catch(e) {
    console.error('데이터 수집 현황 로드 실패:', e);
  }
}

fetchData();
fetchHistory();
fetchDataStats();
setInterval(fetchData, 30000);
setInterval(fetchHistory, 30000);
setInterval(fetchDataStats, 60000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

if __name__ == "__main__":
    print("=" * 50)
    print("  🚀 TRADER_J 대시보드 서버 시작!")
    print("  브라우저에서 http://localhost:5000 접속")
    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=5000)