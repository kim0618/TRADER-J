# server.py - 웹 대시보드 서버
from flask import Flask, jsonify, render_template_string
import json
import csv
import os
import glob
import requests
from datetime import datetime

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INITIAL_BALANCE = 1_000_000

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
        "total_trades": 0,
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
            "stop_loss": getattr(cfg, "STOP_LOSS", -0.03) * 100,
            "take_profit_base": getattr(cfg, "TAKE_PROFIT_BASE", 0.06) * 100,
            "trade_ratio": getattr(cfg, "TRADE_RATIO", 0.3) * 100,
            "fee_rate": getattr(cfg, "FEE_RATE", 0.0025) * 100,
            "interval": getattr(cfg, "INTERVAL_SECONDS", 60),
            "max_tickers": getattr(cfg, "TOP_TICKER_LIMIT", 3),
            "force_sell_hours": getattr(cfg, "FORCE_SELL_HOURS", 48),
        }
    except:
        return {
            "short_ma": 5, "long_ma": 20, "stop_loss": -3.0,
            "take_profit_base": 6.0, "trade_ratio": 30.0,
            "fee_rate": 0.25, "interval": 60, "max_tickers": 3,
            "force_sell_hours": 48,
        }

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

    return jsonify({
        "coins": coins,
        "summary": {
            "total_assets": total_assets,
            "total_profit": total_profit,
            "total_profit_rate": total_profit_rate,
            "cash": cash,
            "total_trades": portfolio.get("total_trades", 0),
            "win_trades": portfolio.get("win_trades", 0),
            "total_profit_realized": portfolio.get("total_profit", 0),
        },
        "strategy": strategy,
        "cycle": cycle_info.get("cycle", 0),
        "last_check": cycle_info.get("last_check", "-"),
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
    --bg: #060910;
    --panel: #0d1117;
    --panel2: #111820;
    --border: #1a2332;
    --border2: #243044;
    --accent: #00ff88;
    --accent2: #0088ff;
    --red: #ff4455;
    --yellow: #ffcc00;
    --text: #e2e8f0;
    --muted: #4a5568;
    --mono: 'Space Mono', monospace;
    --sans: 'Noto Sans KR', sans-serif;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:var(--sans); min-height:100vh; }
  body::before {
    content:''; position:fixed; inset:0;
    background-image:
      linear-gradient(rgba(0,255,136,0.02) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,136,0.02) 1px, transparent 1px);
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
    0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(0,255,136,0.4)}
    50%{opacity:.7;box-shadow:0 0 0 6px rgba(0,255,136,0)}
  }

  .summary-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:16px; }
  .summary-card {
    background:var(--panel); border:1px solid var(--border);
    border-radius:12px; padding:20px; position:relative; overflow:hidden;
  }
  .summary-card::after {
    content:''; position:absolute; top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg, var(--accent), var(--accent2));
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
    border-radius:12px; overflow:hidden; margin-bottom:24px;
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
      <div class="cycle-label">총 거래</div>
      <div class="cycle-value" style="color:var(--yellow)" id="c-trades">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">승률</div>
      <div class="cycle-value" id="c-winrate">--</div>
    </div>
    <div class="cycle-divider"></div>
    <div class="cycle-item">
      <div class="cycle-label">실현 손익</div>
      <div class="cycle-value" id="c-realized">--</div>
    </div>
  </div>

  <!-- 전략 패널 -->
  <div class="strategy-panel">
    <div class="strategy-title">📊 RSI + 볼린저밴드 전략 <span class="active-badge">● ACTIVE</span></div>
    <div class="strat-params">
      <div class="strat-chip"><div class="strat-chip-label">단기 이평</div><div class="strat-chip-value blue" id="s-short">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">장기 이평</div><div class="strat-chip-value blue" id="s-long">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">매수 비율</div><div class="strat-chip-value" id="s-ratio">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">기본 익절</div><div class="strat-chip-value green" id="s-tp">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">손절</div><div class="strat-chip-value red" id="s-sl">--</div></div>
      <div class="strat-chip"><div class="strat-chip-label">수수료</div><div class="strat-chip-value" id="s-fee">--</div></div>
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

    // 체크 현황
    document.getElementById('c-interval').textContent = d.strategy.interval + '초';
    document.getElementById('c-cycle').textContent = (d.cycle || 0).toLocaleString() + '회';
    document.getElementById('c-lastcheck').textContent = d.last_check || '-';
    document.getElementById('c-trades').textContent = (s.total_trades || 0) + '회';
    const winRate = s.total_trades > 0 ? (s.win_trades / s.total_trades * 100).toFixed(1) : '0.0';
    const wr = document.getElementById('c-winrate');
    wr.textContent = winRate + '%';
    wr.className = 'cycle-value ' + (parseFloat(winRate) >= 50 ? 'up' : 'down');
    const realized = s.total_profit_realized || 0;
    const re = document.getElementById('c-realized');
    re.textContent = (realized >= 0 ? '+' : '') + fmt(Math.round(realized)) + '원';
    re.className = 'cycle-value ' + (realized >= 0 ? 'up' : 'down');

    // 전략
    const st = d.strategy;
    document.getElementById('s-short').textContent = st.short_ma + '봉';
    document.getElementById('s-long').textContent = st.long_ma + '봉';
    document.getElementById('s-ratio').textContent = st.trade_ratio.toFixed(0) + '%';
    document.getElementById('s-tp').textContent = '+' + st.take_profit_base.toFixed(1) + '%';
    document.getElementById('s-sl').textContent = st.stop_loss.toFixed(1) + '%';
    document.getElementById('s-fee').textContent = st.fee_rate.toFixed(2) + '%';
    document.getElementById('s-maxticker').textContent = st.max_tickers + '개';
    document.getElementById('s-forcesell').textContent = st.force_sell_hours + 'h';

    // 테이블 & 카드
    renderProfitTable(d.coins);
    renderCoinCards(d.coins);

    document.getElementById('footer-time').textContent = '마지막 업데이트: ' + d.updated_at + ' | 30초마다 자동 갱신';
    document.getElementById('updated').textContent = d.updated_at;

  } catch(e) {
    console.error('데이터 로드 실패:', e);
  }
}

fetchData();
setInterval(fetchData, 30000);
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