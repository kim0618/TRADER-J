# collector.py
import requests
import pandas as pd
import time
from config import CANDLE_INTERVAL, CANDLE_COUNT

BITHUMB_API_URL = "https://api.bithumb.com/public"

def get_all_tickers():
    """전체 종목 시세 데이터"""
    try:
        res = requests.get(f"{BITHUMB_API_URL}/ticker/ALL_KRW", timeout=5)
        data = res.json()
        if data["status"] == "0000":
            return {k: v for k, v in data["data"].items() if k != "date"}
    except Exception as e:
        print(f"[오류] 전체 시세 조회 실패: {e}")
    return {}

def get_btc_trend():
    """BTC 시장 전체 추세 확인"""
    try:
        res = requests.get(f"{BITHUMB_API_URL}/ticker/BTC_KRW", timeout=5)
        data = res.json()
        if data["status"] == "0000":
            change_rate = float(data["data"]["fluctate_rate_24H"])
            if change_rate >= 1.0:
                return "UP", change_rate
            elif change_rate <= -2.0:
                return "DOWN", change_rate
            else:
                return "SIDEWAYS", change_rate
    except:
        pass
    return "SIDEWAYS", 0.0

def get_rsi(coin):
    """
    RSI 빠르게 계산
    5분봉 50개로 계산
    """
    try:
        res = requests.get(
            f"{BITHUMB_API_URL}/candlestick/{coin}_KRW/5m",
            timeout=5
        )
        data = res.json()
        if data["status"] == "0000":
            closes = [float(c[2]) for c in data["data"][-50:]]
            if len(closes) < 15:
                return 50.0

            df = pd.Series(closes)
            delta = df.diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return round(float(rsi.iloc[-1]), 1)
    except:
        pass
    return 50.0

def get_smart_tickers(limit=2):
    """
    전략과 일치하는 종목 선정

    [핵심 원칙]
    RSI + BB 저점 매수 전략 사용 중
    → 선정 기준도 저점 종목으로 맞춰야 함

    [선정 기준]
    1. 거래금액 50억 이상 (유동성 확보)
    2. 급락 종목 제외 (-15% 이하)
    3. 급등 종목 제외 (+15% 이상) → 이미 고점
    4. RSI 계산해서 55 이하 종목만 (아직 안 오른 것)
    5. 52주 하단 50% 이내 (저점 구간)

    [점수 계산]
    RSI 낮을수록   +40점 (가장 중요)
    52주 하단 근처 +30점
    거래금액 많을수록 +20점
    변동률 적당히   +10점
    """
    try:
        tickers = get_all_tickers()
        if not tickers:
            return get_top_volume_tickers(limit)

        # BTC 시장 추세 확인
        btc_trend, btc_change = get_btc_trend()
        print(f"\n  🌍 BTC 시장 추세: {btc_trend} ({btc_change:+.2f}%)")

        # BTC 급락장이면 전체 매수 보류
        if btc_trend == "DOWN" and btc_change <= -3.0:
            print(f"  ⚠️ BTC 급락장 (-3% 이하), 안전 종목으로 대체")
            return get_top_volume_tickers(limit)

        candidates = []

        for coin, data in tickers.items():
            try:
                current_price = float(data.get("closing_price", 0))
                value_24h = float(data.get("acc_trade_value_24H", 0))
                change_rate = float(data.get("fluctate_rate_24H", 0))
                min_price = float(data.get("min_price", 0))
                max_price = float(data.get("max_price", 0))

                # 기본 데이터 필터
                if current_price <= 0 or value_24h <= 0:
                    continue

                # 필터 1 - 거래금액 50억 이상 (유동성)
                if value_24h < 5_000_000_000:
                    continue

                # 필터 2 - 급락 종목 제외 (상폐 위험)
                if change_rate < -15.0:
                    continue

                # 필터 3 - 급등 종목 제외 (이미 고점)
                if change_rate > 15.0:
                    continue

                # 필터 4 - 52주 위치 계산
                price_position = 0.5  # 기본값
                if max_price > min_price:
                    price_position = (current_price - min_price) / (max_price - min_price)

                # 52주 상단 70% 이상이면 제외 (과열)
                if price_position > 0.7:
                    continue

                candidates.append({
                    "coin": coin,
                    "price": current_price,
                    "change_rate": change_rate,
                    "value_24h": value_24h,
                    "price_position": price_position,
                })

            except:
                continue

        print(f"  🔍 1차 필터 통과: {len(candidates)}개 종목")

        if not candidates:
            print("  ⚠️ 조건 충족 종목 없음, 거래량 기준으로 대체")
            return get_top_volume_tickers(limit)

        # 거래금액 기준 상위 20개만 RSI 계산 (API 호출 최소화)
        candidates.sort(key=lambda x: x["value_24h"], reverse=True)
        top_candidates = candidates[:20]

        print(f"  📊 RSI 계산 중... (상위 20개)")
        for c in top_candidates:
            rsi = get_rsi(c["coin"])
            c["rsi"] = rsi
            time.sleep(0.1)  # API 과부하 방지

        # RSI 55 초과 종목 제외 (이미 오른 종목)
        top_candidates = [c for c in top_candidates if c["rsi"] <= 55]

        if not top_candidates:
            print("  ⚠️ RSI 조건 충족 종목 없음, 거래량 기준으로 대체")
            return get_top_volume_tickers(limit)

        # 종합 점수 계산
        import math
        for c in top_candidates:
            score = 0

            # 1. RSI 낮을수록 높은 점수 (40점)
            rsi = c["rsi"]
            if rsi <= 30:
                score += 40      # 극단 과매도
            elif rsi <= 35:
                score += 35
            elif rsi <= 40:
                score += 28
            elif rsi <= 45:
                score += 20
            elif rsi <= 50:
                score += 12
            else:
                score += 5       # RSI 50~55

            # 2. 52주 하단 근처일수록 높은 점수 (30점)
            pos = c["price_position"]
            if pos <= 0.1:
                score += 30      # 52주 최저점 근처
            elif pos <= 0.2:
                score += 25
            elif pos <= 0.3:
                score += 20
            elif pos <= 0.4:
                score += 15
            elif pos <= 0.5:
                score += 10
            else:
                score += 5

            # 3. 거래금액 (20점) - 유동성
            score += min(math.log10(c["value_24h"]) * 2, 20)

            # 4. 변동률 (10점) - 너무 많이 오르지 않은 것
            cr = c["change_rate"]
            if -3.0 <= cr <= 3.0:
                score += 10      # 보합 → 아직 움직임 없음
            elif -8.0 <= cr < -3.0:
                score += 7       # 소폭 하락 → 반등 기대
            elif 3.0 < cr <= 8.0:
                score += 5       # 소폭 상승
            else:
                score += 2       # 큰 변동

            c["score"] = round(score, 1)

        # 점수 높은 순 정렬
        top_candidates.sort(key=lambda x: x["score"], reverse=True)

        # 결과 출력
        print(f"\n  {'종목':>6} | {'RSI':>5} | {'52주위치':>8} | {'변동률':>7} | {'거래금액':>10} | {'점수':>6}")
        print("  " + "-"*60)
        for c in top_candidates[:7]:
            print(
                f"  {c['coin']:>6} | "
                f"{c['rsi']:>5.1f} | "
                f"{c['price_position']*100:>7.1f}% | "
                f"{c['change_rate']:>+6.2f}% | "
                f"{c['value_24h']/1e8:>8.1f}억 | "
                f"{c['score']:>5.1f}점"
            )

        selected = [c["coin"] for c in top_candidates[:limit]]

        # 부족하면 거래량으로 보완
        if len(selected) < limit:
            print(f"  ⚠️ 선정 종목 부족 ({len(selected)}개), 거래량 기준으로 보완")
            fallback = get_top_volume_tickers(limit)
            for t in fallback:
                if t not in selected:
                    selected.append(t)
                if len(selected) >= limit:
                    break

        print(f"\n  ✅ 최종 선정: {selected}")
        return selected[:limit]

    except Exception as e:
        print(f"[오류] 스마트 종목 선정 실패: {e}")
        return get_top_volume_tickers(limit)

def get_top_volume_tickers(limit=2):
    """거래량 기준 상위 종목 (폴백용)"""
    try:
        tickers = get_all_tickers()
        sorted_tickers = sorted(
            tickers.items(),
            key=lambda x: float(x[1].get('acc_trade_value_24H', 0)),
            reverse=True
        )
        return [item[0] for item in sorted_tickers[:limit]]
    except Exception as e:
        print(f"[오류] 거래량 상위 종목 조회 실패: {e}")
    return ["BTC", "XRP"]

def get_current_price(symbol="BTC"):
    """현재가 조회"""
    try:
        coin = symbol.split('_')[0]
        res = requests.get(
            f"{BITHUMB_API_URL}/ticker/{coin}_KRW",
            timeout=5
        )
        data = res.json()
        if data["status"] == "0000":
            return float(data["data"]["closing_price"])
    except Exception as e:
        print(f"[오류] {symbol} 현재가 조회 실패: {e}")
    return None

def get_ohlcv(symbol="BTC", interval=None, count=None):
    """캔들 데이터 조회"""
    if interval is None:
        interval = CANDLE_INTERVAL
    if count is None:
        count = CANDLE_COUNT
    try:
        coin = symbol.split('_')[0]
        res = requests.get(
            f"{BITHUMB_API_URL}/candlestick/{coin}_KRW/{interval}",
            timeout=5
        )
        data = res.json()
        if data["status"] == "0000":
            df = pd.DataFrame(
                data["data"],
                columns=["time", "open", "close", "high", "low", "volume"]
            )
            df = df.astype({
                "open": float, "close": float,
                "high": float, "low": float, "volume": float
            })
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            return df.tail(count)
    except Exception as e:
        print(f"[오류] {symbol} 캔들 데이터 조회 실패: {e}")
    return None

def get_ohlcv_1h(symbol="BTC"):
    """1시간봉 - 큰 추세 파악용"""
    return get_ohlcv(symbol, interval="1h", count=100)


if __name__ == "__main__":
    print("=" * 65)
    print("   전략 일치형 종목 선정 테스트 (RSI 저점 기준)")
    print("=" * 65)

    selected = get_smart_tickers(2)

    print(f"\n{'='*65}")
    print(f"  🎯 최종 선정 종목: {selected}")
    print(f"{'='*65}\n")

    for ticker in selected:
        price = get_current_price(ticker)
        rsi = get_rsi(ticker)
        if price:
            print(f"  [{ticker}] 현재가: {price:,.0f}원 | RSI: {rsi}")
