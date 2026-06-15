"""
추세 풀백 전략 파라미터 최적화
"""
import pandas as pd
import numpy as np
import os
from itertools import product

INITIAL_CAPITAL = 1_000_000
FEE_RATE = 0.0025
SLIPPAGE = 0.002
RISK_PER_TRADE = 0.01


def load_1h(symbol):
    p = f"data/ohlcv/{symbol}_1h.csv"
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, parse_dates=["time"])
    return df.sort_values("time").reset_index(drop=True)


def resample(df, freq):
    df = df.set_index("time")
    return df.resample(freq).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna().reset_index()


def add_indicators(df, ema_fast=20, ema_mid=50, ema_slow=100,
                    atr_period=14, rsi_period=14, adx_period=14):
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=ema_fast, adjust=False).mean()
    df["ema_mid"] = df["close"].ewm(span=ema_mid, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=ema_slow, adjust=False).mean()

    h_l = df["high"] - df["low"]
    h_c = (df["high"] - df["close"].shift()).abs()
    l_c = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([h_l, h_c, l_c], axis=1).max(axis=1)
    df["atr"] = tr.rolling(atr_period).mean()

    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))

    up = df["high"].diff()
    dn = -df["low"].diff()
    p_dm = np.where((up > dn) & (up > 0), up, 0)
    m_dm = np.where((dn > up) & (dn > 0), dn, 0)
    tr_s = tr.rolling(adx_period).mean()
    p_di = 100 * pd.Series(p_dm).rolling(adx_period).mean() / tr_s
    m_di = 100 * pd.Series(m_dm).rolling(adx_period).mean() / tr_s
    df["adx"] = (100 * (p_di - m_di).abs() / (p_di + m_di)).rolling(adx_period).mean()

    df["vol_avg"] = df["volume"].rolling(20).mean()
    return df


def simulate(df, params):
    """추세 풀백 전략 시뮬레이션 with 파라미터화"""
    capital = INITIAL_CAPITAL
    position = None
    trades = []

    stop_atr = params["stop_atr"]
    take_profit = params["take_profit"]
    use_partial = params["use_partial"]
    trail_lookback = params["trail_lookback"]
    adx_min = params["adx_min"]
    volume_min = params["volume_min"]
    touch_tolerance = params["touch_tolerance"]

    for i in range(120, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        if pd.isna(row.get("adx")) or pd.isna(row.get("ema_slow")) or pd.isna(row.get("atr")):
            continue

        if position is not None:
            current = row["close"]
            position["highest"] = max(position["highest"], row["high"])

            if use_partial and not position["partial_taken"]:
                if current >= position["entry"] * (1 + take_profit):
                    sell_price = position["entry"] * (1 + take_profit) * (1 - SLIPPAGE)
                    sell_qty = position["qty"] * 0.5
                    fee = sell_qty * sell_price * FEE_RATE
                    capital += sell_qty * sell_price - fee
                    position["partial_pnl"] = sell_qty * (sell_price - position["entry"]) - fee
                    position["qty"] -= sell_qty
                    position["partial_taken"] = True
                    continue

            recent_low = df["low"].iloc[max(0, i-trail_lookback):i].min()
            sl_atr = position["entry"] - stop_atr * position["atr"]

            exit_price, reason = None, None
            if current <= sl_atr:
                exit_price = sl_atr * (1 - SLIPPAGE); reason = "ATR_SL"
            elif position["partial_taken"] and current <= recent_low:
                exit_price = recent_low * (1 - SLIPPAGE); reason = "TRAIL"
            elif row["close"] < row["ema_mid"]:
                exit_price = row["close"] * (1 - SLIPPAGE); reason = "EMA_EXIT"

            if exit_price:
                fee = position["qty"] * exit_price * FEE_RATE
                capital += position["qty"] * exit_price - fee
                final_pnl = position["qty"] * (exit_price - position["entry"]) - fee + position.get("partial_pnl", 0)
                trades.append({
                    "entry": position["entry"], "exit": exit_price,
                    "pnl": final_pnl, "reason": reason,
                    "hold_h": (row["time"] - position["entry_time"]).total_seconds() / 3600,
                    "pnl_pct": (exit_price - position["entry"]) / position["entry"] * 100,
                })
                position = None
            continue

        # 추세 풀백 진입 신호
        if not (row["ema_fast"] > row["ema_mid"] > row["ema_slow"]):
            continue
        # 직전봉이 EMA20에 근접/터치
        if prev["low"] > prev["ema_fast"] * (1 + touch_tolerance):
            continue
        # 현재봉이 반등 (양봉)
        if row["close"] <= row["open"]:
            continue
        if row["close"] < row["ema_fast"]:
            continue
        # ADX 추세 강도
        if row["adx"] < adx_min:
            continue
        # 거래량
        if pd.isna(row["vol_avg"]) or row["volume"] < row["vol_avg"] * volume_min:
            continue

        entry = row["close"] * (1 + SLIPPAGE)
        risk = capital * RISK_PER_TRADE
        stop_d = stop_atr * row["atr"]
        if stop_d <= 0:
            continue
        qty = risk / stop_d
        cost = qty * entry
        if cost > capital * 0.95:
            qty = (capital * 0.95) / entry
            cost = qty * entry
        fee = cost * FEE_RATE
        capital -= cost + fee
        position = {
            "entry": entry, "qty": qty, "entry_time": row["time"],
            "atr": row["atr"], "highest": row["high"],
            "partial_taken": False, "partial_pnl": 0,
        }

    if position is not None:
        last = df.iloc[-1]
        exit_p = last["close"] * (1 - SLIPPAGE)
        fee = position["qty"] * exit_p * FEE_RATE
        capital += position["qty"] * exit_p - fee
        pnl = position["qty"] * (exit_p - position["entry"]) - fee + position.get("partial_pnl", 0)
        trades.append({
            "entry": position["entry"], "exit": exit_p,
            "pnl": pnl, "reason": "FORCED",
            "hold_h": (last["time"] - position["entry_time"]).total_seconds() / 3600,
            "pnl_pct": (exit_p - position["entry"]) / position["entry"] * 100,
        })

    return capital, trades


def evaluate(trades):
    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = sum(t["pnl"] for t in trades)
    win_p = sum(t["pnl"] for t in wins)
    loss_p = abs(sum(t["pnl"] for t in losses))
    pf = win_p / loss_p if loss_p > 0 else 99
    return {
        "n": n, "wr": len(wins)/n*100, "pf": pf, "total": total,
        "aw": np.mean([t["pnl_pct"] for t in wins]) if wins else 0,
        "al": np.mean([t["pnl_pct"] for t in losses]) if losses else 0,
        "avg_hold": np.mean([t["hold_h"] for t in trades]),
    }


def run_grid(symbols):
    """파라미터 그리드 서치"""
    grid = {
        "stop_atr": [1.5, 2.0, 2.5, 3.0],
        "take_profit": [0.04, 0.06, 0.08, 0.10],
        "use_partial": [True, False],
        "trail_lookback": [10],
        "adx_min": [15, 20, 25],
        "volume_min": [1.0, 1.5, 2.0],
        "touch_tolerance": [0.005, 0.01, 0.02],
    }

    keys = list(grid.keys())
    combos = list(product(*[grid[k] for k in keys]))
    print(f"총 {len(combos)}개 조합 테스트 중...")

    best = []
    for combo in combos:
        params = dict(zip(keys, combo))
        all_trades = []
        for sym in symbols:
            df_1h = load_1h(sym)
            if df_1h is None or len(df_1h) < 200:
                continue
            df = resample(df_1h, "4h")
            df = add_indicators(df)
            _, trades = simulate(df, params)
            all_trades.extend(trades)

        e = evaluate(all_trades)
        if e and e["n"] >= 10:  # 최소 10건
            best.append((params, e))

    # PF로 정렬, 동률은 총손익으로
    best.sort(key=lambda x: (x[1]["pf"], x[1]["total"]), reverse=True)

    print(f"\n{'='*88}")
    print(f"  📊 최적 파라미터 상위 10개")
    print(f"{'='*88}")
    print(f"  {'PF':>5} | {'손익':>11} | {'거래':>3} | {'승률':>4} | {'익절':>5}/{'손절':>5} | 파라미터")
    print("  " + "-"*120)
    for params, e in best[:10]:
        param_str = f"SL{params['stop_atr']}× TP{params['take_profit']*100:.0f}% "
        param_str += f"ADX{params['adx_min']} V{params['volume_min']}x TT{params['touch_tolerance']:.3f}"
        if not params["use_partial"]: param_str += " noPartial"
        print(f"  {e['pf']:>5.2f} | {e['total']:>+10,.0f} | {e['n']:>3} | {e['wr']:>3.0f}% | "
              f"{e['aw']:>+4.1f}%/{e['al']:>+4.1f}% | {param_str}")

    return best


if __name__ == "__main__":
    symbols = ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "BCH", "AVAX", "DOT"]
    print(f"종목: {symbols}")
    run_grid(symbols)
