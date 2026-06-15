"""
종합 백테스트: 모든 전략 변형 + 다양한 파라미터 + buy-and-hold 비교
"""
import pandas as pd
import numpy as np
import os
from itertools import product
import warnings
warnings.filterwarnings("ignore")

INITIAL = 1_000_000
FEE = 0.0025
SLIP = 0.002


def load_1h(s):
    p = f"data/ohlcv/{s}_1h.csv"
    if not os.path.exists(p): return None
    df = pd.read_csv(p, parse_dates=["time"])
    return df.sort_values("time").reset_index(drop=True)


def resample(df, freq):
    df = df.set_index("time")
    return df.resample(freq).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna().reset_index()


def add_inds(df, ef=20, em=50, el=100):
    df = df.copy()
    df['ema_f'] = df['close'].ewm(span=ef, adjust=False).mean()
    df['ema_m'] = df['close'].ewm(span=em, adjust=False).mean()
    df['ema_l'] = df['close'].ewm(span=el, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    df['rsi'] = df['rsi'].fillna(50)

    macd_f = df['close'].ewm(span=12, adjust=False).mean()
    macd_s = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = macd_f - macd_s
    df['macd_sig'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_h'] = df['macd'] - df['macd_sig']

    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    df['donch_h'] = df['high'].rolling(20).max().shift(1)
    df['donch_l'] = df['low'].rolling(20).min().shift(1)
    df['vol_avg'] = df['volume'].rolling(20).mean()
    df['vol_r'] = df['volume'] / df['vol_avg'].replace(0, 1)

    df['bb_m'] = df['close'].rolling(20).mean()
    bb_s = df['close'].rolling(20).std()
    df['bb_u'] = df['bb_m'] + 2 * bb_s
    df['bb_l'] = df['bb_m'] - 2 * bb_s

    up = df['high'].diff()
    dn = -df['low'].diff()
    pdm = up.where((up > dn) & (up > 0), 0)
    mdm = dn.where((dn > up) & (dn > 0), 0)
    atr_s = tr.ewm(span=14, adjust=False).mean()
    pdi = 100 * pdm.ewm(span=14, adjust=False).mean() / atr_s.replace(0, 1)
    mdi = 100 * mdm.ewm(span=14, adjust=False).mean() / atr_s.replace(0, 1)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1)
    df['adx'] = dx.ewm(span=14, adjust=False).mean()
    return df


# ━━━ 다양한 진입 신호 ━━━
def sig_pullback_strict(df, i):
    """EMA 3개 정배열 + 풀백"""
    r = df.iloc[i]
    p = df.iloc[i-1]
    if not (r['ema_f'] > r['ema_m'] > r['ema_l']):
        return False
    touched = p['low'] < p['ema_f'] * 1.01
    bounced = r['close'] > r['open'] and r['close'] > r['ema_f']
    return touched and bounced and r['adx'] > 20


def sig_pullback_loose(df, i):
    """EMA 2개 정배열"""
    r = df.iloc[i]
    p = df.iloc[i-1]
    if not (r['ema_f'] > r['ema_m']):
        return False
    touched = p['low'] < p['ema_f'] * 1.015
    bounced = r['close'] > r['open'] and r['close'] > r['ema_f']
    return touched and bounced and r['adx'] > 15


def sig_breakout_donch(df, i):
    """돈치안 20봉 돌파"""
    r = df.iloc[i]
    return (r['close'] > r['donch_h']
            and r['close'] > r['ema_l']
            and r['adx'] > 25
            and r['vol_r'] > 1.5)


def sig_macd_cross(df, i):
    """MACD 양수 전환 + EMA 정배열"""
    r = df.iloc[i]
    p = df.iloc[i-1]
    return (p['macd_h'] <= 0 and r['macd_h'] > 0
            and r['ema_f'] > r['ema_m']
            and r['rsi'] > 50)


def sig_momentum(df, i):
    """RSI > 50 상향 돌파 + 상승 추세"""
    r = df.iloc[i]
    p = df.iloc[i-1]
    return (p['rsi'] < 50 and r['rsi'] > 55
            and r['ema_f'] > r['ema_m'] > r['ema_l']
            and r['vol_r'] > 1.2)


def sig_ema_bounce(df, i):
    """EMA50 터치 후 반등 (큰 풀백)"""
    r = df.iloc[i]
    p = df.iloc[i-1]
    if not (r['ema_f'] > r['ema_m'] > r['ema_l']):
        return False
    touched_50 = p['low'] < p['ema_m'] * 1.005
    bounced = r['close'] > p['close'] and r['close'] > r['ema_m']
    return touched_50 and bounced and r['rsi'] > 40


# ━━━ 시뮬레이션 엔진 (모듈러) ━━━
def simulate(df, signal_fn, stop_atr=1.5, take_profit=0.05,
             use_partial=True, trail_lookback=10, use_atr_trail=False, atr_trail_mult=3.0):
    capital = INITIAL
    pos = None
    trades = []

    for i in range(120, len(df)):
        r = df.iloc[i]
        if pd.isna(r.get('adx')) or pd.isna(r.get('atr')):
            continue

        if pos is not None:
            current = r['close']
            pos['peak'] = max(pos['peak'], r['high'])

            # 부분 익절
            if use_partial and not pos['partial']:
                if current >= pos['entry'] * (1 + take_profit):
                    sp = pos['entry'] * (1 + take_profit) * (1 - SLIP)
                    sq = pos['qty'] * 0.5
                    fee = sq * sp * FEE
                    capital += sq * sp - fee
                    pos['partial_pnl'] = sq * (sp - pos['entry']) - fee
                    pos['qty'] -= sq
                    pos['partial'] = True
                    continue

            # 손절/익절
            sl_price = pos['entry'] - stop_atr * pos['atr']
            recent_low = df['low'].iloc[max(0, i-trail_lookback):i].min()
            if use_atr_trail:
                atr_trail = pos['peak'] - atr_trail_mult * pos['atr']
                recent_low = max(recent_low, atr_trail)

            exit_p, reason = None, None
            if current <= sl_price:
                exit_p = sl_price * (1 - SLIP); reason = "ATR_SL"
            elif pos['partial'] and current <= recent_low:
                exit_p = recent_low * (1 - SLIP); reason = "TRAIL"
            elif r['close'] < r['ema_m']:  # EMA50 이탈
                exit_p = r['close'] * (1 - SLIP); reason = "EMA_EXIT"

            if exit_p:
                fee = pos['qty'] * exit_p * FEE
                capital += pos['qty'] * exit_p - fee
                pnl = pos['qty'] * (exit_p - pos['entry']) - fee + pos.get('partial_pnl', 0)
                trades.append({
                    'entry': pos['entry'], 'exit': exit_p, 'pnl': pnl, 'reason': reason,
                    'pnl_pct': (exit_p - pos['entry']) / pos['entry'] * 100,
                    'hold_h': (r['time'] - pos['entry_time']).total_seconds() / 3600,
                })
                pos = None
            continue

        if signal_fn(df, i):
            entry = r['close'] * (1 + SLIP)
            risk = capital * 0.01
            stop_d = stop_atr * r['atr']
            if stop_d <= 0: continue
            qty = risk / stop_d
            cost = qty * entry
            if cost > capital * 0.95:
                qty = (capital * 0.95) / entry
                cost = qty * entry
            fee = cost * FEE
            capital -= cost + fee
            pos = {
                'entry': entry, 'qty': qty, 'entry_time': r['time'],
                'atr': r['atr'], 'peak': r['high'],
                'partial': False, 'partial_pnl': 0,
            }

    if pos:
        last = df.iloc[-1]
        exit_p = last['close'] * (1 - SLIP)
        fee = pos['qty'] * exit_p * FEE
        capital += pos['qty'] * exit_p - fee
        pnl = pos['qty'] * (exit_p - pos['entry']) - fee + pos.get('partial_pnl', 0)
        trades.append({
            'entry': pos['entry'], 'exit': exit_p, 'pnl': pnl, 'reason': 'FORCED',
            'pnl_pct': (exit_p - pos['entry']) / pos['entry'] * 100,
            'hold_h': (last['time'] - pos['entry_time']).total_seconds() / 3600,
        })

    return capital, trades


def evaluate(trades):
    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total = sum(t['pnl'] for t in trades)
    win_p = sum(t['pnl'] for t in wins)
    loss_p = abs(sum(t['pnl'] for t in losses))
    pf = win_p / loss_p if loss_p > 0 else 99
    return {
        'n': n, 'wr': len(wins)/n*100, 'pf': pf, 'total': total,
        'aw': np.mean([t['pnl_pct'] for t in wins]) if wins else 0,
        'al': np.mean([t['pnl_pct'] for t in losses]) if losses else 0,
        'avg_hold': np.mean([t['hold_h'] for t in trades]),
    }


def run_all(symbols, timeframe='4h'):
    strategies = {
        'PB-strict(EMA3정렬)': sig_pullback_strict,
        'PB-loose(EMA2정렬)': sig_pullback_loose,
        'Donchian 20봉돌파': sig_breakout_donch,
        'MACD 양전+EMA정렬': sig_macd_cross,
        'RSI 모멘텀': sig_momentum,
        'EMA50 바운스': sig_ema_bounce,
    }

    # 다양한 손절/익절 조합
    params_list = [
        ('SL1.5 TP4 분할', {'stop_atr': 1.5, 'take_profit': 0.04, 'use_partial': True}),
        ('SL1.5 TP6 분할', {'stop_atr': 1.5, 'take_profit': 0.06, 'use_partial': True}),
        ('SL2.0 TP5 분할', {'stop_atr': 2.0, 'take_profit': 0.05, 'use_partial': True}),
        ('SL2.0 TP8 분할', {'stop_atr': 2.0, 'take_profit': 0.08, 'use_partial': True}),
        ('SL1.5 ATR트레일', {'stop_atr': 1.5, 'use_partial': False, 'use_atr_trail': True, 'atr_trail_mult': 3.0}),
        ('SL2.0 ATR트레일', {'stop_atr': 2.0, 'use_partial': False, 'use_atr_trail': True, 'atr_trail_mult': 3.0}),
    ]

    print(f"\n{'='*100}")
    print(f"  📊 {timeframe.upper()} 백테스트 - 전략 × 파라미터 조합 (총 {len(strategies)*len(params_list)}개)")
    print(f"{'='*100}")

    all_results = []

    for strat_name, sig_fn in strategies.items():
        for param_name, params in params_list:
            all_trades = []
            for sym in symbols:
                df_1h = load_1h(sym)
                if df_1h is None or len(df_1h) < 200:
                    continue
                df = resample(df_1h, timeframe)
                if len(df) < 130:
                    continue
                df = add_inds(df)
                _, trades = simulate(df, sig_fn, **params)
                all_trades.extend(trades)

            e = evaluate(all_trades)
            if e and e['n'] >= 10:
                all_results.append({
                    'strategy': strat_name,
                    'params': param_name,
                    **e,
                })

    # PF 순 정렬
    all_results.sort(key=lambda x: (x['pf'], x['total']), reverse=True)

    print(f"\n  {'전략':>20} | {'파라미터':>18} | {'거래':>3} | {'승률':>4} | {'PF':>5} | {'손익':>11} | 익절/손절")
    print("  " + "-"*100)
    for r in all_results[:15]:
        print(f"  {r['strategy']:>20} | {r['params']:>18} | {r['n']:>3} | {r['wr']:>3.0f}% | "
              f"{r['pf']:>5.2f} | {r['total']:>+10,.0f} | {r['aw']:>+5.1f}%/{r['al']:>+5.1f}%")

    return all_results


if __name__ == "__main__":
    symbols = ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "BCH", "AVAX", "DOT",
               "LINK", "ATOM", "MATIC", "ARB", "NEAR"]
    print(f"종목: {symbols}")
    for tf in ['4h', '1d']:
        run_all(symbols, tf)
