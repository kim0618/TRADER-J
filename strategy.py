# strategy.py - Swing Strategy v4.0
# 검증된 백테스트 결과 (PF 1.05, 87일 15종목):
# - Ensemble AND: 추세 풀백 + MACD 방향 일치
# - 4시간봉 기준
# - EMA 3정렬 + EMA20 풀백 + 반등 + MACD 양수
# - ATR × 1.5 손절, +6% 절반 익절, 트레일링
import pandas as pd
import numpy as np
from config import (
    EMA_FAST, EMA_MID, EMA_LONG,
    RSI_PERIOD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    ADX_PERIOD,
    VOLUME_RATIO_MIN,
    TRAILING_STOP_TRIGGER, TRAILING_STOP_DROP,
    BB_PERIOD, BB_STD,
)


def _resample_to_4h(df):
    """1H 데이터를 4H로 집계 (v4.0 스윙 최적화)"""
    if 'time' not in df.columns:
        return df
    try:
        df = df.copy()
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')
        df_4h = df.resample('4h').agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum',
        }).dropna().reset_index()
        return df_4h
    except Exception:
        return df.reset_index(drop=True) if 'time' in df.columns else df


def calculate_indicators(df):
    """4시간봉 기반 지표 계산 (스윙 v4.0)"""
    # 1H 데이터가 오면 4H로 자동 집계
    if len(df) > 100 and 'time' in df.columns:
        try:
            times = pd.to_datetime(df['time'])
            diff_hours = (times.iloc[-1] - times.iloc[-2]).total_seconds() / 3600
            if diff_hours < 3:  # 1H 이하로 판단되면 집계
                df = _resample_to_4h(df)
        except Exception:
            pass
    df = df.copy()

    # EMA 3중 (20/50/100)
    df['ema_f'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_m'] = df['close'].ewm(span=EMA_MID, adjust=False).mean()
    df['ema_l'] = df['close'].ewm(span=EMA_LONG, adjust=False).mean()
    df['short_ma'] = df['ema_f']
    df['long_ma'] = df['ema_m']
    # 호환용 (backtest.py 등이 참조)
    df['ema_fast'] = df['ema_f']
    df['ema_mid'] = df['ema_m']

    # RSI (14)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(RSI_PERIOD).mean()
    df['rsi'] = (100 - (100 / (1 + gain / loss.replace(0, np.nan)))).fillna(50)

    # MACD (12-26-9)
    ef = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    es = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    df['macd'] = ef - es
    df['macd_signal'] = df['macd'].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    # ATR (14)
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    # 거래량
    df['volume_ma'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma'].replace(0, 1)

    # ADX
    df['adx'] = _calculate_adx(df, ADX_PERIOD)

    # 볼린저 (호환용)
    df['bb_mid'] = df['close'].rolling(BB_PERIOD).mean()
    bb_std = df['close'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * bb_std
    df['bb_lower'] = df['bb_mid'] - BB_STD * bb_std
    bb_range = (df['bb_upper'] - df['bb_lower']).replace(0, 1)
    df['bb_pct'] = (df['close'] - df['bb_lower']) / bb_range
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid'].replace(0, 1)

    # 돈치안 (호환용, v3.0 잔재 - 사용 안 함)
    df['high_n'] = df['high'].rolling(20).max().shift(1)
    df['low_n'] = df['low'].rolling(20).min().shift(1)

    return df


def _calculate_adx(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, 1))
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, 1))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1))
    return dx.ewm(span=period, adjust=False).mean()


def get_atr(df, period=14):
    if 'atr' in df.columns:
        v = df['atr'].iloc[-1]
        if not pd.isna(v):
            return v
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    v = tr.rolling(period).mean().iloc[-1]
    return v if not pd.isna(v) else 0


def aggregate_to_daily(df_1h):
    """1H → 1D 집계 (호환용)"""
    df = df_1h.copy()
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')
    daily = df.resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum',
    }).dropna().reset_index()
    daily['ema20'] = daily['close'].ewm(span=20, adjust=False).mean()
    daily['ema50'] = daily['close'].ewm(span=50, adjust=False).mean()
    return daily


def get_daily_trend(df_1h):
    """1D 추세 반환 (v4.0에서는 미사용, 호환용)"""
    try:
        daily = aggregate_to_daily(df_1h)
        if len(daily) < 51:
            return "SIDE"
        d = daily.iloc[-1]
        if pd.isna(d['ema20']) or pd.isna(d['ema50']):
            return "SIDE"
        if d['close'] > d['ema50'] and d['ema20'] > d['ema50']:
            return "UP"
        if d['close'] < d['ema50'] and d['ema20'] < d['ema50']:
            return "DOWN"
    except Exception:
        pass
    return "SIDE"


def check_trailing_stop(current_price, avg_buy_price, peak_price):
    """트레일링 스탑"""
    if avg_buy_price <= 0 or peak_price <= 0:
        return False
    profit_rate = (peak_price - avg_buy_price) / avg_buy_price
    drop_from_peak = (current_price - peak_price) / peak_price
    return (profit_rate >= TRAILING_STOP_TRIGGER
            and drop_from_peak <= -TRAILING_STOP_DROP)


def check_signal(df, current_price, avg_buy_price=None, df_1h=None, peak_price=0):
    """
    스윙 매매 신호 (v4.0 - 검증된 Ensemble AND)

    매수 조건 (모두 만족):
    A. 추세 풀백 (Pullback):
       - EMA 3정렬: EMA20 > EMA50 > EMA100
       - 직전봉 저점이 EMA20에 근접 (1% 이내 터치)
       - 현재봉 양봉 + 종가가 EMA20 위 (반등 확인)
       - ADX > 20 (추세 있음)
    B. MACD 방향 일치:
       - MACD hist > 0
       - EMA20 > EMA50 (단기 상승)

    매도 조건:
    1. 트레일링 스탑 (+7% 후 -3%)
    2. EMA50 이탈 (추세 붕괴)
    3. MACD hist 음전 + 심각한 약세
    """
    df = calculate_indicators(df)
    if len(df) < 3:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    price = current_price

    rsi = curr['rsi']
    macd_h = curr.get('macd_hist', 0)
    ema_f = curr.get('ema_f', None)
    ema_m = curr.get('ema_m', None)
    ema_l = curr.get('ema_l', None)
    adx = curr.get('adx', 0)
    vol_r = curr.get('volume_ratio', 1.0)

    if any(pd.isna(x) if x is not None else True for x in [rsi, macd_h, ema_f, ema_m, ema_l, adx]):
        return "HOLD"

    ema_align = ema_f > ema_m > ema_l
    align_icon = "✓" if ema_align else "✗"
    macd_icon = "▲" if macd_h > 0 else "▼"
    vol_icon = "🔊" if vol_r >= 1.0 else "🔇"
    print(f"  └ EMA정렬:{align_icon} | RSI:{rsi:.1f} | MACD:{macd_icon} | "
          f"ADX:{adx:.0f} | Vol:{vol_r:.1f}x{vol_icon}")

    # ============ 매도 (보유 중일 때) ============
    if avg_buy_price and avg_buy_price > 0:
        if peak_price and peak_price > 0:
            if check_trailing_stop(price, avg_buy_price, peak_price):
                print(f"  └ 🎯 트레일링 스탑 발동")
                return "SELL"

        if (price < ema_m and prev['close'] < prev['ema_m']):
            print(f"  └ 📉 EMA50 이탈 (추세 붕괴)")
            return "SELL"

        if (macd_h < 0 and ema_f < ema_m and rsi < 40):
            print(f"  └ 💀 약세 (MACD▼ + EMA데드 + RSI{rsi:.0f})")
            return "SELL"

    # ============ 매수 (Ensemble AND) ============
    if not ema_align:
        return "HOLD"
    touched = prev['low'] < prev['ema_f'] * 1.01
    if not touched:
        return "HOLD"
    bounced = curr['close'] > curr['open'] and curr['close'] > ema_f
    if not bounced:
        return "HOLD"
    if adx < 20:
        return "HOLD"
    if macd_h <= 0:
        return "HOLD"
    if ema_f <= ema_m:
        return "HOLD"
    if vol_r < VOLUME_RATIO_MIN:
        return "HOLD"

    print(f"  └ 💡 스윙 매수! PB+MACD 이중 확인 (RSI:{rsi:.0f}, ADX:{adx:.0f})")
    return "BUY"


if __name__ == "__main__":
    from collector import get_current_price
    try:
        from collector import get_swing_data
        get_data = get_swing_data
    except ImportError:
        from collector import get_ohlcv_1h as get_data

    print("=" * 60)
    print("   Swing Strategy v4.0 (Ensemble AND: PB + MACD)")
    print("=" * 60)

    for symbol in ["BTC", "ETH", "XRP", "SOL"]:
        print(f"\n▶ [{symbol}]")
        df = get_data(symbol)
        price = get_current_price(symbol)
        if df is not None and price:
            signal = check_signal(df, price)
            print(f"  신호: {signal}")
