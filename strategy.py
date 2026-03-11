# strategy.py
import pandas as pd
import os
import json
from config import (
    SHORT_MA, LONG_MA, STOP_LOSS, TAKE_PROFIT_BASE,    # ← TAKE_PROFIT_BASE로 변경
    RSI_OVERSOLD, RSI_OVERBOUGHT,
    RSI_EXTREME_LOW, RSI_EXTREME_HIGH,
    BB_LOWER_THRESHOLD, BB_UPPER_THRESHOLD,
    TRAILING_STOP_TRIGGER, TRAILING_STOP_DROP
)
from dotenv import load_dotenv
load_dotenv()

def calculate_indicators(df):
    """RSI + 볼린저밴드 + 이동평균 계산"""
    df = df.copy()

    # 이동평균
    df["short_ma"] = df["close"].rolling(window=SHORT_MA).mean()
    df["long_ma"] = df["close"].rolling(window=LONG_MA).mean()

    # RSI (14봉)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # 볼린저밴드 (20봉)
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + (bb_std * 2)
    df['bb_lower'] = df['bb_mid'] - (bb_std * 2)
    bb_range = df['bb_upper'] - df['bb_lower']
    df['bb_pct'] = (df['close'] - df['bb_lower']) / bb_range.replace(0, 1)

    return df

def get_market_trend(df_1h):
    """1시간봉으로 시장 추세 파악"""
    if df_1h is None or len(df_1h) < 50:
        return "SIDEWAYS"

    df = df_1h.copy()
    df['ma20'] = df['close'].rolling(window=20).mean()
    df['ma50'] = df['close'].rolling(window=50).mean()
    curr = df.iloc[-1]

    if pd.isna(curr['ma20']) or pd.isna(curr['ma50']):
        return "SIDEWAYS"

    price = curr['close']
    ma20 = curr['ma20']
    ma50 = curr['ma50']

    if price > ma20 and ma20 > ma50:
        return "UP"
    elif price < ma20 and ma20 < ma50:
        return "DOWN"
    else:
        return "SIDEWAYS"

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

def ask_ai_confirmation(df, current_price, signal_type, trend):
    """Claude AI 최종 확인"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return True

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        recent = df.tail(5)[['close', 'volume', 'rsi', 'bb_pct']].to_string()

        prompt = f"""
당신은 코인 전문 트레이더입니다.
현재 '{signal_type}' 신호 발생. 시장 추세: {trend}
최근 데이터:
{recent}
현재가: {current_price}원

추세와 지표를 종합해서 판단하세요.
반드시 JSON만 답하세요: {{"decision": "BUY", "reason": "이유"}}
"""
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        result = json.loads(response.content[0].text)
        print(f"  └ 🤖 AI: {result['decision']} - {result.get('reason', '')}")
        return result['decision'] == signal_type

    except ImportError:
        return True
    except Exception as e:
        print(f"  └ ⚠️ AI 확인 실패: {e}")
        return True

def check_signal(df, current_price, avg_buy_price=None, df_1h=None, peak_price=0):
    """
    매매 신호 판단

    매수: 3개 조건 중 2개 이상 충족
        조건1. RSI <= 40
        조건2. BB% <= 0.3
        조건3. 추세가 DOWN이 아님

    매도: 3개 조건 중 2개 이상 충족
        조건1. RSI >= 60
        조건2. BB% >= 0.7
        조건3. 추세가 UP이 아님

    즉시 매수: RSI <= 30 (극단 과매도)
    즉시 매도: RSI >= 70 (극단 과매수)
    """
    df = calculate_indicators(df)
    curr = df.iloc[-1]
    rsi = curr['rsi']
    bb_pct = curr['bb_pct']

    # 추세 파악
    trend = get_market_trend(df_1h)

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

        trend_icon = "📈" if trend == "UP" else ("📉" if trend == "DOWN" else "➡️")
        print(f"  └ RSI: {rsi:.1f}{rsi_icon} | BB%: {bb_pct:.2f} | 추세: {trend}{trend_icon}")

    if pd.isna(rsi) or pd.isna(bb_pct):
        print(f"  └ ⚠️ 지표 계산 중 (데이터 부족), HOLD")
        return "HOLD"

    # ✅ 1순위 - 트레일링 스탑
    if avg_buy_price and peak_price > 0:
        if check_trailing_stop(current_price, avg_buy_price, peak_price):
            return "SELL"

    # ✅ 2순위 - 손절/익절
    if avg_buy_price:
        profit_rate = (current_price - avg_buy_price) / avg_buy_price
        if profit_rate <= STOP_LOSS:
            print(f"  └ 🔴 손절! {profit_rate*100:.2f}%")
            return "SELL"
        if profit_rate >= TAKE_PROFIT_BASE:
            print(f"  └ 🟢 익절! {profit_rate*100:.2f}%")
            return "SELL"

    # ✅ 3순위 - 극단값 즉시 매수/매도
    if rsi <= RSI_EXTREME_LOW:
        print(f"  └ 💡 극단 과매도 즉시 매수! RSI: {rsi:.1f}")
        return "BUY"

    if rsi >= RSI_EXTREME_HIGH:
        print(f"  └ 💡 극단 과매수 즉시 매도! RSI: {rsi:.1f}")
        return "SELL"

    # ✅ 4순위 - 3개 중 2개 조건 충족 매수
    buy_score = 0
    buy_reasons = []

    if rsi <= RSI_OVERSOLD:
        buy_score += 1
        buy_reasons.append(f"RSI({rsi:.1f})")
    if bb_pct <= BB_LOWER_THRESHOLD:
        buy_score += 1
        buy_reasons.append(f"BB하단({bb_pct:.2f})")
    if trend != "DOWN":
        buy_score += 1
        buy_reasons.append(f"추세({trend})")

    if buy_score >= 2:
        print(f"  └ 💡 매수 신호! [{' + '.join(buy_reasons)}] ({buy_score}/3)")
        if ask_ai_confirmation(df, current_price, "BUY", trend):
            return "BUY"
        else:
            print(f"  └ 🛑 AI 진입 취소")
            return "HOLD"

    # ✅ 5순위 - 3개 중 2개 조건 충족 매도
    sell_score = 0
    sell_reasons = []

    if rsi >= RSI_OVERBOUGHT:
        sell_score += 1
        sell_reasons.append(f"RSI({rsi:.1f})")
    if bb_pct >= BB_UPPER_THRESHOLD:
        sell_score += 1
        sell_reasons.append(f"BB상단({bb_pct:.2f})")
    if trend != "UP":
        sell_score += 1
        sell_reasons.append(f"추세({trend})")

    if sell_score >= 2:
        print(f"  └ 💡 매도 신호! [{' + '.join(sell_reasons)}] ({sell_score}/3)")
        return "SELL"

    # 매수/매도 점수 출력 (디버깅용)
    print(f"  └ 매수점수: {buy_score}/3 | 매도점수: {sell_score}/3 → HOLD")
    return "HOLD"


if __name__ == "__main__":
    from collector import get_ohlcv, get_ohlcv_1h, get_current_price

    print("=" * 55)
    print("   RSI + 볼린저밴드 전략 테스트 (3중 2 조건)")
    print("=" * 55)

    for symbol in ["BTC", "XRP"]:
        print(f"\n▶ [{symbol}]")
        df = get_ohlcv(symbol)
        df_1h = get_ohlcv_1h(symbol)
        price = get_current_price(symbol)

        if df is not None and price:
            df_calc = calculate_indicators(df)
            curr = df_calc.iloc[-1]
            trend = get_market_trend(df_1h)

            print(f"  현재가:  {price:,.0f}원")
            print(f"  RSI:     {curr['rsi']:.1f}")
            print(f"  BB%:     {curr['bb_pct']:.2f}")
            print(f"  BB상단:  {curr['bb_upper']:,.0f}원")
            print(f"  BB하단:  {curr['bb_lower']:,.0f}원")
            print(f"  추세:    {trend}")
            signal = check_signal(df, price, df_1h=df_1h)
            print(f"  신호:    {signal}")