# regime.py - BTC 일봉 레짐 감지 (하이브리드 v4.1)
"""
BTC 일봉 EMA20 히스테리시스 레짐:
- DOWN → UP: 종가 > EMA20 × (1+밴드)
- UP → DOWN: 종가 < EMA20 × (1-밴드) 가 확인일수 연속

검증 (140일, 2026-02-20 ~ 07-09, 강세+약세 풀사이클):
- 하이브리드 (UP=코어보유 / DOWN=v4.0스윙): +6.85% (연환산 +17.9%)
- vs B&H BTC/ETH -8.7% / v4.0 단독 +1.4% / 레짐보유 단독 +1.9%
- 레짐 전환 이력: 3/12↑ 3/28↓ 4/5↑ 5/18↓ 7/4↑ (총 5회, 휩쏘 억제 확인)
"""
import pandas as pd

from config import (
    REGIME_SYMBOL, REGIME_EMA_SPAN, REGIME_BAND, REGIME_CONFIRM_DAYS,
)


def compute_regime_series(close_daily: pd.Series) -> pd.Series:
    """일봉 종가 시리즈 → 레짐 시리즈 (stateless: 매번 처음부터 재계산, 상태파일 불필요)"""
    ema = close_daily.ewm(span=REGIME_EMA_SPAN, adjust=False).mean()
    state, below = "DOWN", 0
    states = []
    for i in range(len(close_daily)):
        if i >= REGIME_EMA_SPAN:
            px, ev = close_daily.iloc[i], ema.iloc[i]
            if state == "DOWN":
                if px > ev * (1 + REGIME_BAND):
                    state, below = "UP", 0
            else:
                if px < ev * (1 - REGIME_BAND):
                    below += 1
                    if below >= REGIME_CONFIRM_DAYS:
                        state, below = "DOWN", 0
                else:
                    below = 0
        states.append(state)
    return pd.Series(states, index=close_daily.index)


def get_market_regime(df_1h: pd.DataFrame = None) -> str:
    """
    현재 시장 레짐 반환 ("UP" / "DOWN")
    - df_1h 미지정 시 collector에서 BTC 1H 데이터 로드
    - 마감된 일봉만 사용 (진행 중인 오늘 봉 제외 → 백테스트와 동일 기준)
    - 데이터 부족 시 보수적으로 "DOWN" (코어 매수 안 함)
    """
    if df_1h is None:
        from collector import get_swing_data
        df_1h = get_swing_data(REGIME_SYMBOL)
    if df_1h is None or len(df_1h) < REGIME_EMA_SPAN * 20:
        return "DOWN"

    df = df_1h.copy()
    df["time"] = pd.to_datetime(df["time"])
    daily = df.set_index("time")["close"].resample("1D").last().dropna()

    # 진행 중인 오늘 봉 제외 (마감 봉 기준 판단)
    today = pd.Timestamp.now().normalize()
    daily = daily[daily.index < today]

    if len(daily) < REGIME_EMA_SPAN + REGIME_CONFIRM_DAYS + 3:
        return "DOWN"

    return compute_regime_series(daily).iloc[-1]


if __name__ == "__main__":
    print("=" * 60)
    print("   레짐 감지 테스트 (BTC 일봉 EMA{} 밴드 ±{}%)".format(
        REGIME_EMA_SPAN, REGIME_BAND * 100))
    print("=" * 60)
    from collector import get_swing_data
    df = get_swing_data(REGIME_SYMBOL)
    if df is not None:
        d = df.copy()
        d["time"] = pd.to_datetime(d["time"])
        daily = d.set_index("time")["close"].resample("1D").last().dropna()
        series = compute_regime_series(daily)
        flips = series[series != series.shift(1)].iloc[1:]
        print("전환 이력:", " → ".join(f"{t.strftime('%m/%d')}:{v}" for t, v in flips.items()))
        print(f"\n현재 레짐: {get_market_regime(df)}")
