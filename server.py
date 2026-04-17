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
            "short_ma": getattr(cfg, "SHORT_MA", 5),
            "long_ma": getattr(cfg, "LONG_MA", 20),
            "stop_loss": getattr(cfg, "STOP_LOSS", -0.035) * 100,
            "take_profit_base": getattr(cfg, "TAKE_PROFIT_BASE", 0.04) * 100,
            "trade_ratio": getattr(cfg, "TRADE_RATIO", 0.5) * 100,
            "fee_rate": getattr(cfg, "FEE_RATE", 0.0025) * 100,
            "slippage": getattr(cfg, "SLIPPAGE_RATE", 0.002) * 100,
            "interval": getattr(cfg, "INTERVAL_SECONDS", 300),
            "max_tickers": getattr(cfg, "TOP_TICKER_LIMIT", 3),
            "force_sell_hours": getattr(cfg, "FORCE_SELL_HOURS", 48),
            "global_stop_loss": getattr(cfg, "GLOBAL_STOP_LOSS", -0.05) * 100,
            # v2 전략 파라미터
            "rsi_period": getattr(cfg, "RSI_PERIOD", 9),
            "rsi_oversold": getattr(cfg, "RSI_OVERSOLD", 30),
            "rsi_overbought": getattr(cfg, "RSI_OVERBOUGHT", 68),
            "bb_std": getattr(cfg, "BB_STD", 2.0),
            "bb_lower": getattr(cfg, "BB_LOWER_THRESHOLD", 0.15),
            "macd_fast": getattr(cfg, "MACD_FAST", 8),
            "macd_slow": getattr(cfg, "MACD_SLOW", 21),
            "macd_signal": getattr(cfg, "MACD_SIGNAL", 5),
            "adx_period": getattr(cfg, "ADX_PERIOD", 14),
            "adx_trend": getattr(cfg, "ADX_TREND_THRESHOLD", 25),
            "trailing_trigger": getattr(cfg, "TRAILING_STOP_TRIGGER", 0.025) * 100,
            "trailing_drop": getattr(cfg, "TRAILING_STOP_DROP", 0.012) * 100,
            "max_buy_count": getattr(cfg, "MAX_BUY_COUNT", 2),
            "strategy_sell": getattr(cfg, "STRATEGY_SELL_ENABLED", False),
            "volume_min": getattr(cfg, "VOLUME_RATIO_MIN", 0.5),
        }
    except:
        return {
            "short_ma": 5, "long_ma": 20, "stop_loss": -3.5,
            "take_profit_base": 4.0, "trade_ratio": 50.0,
            "fee_rate": 0.25, "slippage": 0.2, "interval": 300,
            "max_tickers": 3, "force_sell_hours": 48,
            "global_stop_loss": -5.0,
            "rsi_period": 9, "rsi_oversold": 30, "rsi_overbought": 68,
            "bb_std": 2.0, "bb_lower": 0.15,
            "macd_fast": 8, "macd_slow": 21, "macd_signal": 5,
            "adx_period": 14, "adx_trend": 25,
            "trailing_trigger": 2.5, "trailing_drop": 1.2,
            "max_buy_count": 2, "strategy_sell": False, "volume_min": 0.5,
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
        "start_time": cycle_info.get("start_time", "-"),
        "running_hours": cycle_info.get("running_hours", 0),
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
    --bg: #f0f2f5;
    --panel: #ffffff;
    --panel2: #f8f9fb;
    --border: #e2e6ed;
    --border2: #d0d6e0;
    --accent: #00a86b;
    --accent2: #1a6ef5;
    --red: #e53e3e;
    --yellow: #d97706;
    --text: #1a202c;
    --muted: #4a5568;
    --mono: 'Space Mono', monospace;
    --sans: 'Noto Sans KR', sans-serif;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:var(--sans); min-height:100vh; }
  body::before {
    content:''; position:fixed; inset:0;
    background-image:
      linear-gradient(rgba(0,0,0,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,0,0,0.03) 1px, transparent 1px);
    background-size:48px 48px; pointer-events:none; z-index:0;
  }
  .container { position:relative; z-index:1; max-width:1400px; margin:0 auto; padding:24px; }

  header {
    display:flex; justify-content:space-between; align-items:center;
    margin-bottom:28px; padding-bottom:18px; border-bottom:1px solid var(--border);
  }
  .logo { font-family:var(--mono); font-size:20px; font-weight:700; color:var(--accent); letter-spacing:3px; }
  .logo span { color:var(--muted); }
  .header-right { display:flex; align-items:center; gap:16px; font-family:var(--mono); font-size:12px; color:var(--muted); }
  .live-dot { width:8px; height:8px; border-radius:50%; background:var(--accent); animation:pulse 2s infinite; }
  @keyframes pulse {
    0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(0,168,107,0.4)}
    50%{opacity:.7;box-shadow:0 0 0 6px rgba(0,168,107,0)}
  }

  .summary-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:16px; }
  .summary-card {
    background:var(--panel); border:1px solid var(--border);
    border-radius:12px; padding:20px; position:relative; overflow:hidden;
  }
  .summary-card::after {
    content:''; position:absolute; top:0; left:0; right:0; height:3px;
    background:linear-gradient(90deg, var(--accent), var(--accent2));
    border-radius:12px 12px 0 0;
  }
  .card-label { font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-bottom:10px; font-family:var(--mono); }
  .card-value { font-family:var(--mono); font-size:24px; font-weight:700; }
  .card-sub { font-size:11px; color:var(--muted); margin-top:6px; font-family:var(--mono); }
  .up { color:var(--accent) !important; }
  .down { color:var(--red) !important; }

  /* 체크 현황 바 */
  .cycle-bar {
    background:var(--panel); border:1px solid var(--border);
    border-radius:10px; padding:14px 20px; margin-bottom:16px;
    display:flex; align-items:center; gap:0; flex-wrap:wrap;
  }
  .cycle-item { display:flex; flex-direction:column; gap:4px; padding:0 20px; }
  .cycle-item:first-child { padding-left:0; }
  .cycle-label { font-family:var(--mono); font-size:9px; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; }
  .cycle-value { font-family:var(--mono); font-size:14px; font-weight:700; color:var(--accent); }
  .cycle-divider { width:1px; height:32px; background:var(--border); flex-shrink:0; }

  /* 전략 패널 */
  .strategy-panel {
    background:var(--panel); border:1px solid var(--border);
    border-radius:12px; padding:14px 20px; margin-bottom:20px;
    display:flex; align-items:center; gap:16px; flex-wrap:wrap;
  }
  .strategy-title { font-family:var(--mono); font-size:12px; color:var(--accent); font-weight:700; white-space:nowrap; display:flex; align-items:center; gap:8px; }
  .active-badge { background:rgba(0,255,136,0.1); border:1px solid rgba(0,255,136,0.3); color:var(--accent); font-family:var(--mono); font-size:9px; padding:2px 8px; border-radius:20px; }
  .strat-params { display:flex; gap:8px; flex-wrap:wrap; flex:1; }
  .strat-chip { background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:8px; padding:7px 12px; text-align:center; min-width:68px; }
  .strat-chip-label { font-size:9px; color:var(--muted); text-transform:uppercase; letter-spacing:1px; margin-bottom:3px; font-family:var(--mono); }
  .strat-chip-value { font-family:var(--mono); font-size:12px; font-weight:700; color:var(--text); }
  .green { color:var(--accent) !important; }
  .red { color:var(--red) !important; }
  .blue { color:var(--accent2) !important; }
  .yellow { color:var(--yellow) !important; }

  /* 섹션 타이틀 */
  .section-title {
    font-family:var(--mono); font-size:11px; color:var(--muted);
    text-transform:uppercase; letter-spacing:2px; margin-bottom:12px;
    display:flex; align-items:center; gap:8px;
  }
  .section-title::before { content:''; width:3px; height:13px; background:var(--accent2); border-radius:2px; }

  /* 수익률 테이블 */
  .profit-table-wrap {
    background:var(--panel); border:1px solid var(--border);
    border-radius:12px; overflow-x:auto; margin-bottom:24px;
    -webkit-overflow-scrolling:touch;
  }
  .profit-table { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:12px; }
  .profit-table thead th {
    background:rgba(255,255,255,0.03); padding:12px 18px;
    text-align:left; color:var(--muted); font-size:10px;
    text-transform:uppercase; letter-spacing:1.5px;
    border-bottom:1px solid var(--border); font-weight:400;
  }
  .profit-table thead th:not(:first-child) { text-align:right; }
  .profit-table tbody tr { border-bottom:1px solid var(--border); transition:background 0.15s; }
  .profit-table tbody tr:last-child { border-bottom:none; }
  .profit-table tbody tr:hover { background:rgba(255,255,255,0.02); }
  .profit-table td { padding:16px 18px; vertical-align:middle; }
  .profit-table td:not(:first-child) { text-align:right; }

  .coin-badge { display:inline-flex; align-items:center; gap:10px; }
  .coin-dot { width:8px; height:8px; border-radius:50%; }
  .coin-sym { font-size:15px; font-weight:700; color:var(--accent2); }
  .coin-label { font-size:10px; color:var(--muted); margin-top:2px; }

  .rate-badge { display:inline-block; padding:5px 12px; border-radius:6px; font-size:12px; font-weight:700; }
  .rate-up { background:rgba(0,255,136,0.1); color:var(--accent); border:1px solid rgba(0,255,136,0.2); }
  .rate-down { background:rgba(255,68,85,0.1); color:var(--red); border:1px solid rgba(255,68,85,0.2); }
  .rate-zero { background:rgba(255,255,255,0.04); color:var(--muted); border:1px solid var(--border); }

  .holding-bar-wrap { display:flex; align-items:center; gap:8px; justify-content:flex-end; }
  .holding-bar { width:56px; height:4px; background:var(--border); border-radius:2px; overflow:hidden; }
  .holding-bar-fill { height:100%; border-radius:2px; transition:width 0.4s; }

  /* 종목 거래내역 카드 */
  .coins-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(400px, 1fr)); gap:16px; margin-bottom:24px; }
  .coin-card { background:var(--panel); border:1px solid var(--border); border-radius:12px; overflow:hidden; }
  .coin-card-header {
    display:flex; justify-content:space-between; align-items:center;
    padding:14px 18px; border-bottom:1px solid var(--border);
    background:rgba(255,255,255,0.02);
  }
  .coin-card-name { font-family:var(--mono); font-size:14px; font-weight:700; color:var(--accent2); }
  .coin-card-meta { font-family:var(--mono); font-size:10px; color:var(--muted); margin-top:3px; }

  .stats-row { display:grid; grid-template-columns:repeat(3,1fr); border-bottom:1px solid var(--border); }
  .stat-item { padding:11px 16px; border-right:1px solid var(--border); }
  .stat-item:last-child { border-right:none; }
  .stat-label { font-family:var(--mono); font-size:9px; color:var(--muted); text-transform:uppercase; letter-spacing:1px; margin-bottom:4px; }
  .stat-val { font-family:var(--mono); font-size:12px; font-weight:700; color:var(--text); }

  .trades-section { padding:14px 16px; }
  .trades-title { font-family:var(--mono); font-size:9px; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-bottom:10px; }
  .trade-table { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:11px; }
  .trade-table th { text-align:left; color:var(--muted); padding:5px 6px; border-bottom:1px solid var(--border); font-weight:400; font-size:9px; text-transform:uppercase; letter-spacing:1px; }
  .trade-table th:not(:first-child) { text-align:right; }
  .trade-table td { padding:7px 6px; border-bottom:1px solid rgba(26,35,50,0.4); }
  .trade-table td:not(:first-child) { text-align:right; }
  .trade-table tr:last-child td { border-bottom:none; }
  .badge { display:inline-block; padding:1px 7px; border-radius:4px; font-size:10px; font-weight:700; }
  .badge-buy { background:rgba(0,255,136,0.15); color:var(--accent); }
  .badge-sell { background:rgba(255,68,85,0.15); color:var(--red); }


  .footer { text-align:center; font-family:var(--mono); font-size:11px; color:var(--muted); padding-top:16px; border-top:1px solid var(--border); }

  /* 청산 히스토리 */
  .history-table-wrap {
    background:var(--panel); border:1px solid var(--border);
    border-radius:12px; overflow-x:auto; margin-bottom:24px;
    -webkit-overflow-scrolling:touch;
  }
  .history-table { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:12px; }
  .history-table thead th {
    background:rgba(255,255,255,0.03); padding:12px 18px;
    text-align:left; color:var(--muted); font-size:10px;
    text-transform:uppercase; letter-spacing:1.5px;
    border-bottom:1px solid var(--border); font-weight:400;
  }
  .history-table thead th:not(:first-child) { text-align:right; }
  .history-table tbody tr { border-bottom:1px solid var(--border); transition:background 0.15s; }
  .history-table tbody tr:last-child { border-bottom:none; }
  .history-table tbody tr:hover { background:rgba(255,255,255,0.02); }
  .history-table td { padding:14px 18px; vertical-align:middle; }
  .history-table td:not(:first-child) { text-align:right; }
  .pnl-pill {
    display:inline-block; padding:4px 12px; border-radius:20px;
    font-size:13px; font-weight:700;
  }
  .pnl-up { background:rgba(0,255,136,0.1); color:var(--accent); border:1px solid rgba(0,255,136,0.25); }
  .pnl-down { background:rgba(255,68,85,0.1); color:var(--red); border:1px solid rgba(255,68,85,0.25); }
  .pnl-zero { background:rgba(255,255,255,0.04); color:var(--muted); border:1px solid var(--border); }
  .arrow-icon { color:var(--muted); margin:0 4px; font-size:10px; }

  /* ===== 반응형 (모바일) ===== */
  @media (max-width: 768px) {
    .container { padding:12px; }

    header { flex-direction:column; align-items:flex-start; gap:6px; margin-bottom:16px; padding-bottom:12px; }
    .logo { font-size:16px; }
    .header-right { font-size:11px; gap:10px; }

    .summary-grid { grid-template-columns:repeat(2,1fr); gap:10px; margin-bottom:10px; }
    .summary-card { padding:14px 12px; }
    .card-value { font-size:18px; }
    .card-sub { font-size:10px; }

    /* cycle-bar → 2열 그리드 */
    .cycle-bar { display:grid; grid-template-columns:1fr 1fr; padding:0; gap:0; border-radius:10px; overflow:hidden; }
    .cycle-divider { display:none; }
    .cycle-item { padding:10px 12px; border-bottom:1px solid var(--border); width:auto; }
    .cycle-item:nth-child(4n+1) { border-right:1px solid var(--border); }
    .cycle-item:nth-child(n+13) { border-bottom:none; }

    .strategy-panel { padding:12px; gap:8px; }
    .strategy-title { font-size:11px; }
    .strat-params { gap:5px; }
    .strat-chip { min-width:50px; padding:6px 8px; }
    .strat-chip-value { font-size:11px; }
    .strat-chip-label { font-size:8px; }

    .section-title { font-size:10px; margin-bottom:10px; }

    .profit-table { min-width:520px; font-size:11px; }
    .profit-table thead th { padding:10px; font-size:9px; }
    .profit-table td { padding:12px 10px; }

    .history-table { min-width:680px; font-size:11px; }
    .history-table thead th { padding:10px; font-size:9px; }
    .history-table td { padding:10px; }

    .coins-grid { grid-template-columns:1fr; gap:12px; }
    .stats-row { grid-template-columns:repeat(3,1fr); }

    .footer { font-size:10px; }
  }

  @media (max-width: 480px) {
    .summary-grid { grid-template-columns:1fr; }
    .card-value { font-size:20px; }
    .strat-params { gap:4px; }
    .strat-chip { min-width:46px; }
  }
</style>
</head>
<body>
<div class="container">

  <header>
    <div class="logo">TRADER<span>_</span>J
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
      <div style="display:flex;flex-direction:column;gap:8px;margin-top:4px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-family:var(--mono);font-size:10px;color:var(--muted)">💰 현금 잔고</span>
          <span class="card-value" id="sum-cash2" style="font-size:16px;color:var(--accent)">--</span>
        </div>
        <div style="height:1px;background:var(--border)"></div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-family:var(--mono);font-size:10px;color:var(--muted)">📈 평가 금액</span>
          <span class="card-value" id="sum-invested" style="font-size:16px;color:var(--accent2)">--</span>
        </div>
        <div style="height:3px;background:var(--border);border-radius:2px;overflow:hidden;margin-top:2px">
          <div id="sum-bar" style="height:100%;background:linear-gradient(90deg,var(--accent2),var(--accent));border-radius:2px;transition:width 0.4s;width:0%"></div>
        </div>
        <div class="card-sub" id="sum-bar-label" style="text-align:right">투자 비중 --</div>
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
      <div class="card-sub">최대 3개 자동 선정</div>
    </div>
  </div>

  <!-- 체크 현황 -->
  <div class="cycle-bar">
    <div class="cycle-item">
      <div class="cycle-label">시작 시각</div>
      <div class="cycle-value" style="color:var(--accent2);font-size:12px" id="c-starttime">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">가동 시간</div>
      <div class="cycle-value" style="color:var(--accent)" id="c-uptime">--</div>
    </div>
    <div class="cycle-divider"></div>
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

  <!-- 전략 패널 v2 -->
  <div class="strategy-panel">
    <div class="strategy-title">📊 v2 ADX+RSI+BB+MACD <span class="active-badge" id="s-mode">● ACTIVE</span></div>
    <div class="strat-params">
      <div class="strat-chip"><div class="strat-chip-label">RSI</div><div class="strat-chip-value blue" id="s-rsi">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">BB</div><div class="strat-chip-value blue" id="s-bb">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">MACD</div><div class="strat-chip-value blue" id="s-macd">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">ADX</div><div class="strat-chip-value blue" id="s-adx">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">익절</div><div class="strat-chip-value green" id="s-tp">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">손절</div><div class="strat-chip-value red" id="s-sl">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">트레일링</div><div class="strat-chip-value green" id="s-trail">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">DCA</div><div class="strat-chip-value yellow" id="s-dca">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">거래량</div><div class="strat-chip-value" id="s-vol">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">최대 종목</div><div class="strat-chip-value yellow" id="s-maxticker">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">강제 청산</div><div class="strat-chip-value red" id="s-forcesell">--</div></div>
    </div>
  </div>


  <!-- 종목별 수익률 테이블 -->
  <div class="section-title">종목별 수익률 현황</div>
  <div class="profit-table-wrap">
    <table class="profit-table">
      <thead>
        <tr>
          <th>종목</th>
          <th>매수 평균가</th>
          <th>현재 가격</th>
          <th>수익률</th>
          <th>평가금액</th>
          <th>분할매수</th>
          <th>보유시간</th>
        </tr>
      </thead>
      <tbody id="profit-tbody">
        <tr><td colspan="7" style="text-align:center;padding:28px;color:var(--muted);font-family:var(--mono);font-size:12px">데이터 로딩 중...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- 청산 히스토리 -->
  <div class="section-title">청산 완료 종목 히스토리</div>
  <div class="history-table-wrap">
    <table class="history-table">
      <thead>
        <tr>
          <th>종목</th>
          <th>매수 평균가</th>
          <th>매도 평균가</th>
          <th>수익률</th>
          <th>실현 손익</th>
          <th>수수료</th>
          <th>매수/매도</th>
          <th>진입 시각</th>
          <th>청산 시각</th>
        </tr>
      </thead>
      <tbody id="history-tbody">
        <tr><td colspan="8" style="text-align:center;padding:28px;color:var(--muted);font-family:var(--mono);font-size:12px">데이터 로딩 중...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- 종목별 거래내역 -->
  <div class="section-title">종목별 거래 내역</div>
  <div class="coins-grid" id="coins-grid">
    <div style="color:var(--muted);font-family:var(--mono);padding:40px;text-align:center;font-size:12px">데이터 로딩 중...</div>
  </div>

  <div class="footer" id="footer-time">마지막 업데이트: -- | 30초마다 자동 갱신</div>
</div>

<script>
function fmt(n) { return Number(n).toLocaleString('ko-KR'); }
function fmtRate(r) { return (r >= 0 ? '+' : '') + Number(r).toFixed(2) + '%'; }

function renderProfitTable(coins) {
  const tbody = document.getElementById('profit-tbody');
  if (!coins || coins.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:28px;color:var(--muted);font-family:var(--mono);font-size:12px">보유 종목 없음</td></tr>';
    return;
  }

  tbody.innerHTML = coins.map(c => {
    const hasPos = c.quantity > 0;
    const rateClass = !hasPos ? 'rate-zero' : (c.profit_rate >= 0 ? 'rate-up' : 'rate-down');
    const rateText = !hasPos ? '미보유' : fmtRate(c.profit_rate);
    const profitAmt = hasPos && c.profit !== 0
      ? `<div style="font-size:10px;color:var(--muted);margin-top:4px">${c.profit >= 0 ? '+' : ''}${fmt(Math.round(c.profit))}원</div>`
      : '';

    // 보유시간 바 (48시간 기준)
    const maxH = 48;
    const barPct = Math.min((c.holding_hours / maxH) * 100, 100);
    const barColor = barPct > 80 ? '#ff4455' : barPct > 50 ? '#ffcc00' : '#0088ff';
    const holdStr = c.holding_hours > 0
      ? (c.holding_hours >= 1 ? c.holding_hours.toFixed(1) + 'h' : Math.round(c.holding_hours * 60) + 'm')
      : '-';

    return `<tr>
      <td>
        <div class="coin-badge">
          <div class="coin-dot" style="background:${hasPos ? 'var(--accent)' : 'var(--muted)'}"></div>
          <div>
            <div class="coin-sym">${c.coin}</div>
            <div class="coin-label">/KRW</div>
          </div>
        </div>
      </td>
      <td style="color:var(--muted);font-size:13px">
        ${hasPos && c.avg_buy_price > 0 ? fmt(Math.round(c.avg_buy_price)) + '원' : '<span style="color:var(--muted)">-</span>'}
      </td>
      <td style="color:var(--text);font-size:13px;font-weight:700">
        ${c.price ? fmt(c.price) + '원' : '<span style="color:var(--muted)">-</span>'}
      </td>
      <td>
        <span class="rate-badge ${rateClass}">${rateText}</span>
        ${profitAmt}
      </td>
      <td style="color:var(--text)">
        ${hasPos ? fmt(Math.round(c.coin_value)) + '원' : '<span style="color:var(--muted)">-</span>'}
      </td>
      <td style="color:var(--accent2);font-weight:700">
        ${hasPos ? c.buy_count + '회' : '<span style="color:var(--muted)">-</span>'}
      </td>
      <td>
        ${hasPos ? `
          <div class="holding-bar-wrap">
            <div class="holding-bar">
              <div class="holding-bar-fill" style="width:${barPct}%;background:${barColor}"></div>
            </div>
            <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${holdStr}</span>
          </div>
        ` : '<span style="color:var(--muted);font-size:11px">-</span>'}
      </td>
    </tr>`;
  }).join('');
}

function renderCoinCards(coins) {
  const grid = document.getElementById('coins-grid');
  if (!coins || coins.length === 0) {
    grid.innerHTML = '<div style="color:var(--muted);font-family:var(--mono);padding:40px;text-align:center;font-size:12px">거래 내역 없음</div>';
    return;
  }

  grid.innerHTML = coins.map(c => {
    const hasPos = c.quantity > 0;
    const rateClass = !hasPos ? 'rate-zero' : (c.profit_rate >= 0 ? 'rate-up' : 'rate-down');

    const recentTrades = [...(c.trades || [])].reverse().slice(0, 8);
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
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:28px;color:var(--muted);font-family:var(--mono);font-size:12px">청산 완료 종목 없음</td></tr>';
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
          <div>
            <div class="coin-sym">${h.coin}</div>
            <div class="coin-label">/KRW</div>
          </div>
        </div>
      </td>
      <td style="color:var(--muted)">${h.avg_buy > 0 ? fmt(Math.round(h.avg_buy)) + '원' : '-'}</td>
      <td style="color:var(--text);font-weight:700">${h.avg_sell > 0 ? fmt(Math.round(h.avg_sell)) + '원' : '-'}</td>
      <td><span class="pnl-pill ${pillClass}">${rateStr}</span></td>
      <td style="color:${h.total_pnl >= 0 ? 'var(--accent)' : 'var(--red)'};font-weight:700">${pnlStr}</td>
      <td style="color:var(--yellow);font-size:12px">-${fmt(Math.round(h.total_fee || 0))}원</td>
      <td style="color:var(--muted)">${h.buy_count}매수 / ${h.sell_count}매도</td>
      <td style="color:var(--muted);font-size:11px">${buyTime}</td>
      <td style="color:var(--muted);font-size:11px">${sellTime}</td>
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
    const startTime = d.start_time || '-';
    document.getElementById('c-starttime').textContent = startTime !== '-' ? startTime.substring(5, 16) : '-';
    const runH = d.running_hours || 0;
    document.getElementById('c-uptime').textContent = runH >= 1 ? runH.toFixed(1) + 'h' : Math.round(runH * 60) + 'm';
    document.getElementById('c-interval').textContent = d.strategy.interval + '초';
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

    // 전략 v2
    const st = d.strategy;
    document.getElementById('s-rsi').textContent = st.rsi_period + '봉 ' + st.rsi_oversold + '/' + st.rsi_overbought;
    document.getElementById('s-bb').textContent = st.bb_std + 'σ <' + st.bb_lower;
    document.getElementById('s-macd').textContent = st.macd_fast + '/' + st.macd_slow + '/' + st.macd_signal;
    document.getElementById('s-adx').textContent = st.adx_period + '봉 >' + st.adx_trend;
    document.getElementById('s-tp').textContent = '+' + st.take_profit_base.toFixed(1) + '%';
    document.getElementById('s-sl').textContent = st.stop_loss.toFixed(1) + '%';
    document.getElementById('s-trail').textContent = '+' + st.trailing_trigger.toFixed(1) + '/-' + st.trailing_drop.toFixed(1) + '%';
    document.getElementById('s-dca').textContent = st.max_buy_count + '회/' + st.trade_ratio.toFixed(0) + '%';
    document.getElementById('s-vol').textContent = '>' + st.volume_min + 'x';
    document.getElementById('s-maxticker').textContent = st.max_tickers + '개';
    document.getElementById('s-forcesell').textContent = st.force_sell_hours + 'h';
    const modeEl = document.getElementById('s-mode');
    modeEl.textContent = st.strategy_sell ? '● 전략매도 ON' : '● 기계적 출구';

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