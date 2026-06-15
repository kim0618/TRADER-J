"""
여러 스윙 전략 변형 비교 테스트
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


def add_indicators(df, ema_period=100, atr_period=14, rsi_period=14, adx_period=14, donchian=20):
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_mid"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=ema_period, adjust=False).mean()

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

    df["donchian_high"] = df["high"].shift(1).rolling(donchian).max()
    df["donchian_low"] = df["low"].shift(1).rolling(donchian).min()
    df["vol_avg"] = df["volume"].rolling(20).mean()
    # 볼린저
    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    return df


def simulate(df, signal_fn, name="", **kwargs):
    """공통 시뮬레이션 엔진"""
    capital = INITIAL_CAPITAL
    position = None
    trades = []
    stop_atr = kwargs.get("stop_atr", 2.0)
    take_profit = kwargs.get("take_profit", 0.05)  # +5% 절반 청산
    use_partial = kwargs.get("use_partial", True)
    use_trail = kwargs.get("use_trail", True)
    trail_lookback = kwargs.get("trail_lookback", 10)

    for i in range(120, len(df)):
        row = df.iloc[i]
        if pd.isna(row.get("adx")) or pd.isna(row.get("ema_slow")) or pd.isna(row.get("atr")):
            continue

        if position is not None:
            current = row["close"]
            position["highest"] = max(position["highest"], row["high"])

            # 1차 부분 익절
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
            elif use_trail and position["partial_taken"] and current <= recent_low:
                exit_price = recent_low * (1 - SLIPPAGE); reason = "TRAIL"
            elif row["close"] < row["ema_slow"] and df.iloc[i-1]["close"] < df.iloc[i-1]["ema_slow"]:
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

        if signal_fn(df, i):
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


# ─── 전략 변형들 ─────────────────────────────────────────
def sig_basic(df, i):
    """기본 돈치안 돌파"""
    r = df.iloc[i]
    return (r["close"] > r["ema_slow"]
            and r["adx"] > 25
            and r["close"] > r["donchian_high"]
            and r["volume"] > r["vol_avg"] * 1.5
            and 50 < r["rsi"] < 75)


def sig_strict(df, i):
    """엄격: ADX>30, 거래량 2x, EMA 정렬"""
    r = df.iloc[i]
    return (r["ema_fast"] > r["ema_mid"] > r["ema_slow"]
            and r["adx"] > 30
            and r["close"] > r["donchian_high"]
            and r["volume"] > r["vol_avg"] * 2.0
            and 55 < r["rsi"] < 70)


def sig_pullback(df, i):
    """추세 풀백: 상승 추세 중 EMA20 터치 후 반등"""
    r = df.iloc[i]
    prev = df.iloc[i-1]
    if not (r["ema_fast"] > r["ema_mid"] > r["ema_slow"]):
        return False
    # 직전 봉이 EMA20 근접 or 터치 (저점이 EMA20 아래로 1% 이내)
    touched = prev["low"] < prev["ema_fast"] * 1.01
    # 현재 봉이 반등 (양봉 + 종가가 EMA20 위)
    bounced = r["close"] > r["open"] and r["close"] > r["ema_fast"]
    return touched and bounced and r["adx"] > 20


def sig_ma_cross(df, i):
    """단순 골든크로스: EMA20이 EMA50 상향 돌파"""
    r = df.iloc[i]
    prev = df.iloc[i-1]
    cross = prev["ema_fast"] <= prev["ema_mid"] and r["ema_fast"] > r["ema_mid"]
    return cross and r["close"] > r["ema_slow"] and r["adx"] > 20


def sig_bb_squeeze(df, i):
    """볼린저 스퀴즈 + 돌파"""
    r = df.iloc[i]
    bb_width = (r["bb_upper"] - r["bb_lower"]) / r["bb_mid"]
    # 좁아진 BB 폭이 확장되며 상단 돌파
    return (bb_width < 0.05
            and r["close"] > r["bb_upper"]
            and r["close"] > r["ema_slow"]
            and r["volume"] > r["vol_avg"] * 1.5)


def sig_longer_breakout(df, i):
    """50봉 신고가 (Turtle System 2)"""
    r = df.iloc[i]
    high_50 = df["high"].iloc[max(0, i-50):i].max()
    return (r["close"] > high_50
            and r["close"] > r["ema_slow"]
            and r["adx"] > 25
            and r["volume"] > r["vol_avg"] * 1.5)


def summarize(trades, label):
    if not trades:
        return f"  {label:25} | 거래 0건"
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = sum(t["pnl"] for t in trades)
    win_p = sum(t["pnl"] for t in wins)
    loss_p = abs(sum(t["pnl"] for t in losses))
    pf = win_p / loss_p if loss_p > 0 else 99
    wr = len(wins) / n * 100
    aw = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    al = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
    return (f"  {label:25} | {n:3}건 | 승률{wr:>4.0f}% | PF{pf:>5.2f} | "
            f"손익{total:>+10,.0f} | 익절{aw:>+5.1f}% 손절{al:>+5.1f}%")


def run_all(symbols):
    strategies = {
        "기본 돈치안 돌파": (sig_basic, {"use_partial": True}),
        "엄격 (ADX30+볼륨2x)": (sig_strict, {"use_partial": True}),
        "추세 풀백": (sig_pullback, {"use_partial": True}),
        "단순 골든크로스": (sig_ma_cross, {"use_partial": True}),
        "BB 스퀴즈 돌파": (sig_bb_squeeze, {"use_partial": True}),
        "Turtle 50봉 신고가": (sig_longer_breakout, {"use_partial": True}),
        "기본 (절반청산 X)": (sig_basic, {"use_partial": False}),
        "Turtle (절반청산 X)": (sig_longer_breakout, {"use_partial": False}),
    }

    for tf_name, freq in [("4시간봉", "4h"), ("일봉", "1D")]:
        print(f"\n{'='*88}")
        print(f"  📊 {tf_name} 백테스트 (87일)")
        print(f"{'='*88}")
        all_results = {name: [] for name in strategies.keys()}

        for sym in symbols:
            df_1h = load_1h(sym)
            if df_1h is None or len(df_1h) < 200:
                continue
            df = resample(df_1h, freq)
            if len(df) < 130:
                continue
            df = add_indicators(df)

            for name, (fn, kwargs) in strategies.items():
                _, trades = simulate(df, fn, name=name, **kwargs)
                for t in trades:
                    t["symbol"] = sym
                all_results[name].extend(trades)

        print(f"\n  {'전략':25} | {'거래':>3} | {'승률':>5} | {'PF':>5} | {'손익':>11} | 익절/손절")
        print("  " + "-"*88)
        for name, trades in all_results.items():
            print(summarize(trades, name))


if __name__ == "__main__":
    # 메이저 + 검증된 알트
    symbols = ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "BCH", "AVAX", "DOT"]
    print("종목:", symbols)
    run_all(symbols)
