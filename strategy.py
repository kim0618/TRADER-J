# strategy.py
import pandas as pd
from config import (
    SHORT_MA, LONG_MA,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    RSI_EXTREME_LOW, RSI_EXTREME_HIGH,
    BB_PERIOD, BB_STD, BB_LOWER_THRESHOLD, BB_UPPER_THRESHOLD,
    TRAILING_STOP_TRIGGER, TRAILING_STOP_DROP,
    VOLUME_RATIO_MIN,
    BB_WIDTH_MIN,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    ADX_PERIOD, ADX_TREND_THRESHOLD, ADX_RANGE_THRESHOLD,
    BUY_SIGNAL_REQUIRED_UP, BUY_SIGNAL_REQUIRED_SIDEWAYS,
)

def calculate_indicators(df):
    """RSI(9) + 볼린저밴드(20,2.5σ) + MACD(8,21,5) + ATR + ADX 계산"""
    df = df.copy()

    # 이동평균
    df["short_ma"] = df["close"].rolling(window=SHORT_MA).mean()
    df["long_ma"] = df["close"].rolling(window=LONG_MA).mean()

    # RSI (9봉, 크립토 최적화)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_PERIOD).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # 볼린저밴드 (20봉, 2.5σ)
    df['bb_mid'] = df['close'].rolling(window=BB_PERIOD).mean()
    bb_std = df['close'].rolling(window=BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + (bb_std * BB_STD)
    df['bb_lower'] = df['bb_mid'] - (bb_std * BB_STD)
    bb_range = df['bb_upper'] - df['bb_lower']
    df['bb_pct'] = (df['close'] - df['bb_lower']) / bb_range.replace(0, 1)

    # 거래량 이동평균 (가짜 신호 필터용)
    df['volume_ma'] = df['volume'].rolling(window=20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma'].replace(0, 1)

    # MACD (8, 21, 5 - 크립토 단기 최적화)
    ema_fast = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    df['macd'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd'].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    # BB 밴드 폭 (변동성 측정)
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid'].replace(0, 1)

    # ATR (14봉) - 동적 손절용
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = true_range.rolling(window=14).mean()

    # ADX (레짐 감지: 추세장 vs 횡보장)
    df['adx'] = _calculate_adx(df, ADX_PERIOD)

    return df


def _calculate_adx(df, period=14):
    """ADX (Average Directional Index) 계산"""
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
    """ATR 단독 계산 (check_sell_conditions용)"""
    if df is None or len(df) < period + 1:
        return None
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean().iloc[-1]
    return atr if not pd.isna(atr) else None

def get_market_trend(df_1h):
    """1시간봉으로 시장 추세 파악 (ADX + EMA 복합 판단)"""
    if df_1h is None or len(df_1h) < 50:
        return "SIDEWAYS", 0

    df = df_1h.copy()
    df['ma20'] = df['close'].rolling(window=20).mean()
    df['ma50'] = df['close'].rolling(window=50).mean()

    # 1시간봉에서도 ADX 계산
    df['adx'] = _calculate_adx(df, ADX_PERIOD)
    curr = df.iloc[-1]

    if pd.isna(curr['ma20']) or pd.isna(curr['ma50']):
        return "SIDEWAYS", 0

    price = curr['close']
    ma20 = curr['ma20']
    ma50 = curr['ma50']
    adx = curr['adx'] if not pd.isna(curr['adx']) else 0

    # ADX < 20: 횡보 (추세 없음)
    if adx < ADX_RANGE_THRESHOLD:
        return "SIDEWAYS", adx

    # ADX >= 20: 추세 존재 → 방향은 EMA로 판단
    if price > ma20 and ma20 > ma50:
        return "UP", adx
    elif price < ma20 and ma20 < ma50:
        return "DOWN", adx
    else:
        return "SIDEWAYS", adx

def check_trailing_stop(current_price, avg_buy_price, peak_price):
    """트레일링 스탑 체크"""
    if avg_buy_price <= 0 or peak_price <= 0:
        return False

    profit_rate = (peak_price - avg_buy_price) / avg_buy_price
    drop_from_peak = (current_price - peak_price) / peak_price

    if profit_rate >= TRAILING_STOP_TRIGGER and drop_from_peak <= -TRAILING_STOP_DROP:
        print(f"  └ 🎯 트레일링 스탑 발동!")
        print(f"     고점: {peak_price:,.0f}원 → 현재: {current_price:,.0f}원")
        print(f"     고점수익: {profit_rate*100:.2f}% | 하락: {drop_from_peak*100:.2f}%")
        return True
    return False

def _check_rsi_rising(df, rsi):
    """RSI 2봉 연속 반등 확인 (칼날잡기 방지 강화)"""
    if len(df) >= 3:
        rsi_2 = df.iloc[-3]['rsi']
        rsi_1 = df.iloc[-2]['rsi']
        rsi_0 = rsi
        if pd.isna(rsi_2):
            rsi_2 = rsi_1
        if pd.isna(rsi_1):
            rsi_1 = rsi_0
        # 현재 >= 1봉전 AND 현재 > 2봉전 (2봉 기준 반등 확인)
        return rsi_0 >= rsi_1 and rsi_0 > rsi_2
    elif len(df) >= 2:
        prev_rsi = df.iloc[-2]['rsi']
        if pd.isna(prev_rsi):
            prev_rsi = rsi
        return rsi >= prev_rsi
    return False

def check_signal(df, current_price, avg_buy_price=None, df_1h=None, peak_price=0):
    """
    매매 신호 판단 (v2 - ADX 레짐 감지 + 최적화)

    레짐 감지: ADX로 추세장/횡보장 구분
    매수 필수조건: 추세가 DOWN이 아님
    매수 조건 (3개 중):
        조건1. RSI <= 40
        조건2. BB% <= 0.3
        조건3. MACD 히스토그램 양수
    필요 충족수:
        UP (강한 추세, ADX>=25): 2/3
        SIDEWAYS: 2/3 (3/3→2/3 완화)
        DOWN: 매수 차단

    매도 조건 (4개 중 2개):
        조건1. RSI >= 68
        조건2. BB% >= 0.8
        조건3. 추세가 DOWN
        조건4. MACD 히스토그램 음수

    즉시 매수: RSI <= 25 (극단 과매도, 반등 확인 + 비하락추세)
    즉시 매도: RSI >= 73 (극단 과매수)
    """
    df = calculate_indicators(df)
    curr = df.iloc[-1]
    rsi = curr['rsi']
    bb_pct = curr['bb_pct']
    adx_5m = curr.get('adx', 0)

    # 추세 파악 (ADX 기반)
    trend, adx_1h = get_market_trend(df_1h)

    # 지표 출력
    if not pd.isna(rsi):
        if rsi <= RSI_EXTREME_LOW:
            rsi_icon = "🔴🔴"
        elif rsi <= RSI_OVERSOLD:
            rsi_icon = "🔴"
        elif rsi >= RSI_EXTREME_HIGH:
            rsi_icon = "🟡🟡"
        elif rsi >= RSI_OVERBOUGHT:
            rsi_icon = "🟡"
        else:
            rsi_icon = "🟢"

        macd_hist = curr.get('macd_hist', 0)
        macd_icon = "▲" if not pd.isna(macd_hist) and macd_hist > 0 else "▼"
        trend_icon = "📈" if trend == "UP" else ("📉" if trend == "DOWN" else "➡️")
        volume_ratio = curr.get('volume_ratio', 0)
        vol_icon = "🔊" if not pd.isna(volume_ratio) and volume_ratio >= VOLUME_RATIO_MIN else "🔇"
        adx_display = adx_1h if adx_1h else (adx_5m if not pd.isna(adx_5m) else 0)
        print(f"  └ RSI: {rsi:.1f}{rsi_icon} | BB%: {bb_pct:.2f} | MACD: {macd_icon} | 추세: {trend}{trend_icon} ADX:{adx_display:.0f} | 거래량: {volume_ratio:.1f}x{vol_icon}")

    if pd.isna(rsi) or pd.isna(bb_pct):
        print(f"  └ ⚠️ 지표 계산 중 (데이터 부족), HOLD")
        return "HOLD"

    # 거래량 필터 (평균 미만이면 가짜 신호 HOLD)
    volume_ratio = curr.get('volume_ratio', 1.0)
    if not pd.isna(volume_ratio) and volume_ratio < VOLUME_RATIO_MIN:
        print(f"  └ ⚠️ 거래량 부족 ({volume_ratio:.2f}x < {VOLUME_RATIO_MIN}x 평균), HOLD")
        return "HOLD"

    # BB 변동성 필터 (밴드 폭이 너무 좁으면 횡보 구간 → HOLD)
    bb_width = curr.get('bb_width', 1.0)
    if not pd.isna(bb_width) and bb_width < BB_WIDTH_MIN:
        print(f"  └ ⚠️ 변동성 부족 (BB폭: {bb_width:.3f} < {BB_WIDTH_MIN}), 횡보 구간 HOLD")
        return "HOLD"

    # 1순위 - 트레일링 스탑
    if avg_buy_price and peak_price > 0:
        if check_trailing_stop(current_price, avg_buy_price, peak_price):
            return "SELL"

    # 2순위 - 극단값 즉시 매수/매도
    if rsi <= RSI_EXTREME_LOW:
        if trend == "DOWN":
            print(f"  └ 🚫 극단 과매도지만 하락추세 → 매수 차단 (RSI: {rsi:.1f})")
        else:
            rsi_rising = _check_rsi_rising(df, rsi)
            if rsi_rising:
                print(f"  └ 💡 극단 과매도 즉시 매수! RSI: {rsi:.1f} (추세{trend}✅ 반등✅)")
                return "BUY"
            else:
                print(f"  └ ⏳ 극단 과매도지만 RSI 반등 미확인, 대기")

    if rsi >= RSI_EXTREME_HIGH:
        print(f"  └ 💡 극단 과매수 즉시 매도! RSI: {rsi:.1f}")
        return "SELL"

    # 3순위 - 추세별 매수 조건 (DOWN: 차단, UP/SIDEWAYS: 2/3)
    macd_hist = curr.get('macd_hist', 0)

    if trend != "DOWN":
        buy_score = 0
        buy_reasons = []

        if rsi <= RSI_OVERSOLD:
            buy_score += 1
            buy_reasons.append(f"RSI({rsi:.1f})")
        if bb_pct <= BB_LOWER_THRESHOLD:
            buy_score += 1
            buy_reasons.append(f"BB하단({bb_pct:.2f})")
        if not pd.isna(macd_hist) and macd_hist > 0:
            buy_score += 1
            buy_reasons.append(f"MACD▲")

        # 추세별 진입 조건 차등 적용
        # UP: 2/3 (추세 추종, 조건 완화)
        # SIDEWAYS: 3/3 (횡보장 false signal 차단, 전체 조건 필요)
        required = BUY_SIGNAL_REQUIRED_UP if trend == "UP" else BUY_SIGNAL_REQUIRED_SIDEWAYS

        if buy_score >= required:
            rsi_rising = _check_rsi_rising(df, rsi)
            if not rsi_rising:
                print(f"  └ ⏳ RSI 반등 미확인 (2봉 기준), 대기")
                return "HOLD"

            print(f"  └ 💡 매수 신호! [{' + '.join(buy_reasons)}] ({buy_score}/3, 필요{required}) 추세{trend}✅ RSI반등✅")
            return "BUY"

    # 4순위 - 매도 (4개 중 3개 충족)
    sell_score = 0
    sell_reasons = []

    if rsi >= RSI_OVERBOUGHT:
        sell_score += 1
        sell_reasons.append(f"RSI({rsi:.1f})")
    if bb_pct >= BB_UPPER_THRESHOLD:
        sell_score += 1
        sell_reasons.append(f"BB상단({bb_pct:.2f})")
    if trend == "DOWN":
        sell_score += 1
        sell_reasons.append(f"추세({trend})")
    if not pd.isna(macd_hist) and macd_hist < 0:
        sell_score += 1
        sell_reasons.append(f"MACD▼")

    if sell_score >= 3:
        print(f"  └ 💡 매도 신호! [{' + '.join(sell_reasons)}] ({sell_score}/4)")
        return "SELL"

    # 매수/매도 점수 출력 (디버깅용)
    print(f"  └ 매도점수: {sell_score}/4 | 추세: {trend} ADX:{adx_1h:.0f} → HOLD")
    return "HOLD"


if __name__ == "__main__":
    from collector import get_ohlcv, get_ohlcv_1h, get_current_price

    print("=" * 55)
    print("   v2 전략 테스트 (ADX레짐 + RSI9 + MACD8/21/5)")
    print("=" * 55)

    for symbol in ["BTC", "XRP"]:
        print(f"\n▶ [{symbol}]")
        df = get_ohlcv(symbol)
        df_1h = get_ohlcv_1h(symbol)
        price = get_current_price(symbol)

        if df is not None and price:
            df_calc = calculate_indicators(df)
            curr = df_calc.iloc[-1]
            trend, adx = get_market_trend(df_1h)

            print(f"  현재가:  {price:,.0f}원")
            print(f"  RSI(9):  {curr['rsi']:.1f}")
            print(f"  BB%:     {curr['bb_pct']:.2f}")
            print(f"  BB상단:  {curr['bb_upper']:,.0f}원")
            print(f"  BB하단:  {curr['bb_lower']:,.0f}원")
            print(f"  ATR:     {curr['atr']:,.0f}원")
            print(f"  ADX:     {curr['adx']:.1f} (5m) / {adx:.1f} (1h)")
            print(f"  추세:    {trend}")
            signal = check_signal(df, price, df_1h=df_1h)
            print(f"  신호:    {signal}")
