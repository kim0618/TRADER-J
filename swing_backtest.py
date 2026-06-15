"""
스윙 트레이딩 전략 백테스트
- 4시간봉 기준 (1시간봉 4개 resample)
- 진입: EMA100 위 + ADX>25 + 20봉 신고가 돌파 + 거래량 1.5x + RSI 50-75
- 청산: ATR x 2 손절 / +5% 1차익절 50% / 트레일링 10봉 저점
"""
import pandas as pd
import numpy as np
import os
import json
from pathlib import Path

# 파라미터
INITIAL_CAPITAL = 1_000_000
FEE_RATE = 0.0025
SLIPPAGE = 0.002
RISK_PER_TRADE = 0.01  # 자본 1% 리스크

ATR_PERIOD = 14
ADX_PERIOD = 14
EMA_TREND = 100   # 4h EMA100 (약 17일, 200일 데이터 부족해서 단축)
DONCHIAN_PERIOD = 20
VOLUME_AVG = 20
VOLUME_RATIO = 1.5
RSI_PERIOD = 14
RSI_MIN = 50
RSI_MAX = 75
ADX_MIN = 25

STOP_ATR_MULTIPLE = 2.0
TAKE_PROFIT_1 = 0.05
PARTIAL_SELL_RATIO = 0.5
TRAIL_LOOKBACK = 10


def load_1h(symbol):
    p = f"data/ohlcv/{symbol}_1h.csv"
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)
    return df


def resample_to_4h(df_1h):
    df = df_1h.set_index("time")
    df_4h = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna().reset_index()
    return df_4h


def compute_indicators(df):
    df = df.copy()
    # EMA
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # ADX (간략화)
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0)
    tr_smooth = tr.rolling(ADX_PERIOD).mean()
    plus_di = 100 * pd.Series(plus_dm).rolling(ADX_PERIOD).mean() / tr_smooth
    minus_di = 100 * pd.Series(minus_dm).rolling(ADX_PERIOD).mean() / tr_smooth
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    df["adx"] = dx.rolling(ADX_PERIOD).mean()

    # 돈치안 상단 (직전 N봉 신고가)
    df["donchian_high"] = df["high"].shift(1).rolling(DONCHIAN_PERIOD).max()
    df["donchian_low"] = df["low"].shift(1).rolling(DONCHIAN_PERIOD).min()

    # 거래량 평균
    df["vol_avg"] = df["volume"].rolling(VOLUME_AVG).mean()

    return df


def backtest_swing(df, symbol="?"):
    """돈치안 돌파 + 다중 필터 스윙 전략"""
    capital = INITIAL_CAPITAL
    position = None
    trades = []

    for i in range(max(EMA_TREND, ADX_PERIOD * 2, DONCHIAN_PERIOD) + 5, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # NaN 체크
        if pd.isna(row["adx"]) or pd.isna(row["ema_trend"]) or pd.isna(row["donchian_high"]):
            continue

        # 포지션 보유 중
        if position is not None:
            current_price = row["close"]
            highest = max(position["highest"], row["high"])
            position["highest"] = highest

            # 1차 익절 (+5%) - 절반 매도
            if not position["partial_taken"] and current_price >= position["entry"] * (1 + TAKE_PROFIT_1):
                sell_price = position["entry"] * (1 + TAKE_PROFIT_1) * (1 - SLIPPAGE)
                sell_qty = position["qty"] * PARTIAL_SELL_RATIO
                pnl = sell_qty * (sell_price - position["entry"])
                fee = sell_qty * sell_price * FEE_RATE
                capital += sell_qty * sell_price - fee
                position["qty"] -= sell_qty
                position["partial_taken"] = True
                position["partial_pnl"] = pnl - fee
                continue

            # 트레일링 스탑 (10봉 저점 이탈)
            recent_low = df["low"].iloc[max(0, i - TRAIL_LOOKBACK):i].min()

            # 손절 조건들
            sl_atr = position["entry"] - STOP_ATR_MULTIPLE * position["atr"]
            sl_trail = recent_low
            sl_ema = row["ema_trend"]

            exit_price = None
            exit_reason = None

            if current_price <= sl_atr:
                exit_price = sl_atr * (1 - SLIPPAGE)
                exit_reason = "ATR_SL"
            elif position["partial_taken"] and current_price <= sl_trail:
                exit_price = sl_trail * (1 - SLIPPAGE)
                exit_reason = "TRAIL"
            elif row["close"] < row["ema_trend"] and prev["close"] < prev["ema_trend"]:
                # 2봉 연속 EMA 이탈
                exit_price = row["close"] * (1 - SLIPPAGE)
                exit_reason = "EMA_BREAK"

            if exit_price:
                fee = position["qty"] * exit_price * FEE_RATE
                proceeds = position["qty"] * exit_price - fee
                pnl_remain = position["qty"] * (exit_price - position["entry"]) - fee
                capital += proceeds
                total_pnl = pnl_remain + position.get("partial_pnl", 0)
                trades.append({
                    "symbol": symbol,
                    "entry_time": position["entry_time"],
                    "exit_time": row["time"],
                    "entry": position["entry"],
                    "exit": exit_price,
                    "qty": position["original_qty"],
                    "pnl": total_pnl,
                    "pnl_pct": (exit_price - position["entry"]) / position["entry"] * 100,
                    "reason": exit_reason,
                    "hold_hours": (row["time"] - position["entry_time"]).total_seconds() / 3600,
                })
                position = None
            continue

        # 진입 시그널 체크 (4단계 필터)
        # 1. 추세 필터: EMA100 위
        if row["close"] < row["ema_trend"]:
            continue
        # 2. ADX > 25
        if row["adx"] < ADX_MIN:
            continue
        # 3. 돈치안 상단 돌파
        if row["close"] <= row["donchian_high"]:
            continue
        # 4. 거래량 1.5x
        if pd.isna(row["vol_avg"]) or row["volume"] < row["vol_avg"] * VOLUME_RATIO:
            continue
        # 5. RSI 50-75
        if not (RSI_MIN < row["rsi"] < RSI_MAX):
            continue

        # 진입!
        entry_price = row["close"] * (1 + SLIPPAGE)
        risk_amount = capital * RISK_PER_TRADE
        stop_distance = STOP_ATR_MULTIPLE * row["atr"]
        if stop_distance <= 0:
            continue
        qty = risk_amount / stop_distance
        cost = qty * entry_price
        if cost > capital * 0.95:
            qty = (capital * 0.95) / entry_price
            cost = qty * entry_price
        fee = cost * FEE_RATE
        capital -= (cost + fee)

        position = {
            "entry": entry_price,
            "entry_time": row["time"],
            "qty": qty,
            "original_qty": qty,
            "atr": row["atr"],
            "highest": row["high"],
            "partial_taken": False,
            "partial_pnl": 0,
        }

    # 마지막 포지션 청산
    if position is not None:
        last = df.iloc[-1]
        exit_price = last["close"] * (1 - SLIPPAGE)
        fee = position["qty"] * exit_price * FEE_RATE
        capital += position["qty"] * exit_price - fee
        total_pnl = position["qty"] * (exit_price - position["entry"]) - fee + position.get("partial_pnl", 0)
        trades.append({
            "symbol": symbol,
            "entry_time": position["entry_time"],
            "exit_time": last["time"],
            "entry": position["entry"],
            "exit": exit_price,
            "qty": position["original_qty"],
            "pnl": total_pnl,
            "pnl_pct": (exit_price - position["entry"]) / position["entry"] * 100,
            "reason": "FORCED",
            "hold_hours": (last["time"] - position["entry_time"]).total_seconds() / 3600,
        })

    return capital, trades


def summarize(trades):
    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    win_pnl = sum(t["pnl"] for t in wins)
    loss_pnl = abs(sum(t["pnl"] for t in losses))
    pf = win_pnl / loss_pnl if loss_pnl > 0 else float("inf")
    avg_win = win_pnl / len(wins) if wins else 0
    avg_loss = loss_pnl / len(losses) if losses else 0
    avg_hold = sum(t["hold_hours"] for t in trades) / n
    win_pct = [t["pnl_pct"] for t in wins]
    loss_pct = [t["pnl_pct"] for t in losses]
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n * 100,
        "total_pnl": total_pnl,
        "pf": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_win_pct": np.mean(win_pct) if win_pct else 0,
        "avg_loss_pct": np.mean(loss_pct) if loss_pct else 0,
        "avg_hold_hours": avg_hold,
    }


def run(symbols):
    print(f"{'='*70}")
    print(f"  스윙 트레이딩 백테스트 (4시간봉)")
    print(f"  종목: {symbols}")
    print(f"{'='*70}\n")

    all_trades = []
    for sym in symbols:
        df_1h = load_1h(sym)
        if df_1h is None or len(df_1h) < 200:
            print(f"  ⚠️ {sym}: 데이터 부족")
            continue
        df_4h = resample_to_4h(df_1h)
        df_4h = compute_indicators(df_4h)
        final, trades = backtest_swing(df_4h, sym)
        all_trades.extend(trades)
        s = summarize(trades)
        if s:
            print(f"  ▶ {sym}: {s['trades']}건 | 승률 {s['win_rate']:.0f}% | PF {s['pf']:.2f} | "
                  f"손익 {s['total_pnl']:+,.0f}원 | 평균보유 {s['avg_hold_hours']:.1f}h")
        else:
            print(f"  ▶ {sym}: 거래 0건 (신호 없음)")

    print(f"\n{'='*70}")
    print(f"  📊 전체 결과")
    print(f"{'='*70}")
    overall = summarize(all_trades)
    if overall:
        print(f"  총 거래수:       {overall['trades']}건")
        print(f"  승/패:           {overall['wins']}승 {overall['losses']}패")
        print(f"  승률:            {overall['win_rate']:.1f}%")
        print(f"  Profit Factor:   {overall['pf']:.2f}")
        print(f"  총 손익:         {overall['total_pnl']:+,.0f}원")
        print(f"  평균 익절:       {overall['avg_win_pct']:+.2f}% ({overall['avg_win']:+,.0f}원)")
        print(f"  평균 손절:       {overall['avg_loss_pct']:+.2f}% ({overall['avg_loss']:+,.0f}원)")
        print(f"  평균 보유:       {overall['avg_hold_hours']:.1f}시간 ({overall['avg_hold_hours']/24:.1f}일)")
        print(f"  자본 수익률:     {overall['total_pnl']/INITIAL_CAPITAL*100:+.2f}%")

    return all_trades, overall


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        # 메이저 + 검증된 알트 일부
        symbols = ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "BCH", "LTC", "LINK"]
    run(symbols)
