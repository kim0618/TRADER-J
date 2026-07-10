"""
최종 스윙 전략 검증
- Ensemble (여러 전략 조합) vs 단일 전략
- 4h vs 6h vs 1h+1d 하이브리드
- 최적 파라미터 그리드
"""
import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings("ignore")

INITIAL = 1_000_000
FEE = 0.0025
SLIP = 0.002
RISK = 0.01


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


def add_inds(df):
    df = df.copy()
    df['ema_f'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema_m'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_l'] = df['close'].ewm(span=100, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df['rsi'] = (100 - (100 / (1 + gain / loss.replace(0, np.nan)))).fillna(50)

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

    df['vol_avg'] = df['volume'].rolling(20).mean()
    df['vol_r'] = df['volume'] / df['vol_avg'].replace(0, 1)

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


# ━━━ 신호 함수들 ━━━
def sig_pullback(df, i, adx_min=20):
    """EMA3정렬 + EMA20 풀백 + 반등"""
    r, p = df.iloc[i], df.iloc[i-1]
    if not (r['ema_f'] > r['ema_m'] > r['ema_l']): return False
    touched = p['low'] < p['ema_f'] * 1.01
    bounced = r['close'] > r['open'] and r['close'] > r['ema_f']
    return touched and bounced and r['adx'] > adx_min


def sig_macd(df, i, adx_min=15):
    """MACD 양전 + EMA 상승 + RSI 상승"""
    r, p = df.iloc[i], df.iloc[i-1]
    return (p['macd_h'] <= 0 and r['macd_h'] > 0
            and r['ema_f'] > r['ema_m']
            and r['rsi'] > 50
            and r['adx'] > adx_min)


def sig_ensemble(df, i):
    """PB 또는 MACD 어느 하나만 true여도 매수"""
    return sig_pullback(df, i) or sig_macd(df, i)


def sig_ensemble_strict(df, i):
    """PB AND MACD 방향 일치 시만"""
    r = df.iloc[i]
    pb = sig_pullback(df, i)
    macd_positive = r['macd_h'] > 0 and r['ema_f'] > r['ema_m']
    return pb and macd_positive


# ━━━ 시뮬레이션 엔진 ━━━
def simulate(df, sig_fn, stop_atr=1.5, tp1=0.04, tp1_ratio=0.5,
             trail_lookback=10, use_ema_exit=True):
    capital = INITIAL
    pos = None
    trades = []

    for i in range(120, len(df)):
        r = df.iloc[i]
        if pd.isna(r.get('adx')) or pd.isna(r.get('atr')): continue

        if pos is not None:
            current = r['close']
            pos['peak'] = max(pos['peak'], r['high'])

            # 부분 익절
            if not pos['partial']:
                if current >= pos['entry'] * (1 + tp1):
                    sp = pos['entry'] * (1 + tp1) * (1 - SLIP)
                    sq = pos['qty'] * tp1_ratio
                    fee = sq * sp * FEE
                    capital += sq * sp - fee
                    pos['partial_pnl'] = sq * (sp - pos['entry']) - fee
                    pos['qty'] -= sq
                    pos['partial'] = True
                    continue

            sl_price = pos['entry'] - stop_atr * pos['atr']
            recent_low = df['low'].iloc[max(0, i-trail_lookback):i].min()

            exit_p, reason = None, None
            if current <= sl_price:
                exit_p = sl_price * (1 - SLIP); reason = "SL"
            elif pos['partial'] and current <= recent_low:
                exit_p = recent_low * (1 - SLIP); reason = "TRAIL"
            elif use_ema_exit and r['close'] < r['ema_m']:
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

        if sig_fn(df, i):
            entry = r['close'] * (1 + SLIP)
            risk = capital * RISK
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
    if not trades: return None
    n = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    total = sum(t['pnl'] for t in trades)
    win_p = sum(t['pnl'] for t in wins)
    loss_p = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    return {
        'n': n, 'wr': len(wins)/n*100,
        'pf': win_p/loss_p if loss_p > 0 else 99,
        'total': total,
        'aw': np.mean([t['pnl_pct'] for t in wins]) if wins else 0,
        'al': np.mean([t['pnl_pct'] for t in trades if t['pnl'] <= 0]) if any(t['pnl']<=0 for t in trades) else 0,
        'avg_hold': np.mean([t['hold_h'] for t in trades]),
    }


def test_across_symbols(sig_fn, symbols, tf='4h', **params):
    all_trades = []
    for sym in symbols:
        df_1h = load_1h(sym)
        if df_1h is None or len(df_1h) < 200: continue
        df = resample(df_1h, tf)
        if len(df) < 130: continue
        df = add_inds(df)
        _, trades = simulate(df, sig_fn, **params)
        all_trades.extend(trades)
    return evaluate(all_trades)


if __name__ == "__main__":
    # 다양한 종목 조합으로 검증
    symbol_sets = {
        '메이저 5': ["BTC", "ETH", "XRP", "SOL", "DOGE"],
        '메이저 10': ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "BCH", "AVAX", "DOT"],
        '전체 15': ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "BCH", "AVAX", "DOT",
                   "LINK", "ATOM", "MATIC", "ARB", "NEAR"],
    }

    strategies = {
        'PB (EMA3정렬 풀백)': sig_pullback,
        'MACD (양전 + EMA)': sig_macd,
        'Ensemble OR (둘 중 하나)': sig_ensemble,
        'Ensemble AND (둘 다 만족)': sig_ensemble_strict,
    }

    # 파라미터 조합 4가지
    param_sets = [
        ('SL1.5 TP4 half', {'stop_atr': 1.5, 'tp1': 0.04, 'tp1_ratio': 0.5}),
        ('SL2.0 TP5 half', {'stop_atr': 2.0, 'tp1': 0.05, 'tp1_ratio': 0.5}),
        ('SL1.5 TP6 half', {'stop_atr': 1.5, 'tp1': 0.06, 'tp1_ratio': 0.5}),
        ('SL2.0 TP4 60%', {'stop_atr': 2.0, 'tp1': 0.04, 'tp1_ratio': 0.6}),
    ]

    print(f"{'='*115}")
    print(f"  📊 스윙 전략 최종 검증 (4h, 87일 데이터)")
    print(f"{'='*115}")

    for set_name, symbols in symbol_sets.items():
        print(f"\n▶ {set_name} ({len(symbols)}종목)")
        print(f"  {'전략':>25} | {'파라미터':>18} | {'거래':>3} | {'승률':>4} | {'PF':>5} | {'수익':>10} | 익절/손절")
        print("  " + "-"*115)

        results = []
        for strat_name, sig_fn in strategies.items():
            for pname, params in param_sets:
                e = test_across_symbols(sig_fn, symbols, tf='4h', **params)
                if e and e['n'] >= 5:
                    results.append((strat_name, pname, e))

        results.sort(key=lambda x: (x[2]['pf'], x[2]['total']), reverse=True)
        for strat_name, pname, e in results[:8]:
            print(f"  {strat_name:>25} | {pname:>18} | {e['n']:>3} | {e['wr']:>3.0f}% | "
                  f"{e['pf']:>5.2f} | {e['total']:>+9,.0f} | {e['aw']:>+5.1f}%/{e['al']:>+5.1f}%")
