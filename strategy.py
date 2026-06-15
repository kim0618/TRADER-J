# strategy.py - Swing Strategy v3.0
# 1H 시그널 + 1D 추세 필터 + Donchian 돌파 / 추세 풀백 하이브리드
import pandas as pd
import numpy as np
from config import (
    EMA_FAST, EMA_MID, EMA_LONG,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    RSI_PULLBACK_LOW, RSI_PULLBACK_HIGH,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    DONCHIAN_PERIOD, BREAKOUT_BUFFER, BREAKOUT_VOLUME_MIN,
    ADX_PERIOD, ADX_BREAKOUT_MIN,
    VOLUME_RATIO_MIN, BUY_VOLUME_MIN,
    TRAILING_STOP_TRIGGER, TRAILING_STOP_DROP,
    BB_PERIOD, BB_STD,
    DAILY_TREND_FILTER,
)


def calculate_indicators(df):
    """1H 봉 기반 지표 계산 (스윙 v3.0)"""
    df = df.copy()

    # EMA (단/중)
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_mid'] = df['close'].ewm(span=EMA_MID, adjust=False).mean()
    df['short_ma'] = df['ema_fast']  # 호환
    df['long_ma'] = df['ema_mid']    # 호환

    # RSI (14)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))
    df['rsi'] = df['rsi'].fillna(50)

    # MACD (12-26-9)
    ema_fast = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    df['macd'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd'].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    # Donchian 채널 (N봉 전 고가/저가)
    df['high_n'] = df['high'].rolling(window=DONCHIAN_PERIOD).max().shift(1)
    df['low_n'] = df['low'].rolling(window=DONCHIAN_PERIOD).min().shift(1)

    # 볼린저밴드 (호환용, 일부 모듈 사용)
    df['bb_mid'] = df['close'].rolling(window=BB_PERIOD).mean()
    bb_std = df['close'].rolling(window=BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + (bb_std * BB_STD)
    df['bb_lower'] = df['bb_mid'] - (bb_std * BB_STD)
    bb_range = df['bb_upper'] - df['bb_lower']
    df['bb_pct'] = (df['close'] - df['bb_lower']) / bb_range.replace(0, 1)
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid'].replace(0, 1)

    # 거래량
    df['volume_ma'] = df['volume'].rolling(window=20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma'].replace(0, 1)

    # ATR (14)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=14).mean()

    # ADX (추세 강도)
    df['adx'] = _calculate_adx(df, ADX_PERIOD)

    return df


def _calculate_adx(df, period=14):
    """ADX 계산"""
    high = df['high']
    low = df['low']
    close = df['close']

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    high_low = high - low
    high_close = (high - close.shift(1)).abs()
    low_close = (low - close.shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, 1))
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, 1))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1))
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx


def get_atr(df, period=14):
    """ATR 단독 계산 (check_sell_conditions 호환)"""
    if df is None or len(df) < period + 1:
        return None
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean().iloc[-1]
    return atr if not pd.isna(atr) else None


def aggregate_to_daily(df_1h):
    """1H 데이터를 1D로 집계"""
    if df_1h is None or len(df_1h) < 24:
        return None
    df = df_1h.copy()
    if 'time' not in df.columns:
        return None
    df = df.set_index('time')
    daily = df.resample('1D').agg({
        'open': 'first',
        'close': 'last',
        'high': 'max',
        'low': 'min',
        'volume': 'sum'
    }).dropna()
    if len(daily) < 30:
        return None
    daily['ema20'] = daily['close'].ewm(span=20, adjust=False).mean()
    daily['ema50'] = daily['close'].ewm(span=50, adjust=False).mean()
    return daily.reset_index()


def get_daily_trend(df_1h):
    """1H 데이터를 1D로 집계해서 장기 추세 판단"""
    daily = aggregate_to_daily(df_1h)
    if daily is None or len(daily) < 50:
        return "SIDEWAYS"

    curr = daily.iloc[-1]
    price = curr['close']
    ema20 = curr['ema20']
    ema50 = curr['ema50']

    if pd.isna(ema50) or pd.isna(ema20):
        return "SIDEWAYS"

    # UP: 가격 > EMA50 AND EMA20 > EMA50
    if price > ema50 and ema20 > ema50:
        return "UP"
    # DOWN: 가격 < EMA50 AND EMA20 < EMA50
    if price < ema50 and ema20 < ema50:
        return "DOWN"
    return "SIDEWAYS"


def get_market_trend(df_1h):
    """호환 함수: (trend, adx) 튜플 반환"""
    trend = get_daily_trend(df_1h)
    # adx 추정: 1H 데이터 마지막 adx
    adx = 0
    if df_1h is not None and len(df_1h) >= 14:
        try:
            df_calc = df_1h.copy()
            df_calc['_adx'] = _calculate_adx(df_calc, ADX_PERIOD)
            last = df_calc['_adx'].iloc[-1]
            if not pd.isna(last):
                adx = float(last)
        except Exception:
            pass
    return trend, adx


def check_trailing_stop(current_price, avg_buy_price, peak_price):
    """트레일링 스탑 체크"""
    if avg_buy_price <= 0 or peak_price <= 0:
        return False

    profit_rate = (peak_price - avg_buy_price) / avg_buy_price
    drop_from_peak = (current_price - peak_price) / peak_price

    if profit_rate >= TRAILING_STOP_TRIGGER and drop_from_peak <= -TRAILING_STOP_DROP:
        return True
    return False


def check_signal(df, current_price, avg_buy_price=None, df_1h=None, peak_price=0):
    """
    스윙 매매 신호 (v3.0)

    df: 1H OHLCV (primary signal timeframe)
    df_1h: 1H OHLCV (1D 집계용 - df와 동일하거나 더 긴 1H 데이터)
            None이면 df 자체를 사용해서 1D 집계 시도

    매수 조건 (둘 다 충족):
    A. 1D 추세: UP (price > EMA50_1D AND EMA20_1D > EMA50_1D)
    B. 1H 진입 트리거 (둘 중 하나):
       (1) 돌파: 종가 > DONCHIAN_PERIOD 봉 고가 (+버퍼) AND 거래량 1.5x↑ AND ADX 20↑
       (2) 추세 풀백: EMA9 > EMA21 AND RSI 35-55 AND RSI 상승 AND MACD hist > 0

    매도 조건 (보유 중일 때):
    1. 트레일링 스탑 발동
    2. 1D 추세 반전 (DOWN)
    3. 1H 모멘텀 소진 (MACD hist < 0 AND RSI < 45 AND 보유 > 24h)
    """
    df = calculate_indicators(df)
    if len(df) < 2:
        return "HOLD"

    curr = df.iloc[-1]
    price = current_price

    rsi = curr['rsi']
    macd_hist = curr.get('macd_hist', 0)
    high_n = curr.get('high_n', None)
    volume_ratio = curr.get('volume_ratio', 1.0)
    adx = curr.get('adx', 0)
    ema_fast = curr.get('ema_fast', None)
    ema_mid = curr.get('ema_mid', None)

    # ── 1D 추세 ──
    df_for_daily = df_1h if df_1h is not None else df
    daily_trend = get_daily_trend(df_for_daily) if DAILY_TREND_FILTER else "UP"

    # 지표 출력
    if not pd.isna(rsi):
        trend_icon = "📈" if daily_trend == "UP" else ("📉" if daily_trend == "DOWN" else "➡️")
        rsi_icon = "🔴" if rsi <= RSI_OVERSOLD else ("🟡" if rsi >= RSI_OVERBOUGHT else "🟢")
        macd_icon = "▲" if not pd.isna(macd_hist) and macd_hist > 0 else "▼"
        vol_icon = "🔊" if not pd.isna(volume_ratio) and volume_ratio >= 1.0 else "🔇"
        ema_align = "✓" if (ema_fast is not None and ema_mid is not None
                            and not pd.isna(ema_fast) and not pd.isna(ema_mid)
                            and ema_fast > ema_mid) else "✗"
        print(f"  └ 추세{daily_trend}{trend_icon} | RSI:{rsi:.1f}{rsi_icon} | "
              f"MACD:{macd_icon} | EMA정배열:{ema_align} | "
              f"ADX:{adx:.0f} | Vol:{volume_ratio:.1f}x{vol_icon}")

    # 데이터 부족 체크
    if pd.isna(rsi) or pd.isna(macd_hist) or ema_fast is None or pd.isna(ema_fast):
        return "HOLD"

    # ============ 매도 조건 (보유 중일 때만) ============
    # 철학: 스윙은 추세 추종 - 정상 풀백에서 흔들리지 말고 stop/trailing/추세반전만 작동
    if avg_buy_price and avg_buy_price > 0:
        # 1) 트레일링 스탑
        if peak_price and peak_price > 0:
            if check_trailing_stop(price, avg_buy_price, peak_price):
                print(f"  └ 🎯 트레일링 스탑 발동")
                return "SELL"

        # 2) 1D 추세 반전 강제 청산 (메인 출구)
        if daily_trend == "DOWN":
            print(f"  └ 📉 1D 추세 DOWN 전환 → 청산")
            return "SELL"

        # 3) 심각한 모멘텀 붕괴만 (정상 풀백 회피)
        #    RSI 30 이하 (확실한 약세) AND EMA 데드크로스 AND MACD 음수
        if (not pd.isna(macd_hist) and macd_hist < 0
                and rsi < 30
                and ema_fast is not None and ema_mid is not None
                and not pd.isna(ema_fast) and not pd.isna(ema_mid)
                and ema_fast < ema_mid):
            print(f"  └ 💀 심각한 약세 (RSI{rsi:.0f} + EMA데드 + MACD▼) → 청산")
            return "SELL"

    # ============ 매수 조건 ============
    # A. 1D 추세 필터: UP 일 때만
    if daily_trend != "UP":
        return "HOLD"

    # 거래량 기본 필터
    if not pd.isna(volume_ratio) and volume_ratio < VOLUME_RATIO_MIN:
        return "HOLD"

    # B-(1) Donchian 돌파
    breakout = False
    if high_n is not None and not pd.isna(high_n):
        if (price > high_n * (1 + BREAKOUT_BUFFER)
                and volume_ratio >= BREAKOUT_VOLUME_MIN
                and adx >= ADX_BREAKOUT_MIN):
            breakout = True

    # B-(2) 추세 풀백 (1H EMA 정배열 + RSI 중립권 + RSI 상승 + MACD 양수)
    pullback = False
    rsi_prev = df.iloc[-2]['rsi'] if len(df) >= 2 else rsi
    rsi_rising = not pd.isna(rsi_prev) and rsi >= rsi_prev

    if (not pd.isna(ema_mid)
            and ema_fast > ema_mid
            and RSI_PULLBACK_LOW <= rsi <= RSI_PULLBACK_HIGH
            and rsi_rising
            and macd_hist > 0
            and volume_ratio >= BUY_VOLUME_MIN):
        pullback = True

    if breakout:
        print(f"  └ 🚀 돌파 매수! 가격 {price:,.0f} > {DONCHIAN_PERIOD}봉 고점 {high_n:,.0f} "
              f"(Vol {volume_ratio:.1f}x, ADX {adx:.0f})")
        return "BUY"

    if pullback:
        print(f"  └ 💡 풀백 매수! EMA정배열 + RSI{rsi:.0f}(상승) + MACD▲")
        return "BUY"

    return "HOLD"


if __name__ == "__main__":
    from collector import get_ohlcv_1h, get_current_price

    print("=" * 60)
    print("   Swing Strategy v3.0 (1H 시그널 + 1D 추세 필터)")
    print("=" * 60)

    for symbol in ["BTC", "ETH", "XRP", "SOL"]:
        print(f"\n▶ [{symbol}]")
        df_1h = get_ohlcv_1h(symbol)
        price = get_current_price(symbol)

        if df_1h is not None and price:
            daily = aggregate_to_daily(df_1h)
            daily_trend = get_daily_trend(df_1h)
            print(f"  현재가: {price:,.0f}원 | 1D 추세: {daily_trend}")
            if daily is not None:
                d = daily.iloc[-1]
                print(f"  1D EMA20: {d['ema20']:,.0f} | EMA50: {d['ema50']:,.0f}")

            signal = check_signal(df_1h, price, df_1h=df_1h)
            print(f"  신호: {signal}")
