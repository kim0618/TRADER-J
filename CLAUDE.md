# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate virtualenv
source venv/bin/activate

# Run the trading bot
python main.py

# Run the web dashboard (separate terminal)
python server.py  # http://localhost:5000

# Print portfolio report
python reporter.py

# Test ticker selection logic
python collector.py

# Test strategy signals
python strategy.py
```

## Architecture

This is a **paper trading bot** (simulation, no real money) for Korean cryptocurrency exchange Bithumb. It runs as two separate processes:

1. **`main.py`** — the main trading loop (runs every 60s by default)
2. **`server.py`** — Flask web dashboard for monitoring (port 5000)

### Data Flow

```
collector.py  →  strategy.py  →  main.py  →  paper_trader.py
  (Bithumb API)   (RSI+BB+MACD)   (orchestration)  (portfolio state)
                                       ↓
                              data/portfolio.json
                              data/trades_<COIN>.csv
                              data/cycle_info.json
                              logs/bot.log
```

### Module Responsibilities

- **`config.py`** — All tunable constants (thresholds, intervals, limits). Edit this to change strategy parameters.
- **`collector.py`** — Bithumb public API calls: current price, OHLCV candles (5m/1h), smart ticker selection with 5-min cache.
- **`strategy.py`** — Signal generation: `check_signal()` returns `"BUY"` / `"SELL"` / `"HOLD"` using RSI, Bollinger Bands, MACD, and 1h trend. `calculate_indicators()` computes all indicators on a DataFrame.
- **`paper_trader.py`** — `PortfolioManager` class: manages `data/portfolio.json`, handles buy/sell logic, dynamic take-profit (decreases with holding time), trailing stop, sell cooldown (30min), and per-symbol trade CSV logs.
- **`main.py`** — Main loop: ticker refresh every 10 cycles, manages all active positions (including tickers no longer in top list), checks sell conditions before buy conditions.
- **`reporter.py`** — CLI report: portfolio summary, per-symbol trade summary, MDD calculation.
- **`server.py`** — Flask REST API (`/api/status`, `/api/history`) + single-page HTML dashboard (inline in the file).

### Key Design Decisions

- **Portfolio allocation**: 1/3 of initial balance per ticker, max 3 tickers simultaneously.
- **Split buying**: Up to 3 buy entries per ticker, 40% of remaining allocation per entry, 10-min minimum between entries.
- **Dynamic take-profit**: Starts at 4.5%, decreases to 3.5%/2.5%/1.5% at 6h/12h/24h holding time.
- **Sell priority in `main.py`**: Force sell (36h) > Trailing stop > Stop loss (-2.5%) > Dynamic take-profit > Strategy SELL signal.
- **Ticker refresh**: Every 10 cycles, `get_smart_tickers()` re-scores KRW coins by RSI (40pt) + 52-week position (30pt) + volume (20pt) + change rate (10pt). BTC trend checked first — if BTC down >2%, fall back to top-volume tickers.
- **Tickers not in new top list**: Kept until -3% loss (immediate exit) or 3h holding (gradual exit); never re-bought.

### Persistent State

- `data/portfolio.json` — cash, positions (quantity, avg price, buy count, timestamps, peak price), trade stats
- `data/trades_<COIN>.csv` — append-only trade log per coin
- `data/cycle_info.json` — current cycle number, active tickers, last check time (written each cycle for dashboard)
- `logs/bot.log` — all stdout + logging output

The `_sell_cooldowns` dict in `PortfolioManager` is in-memory only (lost on restart).
