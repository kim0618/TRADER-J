"""
Swing v3.0 실제 로직을 87일 데이터에 적용한 백테스트
vs 내가 검증했던 추세 풀백 전략
"""
import pandas as pd
import numpy as np
import os

INITIAL = 1_000_000
FEE = 0.0025
SLIP = 0.002

# ---- v3.0 파라미터 (config.py와 동일) ----
EMA_FAST = 9
EMA_MID = 21
EMA_LONG = 50
RSI_PERIOD = 14
RSI_PB_LOW = 45
RSI_PB_HIGH = 60
MACD_F = 12; MACD_S = 26; MACD_SIG = 9
DONCHIAN = 24
BREAKOUT_BUFFER = 0.003
BREAKOUT_VOL = 2.0
ADX_PERIOD = 14
ADX_BREAKOUT_MIN = 25
VOL_RATIO_MIN = 0.7
BUY_VOL_MIN = 1.0
TRAILING_TRIGGER = 0.07
TRAILING_DROP = 0.03
STOP_LOSS = -0.05
TP_BASE = 0.15
TP_6H = 0.13
TP_12H = 0.12
TP_24H = 0.10
FORCE_SELL_H = 14 * 24
TIME_STOP_H = 7 * 24
TIME_STOP_THR = 0.02
ATR_STOP_MIN = -0.04
ATR_STOP_MAX = -0.08
ATR_STOP_MULT = 3.0


def load_1h(s):
    p = f"data/ohlcv/{s}_1h.csv"
    if not os.path.exists(p): return None
    df = pd.read_csv(p, parse_dates=["time"])
    return df.sort_values("time").reset_index(drop=True)


def aggregate_to_daily(df_1h):
    df = df_1h.set_index("time")
    return df.resample("1D").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna().reset_index()


def add_indicators_1h(df):
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_mid'] = df['close'].ewm(span=EMA_MID, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(RSI_PERIOD).mean()
    df['rsi'] = 100 - (100 / (1 + gain/loss.replace(0, np.nan)))
    df['rsi'] = df['rsi'].fillna(50)

    ef = df['close'].ewm(span=MACD_F, adjust=False).mean()
    es = df['close'].ewm(span=MACD_S, adjust=False).mean()
    df['macd'] = ef - es
    df['macd_sig'] = df['macd'].ewm(span=MACD_SIG, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_sig']

    df['high_n'] = df['high'].rolling(DONCHIAN).max().shift(1)

    df['volume_ma'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma'].replace(0, 1)

    # ATR
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    # ADX
    up = df['high'].diff()
    dn = -df['low'].diff()
    p_dm = up.where((up > dn) & (up > 0), 0)
    m_dm = dn.where((dn > up) & (dn > 0), 0)
    atr_s = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
    p_di = 100 * p_dm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_s.replace(0, 1)
    m_di = 100 * m_dm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_s.replace(0, 1)
    dx = 100 * (p_di - m_di).abs() / (p_di + m_di).replace(0, 1)
    df['adx'] = dx.ewm(span=ADX_PERIOD, adjust=False).mean()
    return df


def add_indicators_1d(df_1d):
    df = df_1d.copy()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    return df


def get_daily_trend(df_1d, ts):
    """ts 시점의 일봉 추세 (UP/DOWN/SIDE)"""
    daily = df_1d[df_1d['time'] <= ts]
    if len(daily) < 51:
        return "SIDE"
    d = daily.iloc[-1]
    if pd.isna(d['ema20']) or pd.isna(d['ema50']):
        return "SIDE"
    if d['close'] > d['ema50'] and d['ema20'] > d['ema50']:
        return "UP"
    if d['close'] < d['ema50'] and d['ema20'] < d['ema50']:
        return "DOWN"
    return "SIDE"


def check_buy_v3(row, prev_row, daily_trend):
    """v3.0 매수 신호"""
    if daily_trend != "UP":
        return None
    if pd.isna(row['rsi']) or pd.isna(row['macd_hist']):
        return None
    if row['volume_ratio'] < VOL_RATIO_MIN:
        return None

    # Donchian 돌파
    if not pd.isna(row['high_n']):
        if (row['close'] > row['high_n'] * (1 + BREAKOUT_BUFFER)
                and row['volume_ratio'] >= BREAKOUT_VOL
                and row['adx'] >= ADX_BREAKOUT_MIN):
            return "BREAKOUT"

    # 풀백
    rsi_rising = row['rsi'] >= prev_row['rsi']
    if (row['ema_fast'] > row['ema_mid']
            and RSI_PB_LOW <= row['rsi'] <= RSI_PB_HIGH
            and rsi_rising
            and row['macd_hist'] > 0
            and row['volume_ratio'] >= BUY_VOL_MIN):
        return "PULLBACK"

    return None


def get_take_profit(hours):
    """보유 시간별 동적 익절"""
    if hours >= 24: return TP_24H
    if hours >= 12: return TP_12H
    if hours >= 6: return TP_6H
    return TP_BASE


def simulate_v3(df_1h, df_1d, symbol=""):
    capital = INITIAL
    pos = None
    trades = []

    for i in range(80, len(df_1h)):
        row = df_1h.iloc[i]
        prev = df_1h.iloc[i-1]
        ts = row['time']

        if pd.isna(row.get('ema_mid')) or pd.isna(row.get('atr')):
            continue

        daily_trend = get_daily_trend(df_1d, ts)
        current = row['close']

        # ---- 매도 체크 ----
        if pos is not None:
            pos['peak'] = max(pos['peak'], row['high'])
            hours = (ts - pos['entry_time']).total_seconds() / 3600
            profit_rate = (current - pos['entry']) / pos['entry']

            exit_price = None
            reason = None

            # 1. 트레일링 스탑
            peak_profit = (pos['peak'] - pos['entry']) / pos['entry']
            drop_from_peak = (current - pos['peak']) / pos['peak']
            if peak_profit >= TRAILING_TRIGGER and drop_from_peak <= -TRAILING_DROP:
                exit_price = current * (1 - SLIP); reason = "TRAIL"

            # 2. 동적 익절
            elif profit_rate >= get_take_profit(hours):
                exit_price = current * (1 - SLIP); reason = "TP"

            # 3. 손절 (ATR or fixed)
            else:
                atr_sl = -ATR_STOP_MULT * pos['atr'] / pos['entry']
                atr_sl = max(ATR_STOP_MAX, min(ATR_STOP_MIN, atr_sl))
                if profit_rate <= atr_sl:
                    exit_price = current * (1 - SLIP); reason = "ATR_SL"
                elif profit_rate <= STOP_LOSS:
                    exit_price = current * (1 - SLIP); reason = "SL"

            # 4. 1D 추세 반전
            if exit_price is None and daily_trend == "DOWN":
                exit_price = current * (1 - SLIP); reason = "TREND_DOWN"

            # 5. 시간 청산
            if exit_price is None:
                if hours >= FORCE_SELL_H:
                    exit_price = current * (1 - SLIP); reason = "FORCED"
                elif hours >= TIME_STOP_H and abs(profit_rate) <= TIME_STOP_THR:
                    exit_price = current * (1 - SLIP); reason = "TIME_STOP"

            if exit_price:
                fee = pos['qty'] * exit_price * FEE
                capital += pos['qty'] * exit_price - fee
                pnl = pos['qty'] * (exit_price - pos['entry']) - fee
                trades.append({
                    "symbol": symbol,
                    "entry_time": pos['entry_time'], "exit_time": ts,
                    "entry": pos['entry'], "exit": exit_price,
                    "pnl": pnl, "reason": reason,
                    "pnl_pct": (exit_price - pos['entry']) / pos['entry'] * 100,
                    "hold_h": hours,
                })
                pos = None
            continue

        # ---- 매수 체크 ----
        sig = check_buy_v3(row, prev, daily_trend)
        if sig:
            entry = current * (1 + SLIP)
            # 25% 배분
            alloc = capital * 0.25
            qty = alloc / entry
            cost = qty * entry
            fee = cost * FEE
            if capital < cost + fee: continue
            capital -= cost + fee
            pos = {
                "entry": entry, "qty": qty, "entry_time": ts,
                "atr": row['atr'], "peak": row['high'],
                "signal": sig,
            }

    if pos:
        last = df_1h.iloc[-1]
        exit_p = last['close'] * (1 - SLIP)
        fee = pos['qty'] * exit_p * FEE
        capital += pos['qty'] * exit_p - fee
        pnl = pos['qty'] * (exit_p - pos['entry']) - fee
        hours = (last['time'] - pos['entry_time']).total_seconds() / 3600
        trades.append({
            "symbol": symbol, "entry_time": pos['entry_time'], "exit_time": last['time'],
            "entry": pos['entry'], "exit": exit_p, "pnl": pnl, "reason": "FORCED_END",
            "pnl_pct": (exit_p - pos['entry']) / pos['entry'] * 100, "hold_h": hours,
        })

    return capital, trades


def summarize(trades):
    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total = sum(t['pnl'] for t in trades)
    win_p = sum(t['pnl'] for t in wins)
    loss_p = abs(sum(t['pnl'] for t in losses))
    pf = win_p / loss_p if loss_p > 0 else 99
    reasons = {}
    for t in trades:
        reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
    return {
        "n": n, "wr": len(wins)/n*100, "pf": pf, "total": total,
        "aw": np.mean([t['pnl_pct'] for t in wins]) if wins else 0,
        "al": np.mean([t['pnl_pct'] for t in losses]) if losses else 0,
        "avg_hold": np.mean([t['hold_h'] for t in trades]),
        "reasons": reasons,
    }


def run_v3(symbols):
    print(f"{'='*88}")
    print(f"  📊 v3.0 백테스트 (87일, 1H 봉)")
    print(f"  Donchian 24봉 돌파 + 풀백 하이브리드 + 1D 추세 필터")
    print(f"{'='*88}\n")

    all_trades = []
    for sym in symbols:
        df_1h = load_1h(sym)
        if df_1h is None or len(df_1h) < 200:
            continue
        df_1d = aggregate_to_daily(df_1h)
        df_1d = add_indicators_1d(df_1d)
        df_1h = add_indicators_1h(df_1h)

        _, trades = simulate_v3(df_1h, df_1d, sym)
        all_trades.extend(trades)
        s = summarize(trades)
        if s:
            print(f"  ▶ {sym:5}: {s['n']:3}건 | 승률 {s['wr']:>3.0f}% | PF {s['pf']:>5.2f} | "
                  f"손익 {s['total']:>+9,.0f} | 평균보유 {s['avg_hold']:>5.1f}h "
                  f"({s['avg_hold']/24:.1f}d)")
        else:
            print(f"  ▶ {sym:5}: 거래 0건")

    print(f"\n{'='*88}")
    print(f"  📊 전체 결과")
    print(f"{'='*88}")
    o = summarize(all_trades)
    if o:
        ret = o['total'] / INITIAL * 100
        annual = ret * 4  # 87일→1년 환산
        print(f"  총 거래수:       {o['n']}건 (월 {o['n']/3:.1f}건)")
        print(f"  승/패:           {len([t for t in all_trades if t['pnl']>0])}승 "
              f"{len([t for t in all_trades if t['pnl']<=0])}패")
        print(f"  승률:            {o['wr']:.1f}%")
        print(f"  Profit Factor:   {o['pf']:.2f}")
        print(f"  총 손익:         {o['total']:+,.0f}원 ({ret:+.2f}%)")
        print(f"  평균 익절:       {o['aw']:+.2f}%")
        print(f"  평균 손절:       {o['al']:+.2f}%")
        print(f"  평균 보유:       {o['avg_hold']:.1f}시간 ({o['avg_hold']/24:.1f}일)")
        print(f"  연환산 수익률:    {annual:+.1f}%")
        print(f"  청산 사유:       {o['reasons']}")
    return all_trades, o


if __name__ == "__main__":
    symbols = ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "BCH", "AVAX", "DOT"]
    print(f"종목: {symbols}\n")
    run_v3(symbols)
