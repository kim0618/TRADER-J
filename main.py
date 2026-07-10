# main.py - Swing Trading v3.0 Live
"""
1H 스윙 라이브 트레이딩 루프
- CSV (data_fetcher 유지) + API 현재가
- 1H 봉 close 시 신호 체크, 매 사이클마다 보호 손절 체크
- 종목당 자본 25%, 최대 4종목 동시
"""
import time
import logging
import os
import builtins
from datetime import datetime, timedelta

import pandas as pd

from config import (
    INTERVAL_SECONDS, LOG_PATH, TOP_TICKER_LIMIT,
    MAX_BUY_COUNT, STOP_LOSS, TRAILING_STOP_TRIGGER,
    TRAILING_STOP_DROP, INITIAL_BALANCE,
    ATR_STOP_MULTIPLIER, ATR_STOP_MIN, ATR_STOP_MAX,
    MIN_HOLD_MINUTES, STRATEGY_SELL_ENABLED,
    TICKER_REFRESH_CYCLES,
    TIME_STOP_HOURS, TIME_STOP_THRESHOLD,
    BACKTEST_MIN_1H_BARS,
    REGIME_ENABLED, CORE_TICKERS,
)
from collector import get_current_price, get_swing_data, get_smart_tickers, get_btc_trend
from strategy import check_signal, get_atr, get_daily_trend
from regime import get_market_regime
from paper_trader import PortfolioManager

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

_original_print = builtins.print
def print(*args, **kwargs):
    _original_print(*args, **kwargs)
    logging.info(" ".join(str(a) for a in args))
builtins.print = print


def save_cycle_info(cycle, tickers, start_time=None, regime="?"):
    """대시보드용 사이클 정보 저장"""
    import json
    os.makedirs("data", exist_ok=True)
    running_hours = round((datetime.now() - start_time).total_seconds() / 3600, 2) if start_time else 0
    with open("data/cycle_info.json", "w") as f:
        json.dump({
            "cycle": cycle,
            "tickers": tickers,
            "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S") if start_time else "",
            "running_hours": running_hours,
            "strategy": "hybrid_v4.1",
            "regime": regime,
        }, f, ensure_ascii=False)


def get_all_active_tickers(pm, top_tickers):
    """관리 대상: 보유 포지션 + 신규 후보"""
    positions = pm.portfolio["positions"]
    active = [s for s, p in positions.items() if p.get("quantity", 0) > 0]
    return list(dict.fromkeys(active + top_tickers))


def is_new_1h_bar(df_1h, last_seen_time):
    """1H 봉이 새로 마감됐는지 확인"""
    if df_1h is None or len(df_1h) == 0:
        return False, last_seen_time
    latest = df_1h["time"].iloc[-1]
    if last_seen_time is None or latest > last_seen_time:
        return True, latest
    return False, last_seen_time


def check_sell_conditions(pm, symbol, price, df_1h):
    """
    매도 조건 (스윙 v3.0):
    1. 강제 청산 (FORCE_SELL_HOURS)
    2. 트레일링 스탑 (+7% 트리거, -3% drop)
    3. ATR 동적 손절
    4. 횡보 정리 (TIME_STOP_HOURS 이상 + 수익 ±2% 이내)
    5. 동적 익절
    """
    pos = pm.portfolio["positions"].get(symbol)
    if not pos or pos.get("quantity", 0) <= 0:
        return None

    avg = pos.get("avg_buy_price", 0)
    if avg <= 0:
        return None

    peak = pm.get_peak_price(symbol)
    profit_rate = (price - avg) / avg
    hours = pm.get_holding_hours(symbol)
    take_profit = pm.get_dynamic_take_profit(symbol)

    # 1) 강제 청산
    if pm.is_force_sell(symbol):
        print(f"  └ ⏰ 강제 청산 ({hours:.1f}h 보유)")
        return ("sell_all", "강제청산")

    # 2) 트레일링 스탑
    if peak > 0:
        peak_profit = (peak - avg) / avg
        drop_from_peak = (price - peak) / peak
        if peak_profit >= TRAILING_STOP_TRIGGER and drop_from_peak <= -TRAILING_STOP_DROP:
            print(f"  └ 🎯 트레일링 스탑 (고점:{peak:,.0f} 현재:{price:,.0f})")
            return ("sell_all", "트레일링스탑")

    # 3) ATR 동적 손절 (v4.0: 백테스트와 동일하게 4H ATR 기준)
    atr_stop = STOP_LOSS
    from strategy import _resample_to_4h
    df_4h = _resample_to_4h(df_1h)
    atr = get_atr(df_4h if df_4h is not None and len(df_4h) >= 20 else df_1h)
    if atr and price > 0:
        atr_pct = -(ATR_STOP_MULTIPLIER * atr) / price
        atr_stop = max(ATR_STOP_MAX, min(ATR_STOP_MIN, atr_pct))
    if profit_rate <= atr_stop:
        print(f"  └ 🔴 손절 ({profit_rate*100:.2f}%, ATR기준:{atr_stop*100:.1f}%)")
        return ("sell_all", "손절")

    # 4) 횡보 정리 (수익 ±2% 이내)
    if hours >= TIME_STOP_HOURS and abs(profit_rate) <= TIME_STOP_THRESHOLD:
        print(f"  └ ⏳ 횡보 정리 ({hours:.1f}h, {profit_rate*100:+.2f}%)")
        return ("sell_all", f"횡보정리({hours:.0f}h)")

    # 5) 동적 익절
    if profit_rate >= take_profit:
        print(f"  └ 🟢 익절 ({profit_rate*100:.2f}%, 기준:+{take_profit*100:.1f}%)")
        return ("sell_all", f"익절({hours:.0f}h)")

    return None


def get_price_with_retry(ticker, retries=3):
    for attempt in range(retries):
        p = get_current_price(ticker)
        if p:
            return p
        if attempt < retries - 1:
            time.sleep(1)
    return None


def run():
    print("\n" + "=" * 60)
    print("   🤖 코인 자동매매 시뮬레이터 (Hybrid v4.1)")
    print(f"   레짐 UP=코어 보유({'/'.join(CORE_TICKERS)}) | DOWN=v4.0 스윙")
    print(f"   주기: {INTERVAL_SECONDS}초 | {TICKER_REFRESH_CYCLES} 사이클마다 종목 재선정")
    print("=" * 60)
    print("   종료: Ctrl+C")
    print("=" * 60 + "\n")

    pm = PortfolioManager()
    top_tickers = []
    cycle = 0
    START_TIME = datetime.now()
    last_1h_bar_time = {}  # 종목별 마지막 1H 봉 시간

    while True:
        try:
            cycle += 1
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now_str}] 🔄 사이클 {cycle}")

            # 0) 시장 레짐 판단 (v4.1 하이브리드 핵심)
            regime = get_market_regime() if REGIME_ENABLED else "DOWN"
            regime_icon = "📈" if regime == "UP" else "📉"
            print(f"  └ {regime_icon} 시장 레짐: {regime} "
                  f"({'코어 보유 모드' if regime == 'UP' else 'v4.0 스윙 모드'})")

            # 1) 종목 재선정 (첫 사이클 + 주기마다) - DOWN 레짐에서만 의미 있음
            if cycle == 1 or cycle % TICKER_REFRESH_CYCLES == 0:
                top_tickers = get_smart_tickers(limit=TOP_TICKER_LIMIT)
                print(f"  └ 신규 종목 풀: {top_tickers}")

            save_cycle_info(cycle, top_tickers, START_TIME, regime)

            # 2) 관리 대상 종목 (보유 + 신규 후보 + 코어)
            all_tickers = get_all_active_tickers(pm, top_tickers)
            if REGIME_ENABLED and regime == "UP":
                all_tickers = list(dict.fromkeys(all_tickers + CORE_TICKERS))

            # 3) 현재가 수집
            prices = {}
            for t in all_tickers:
                p = get_price_with_retry(t)
                if p:
                    prices[t] = p

            # 4) 글로벌 손절 (전체 포트폴리오 -10% 도달 시)
            if pm.global_stop_loss_sell(prices):
                print(f"\n  🚨 글로벌 손절 발동, 모든 포지션 청산")
                time.sleep(INTERVAL_SECONDS)
                continue

            # 4.5) 코어 포지션 관리 (v4.1 하이브리드)
            #   UP:  BTC/ETH 코어 매수 (미보유 시)
            #   DOWN: 코어 청산 → v4.0 스윙 모드로 전환
            if REGIME_ENABLED:
                for core in CORE_TICKERS:
                    core_price = prices.get(core) or get_price_with_retry(core)
                    if not core_price:
                        continue
                    prices[core] = core_price
                    core_avg = pm.get_avg_buy_price(core)
                    if regime == "UP" and core_avg is None:
                        print(f"  └ 🏦 [{core}] 코어 매수 (레짐 UP)")
                        pm.buy(core, core_price)
                    elif regime == "DOWN" and core_avg is not None:
                        profit = (core_price - core_avg) / core_avg * 100
                        print(f"  └ 🏦 [{core}] 코어 청산 (레짐 DOWN, {profit:+.2f}%)")
                        pm.sell_all(core, core_price, reason="레짐DOWN")

            # 5) 종목별 처리
            for ticker in all_tickers:
                price = prices.get(ticker)
                if price is None:
                    print(f"  └ [{ticker}] 가격 조회 실패, 스킵")
                    continue

                df_1h = get_swing_data(ticker)
                if df_1h is None or len(df_1h) < BACKTEST_MIN_1H_BARS:
                    print(f"  └ [{ticker}] 1H 데이터 부족, 스킵")
                    continue

                is_active = ticker in top_tickers
                pm.update_peak_price(ticker, price)
                avg = pm.get_avg_buy_price(ticker)
                hours = pm.get_holding_hours(ticker)
                tp = pm.get_dynamic_take_profit(ticker)

                # v4.1: UP 레짐의 코어 포지션은 매도 규칙 면제 (레짐 전환만이 출구)
                is_core_hold = (REGIME_ENABLED and regime == "UP"
                                and ticker in CORE_TICKERS and avg is not None)

                # 상태 출력
                if avg:
                    profit_rate = (price - avg) / avg * 100
                    tag = "🏦 코어 보유" if is_core_hold else ("보유 중" if is_active else "잔여 보유")
                    print(f"\n  ── [{ticker}] {tag} ──")
                    print(f"  └ 현재가: {price:,.0f}원 | 수익: {profit_rate:+.2f}% | "
                          f"보유: {hours:.1f}h" + ("" if is_core_hold else f" | 익절기준: +{tp*100:.1f}%"))
                else:
                    print(f"\n  ── [{ticker}] 분석 ──")
                    print(f"  └ 현재가: {price:,.0f}원")

                # 코어 포지션: 레짐 전환 전까지 그대로 보유 (신호/손절 체크 스킵)
                if is_core_hold:
                    continue

                # 5-1) 보호 손절 체크 (현재가 기준, 즉시 반응)
                if avg:
                    sell_result = check_sell_conditions(pm, ticker, price, df_1h)
                    if sell_result:
                        action, reason = sell_result
                        pm.sell_all(ticker, price, reason=reason)
                        continue

                # 5-2) 새 1H 봉 마감 시에만 신호 체크
                new_bar, latest_t = is_new_1h_bar(df_1h, last_1h_bar_time.get(ticker))
                last_1h_bar_time[ticker] = latest_t

                if not new_bar:
                    if avg is None and is_active:
                        # 신호 평가는 안 하지만 상태 표시
                        daily_t = get_daily_trend(df_1h)
                        print(f"  └ 1D 추세: {daily_t} (다음 1H 봉 close 대기 중)")
                    continue

                # 신호 계산 (1H 봉 close 후)
                signal = check_signal(df_1h, price, avg, df_1h, pm.get_peak_price(ticker))

                # 5-3) 전략 매도 신호 (보유 중 + STRATEGY_SELL_ENABLED)
                if avg and signal == "SELL" and STRATEGY_SELL_ENABLED:
                    min_hold = MIN_HOLD_MINUTES / 60
                    if hours < min_hold:
                        print(f"  └ ⏳ 최소보유시간 미달 ({hours*60:.0f}분 / {MIN_HOLD_MINUTES}분 필요)")
                    else:
                        pm.sell_all(ticker, price, reason="전략매도")
                        continue

                # 5-4) 매수 신호 (active + 미보유)
                # v4.1: v4.0 스윙 진입은 DOWN 레짐에서만 (검증: DOWN 4건 PF 8.39 vs UP구간 PF 0.72)
                if is_active and avg is None and signal == "BUY":
                    if REGIME_ENABLED and regime == "UP":
                        print(f"  └ 🚫 레짐 UP → 스윙 진입 차단 (코어 보유 모드)")
                        continue
                    active_count = sum(
                        1 for p in pm.portfolio["positions"].values()
                        if p.get("quantity", 0) > 0
                    )
                    if active_count >= TOP_TICKER_LIMIT:
                        print(f"  └ ⚠️ 종목 한도 도달 ({TOP_TICKER_LIMIT}종 보유 중)")
                    else:
                        # BTC 급락 필터
                        btc_trend, btc_change = get_btc_trend()
                        if btc_trend == "DOWN" and btc_change <= -3.0:
                            print(f"  └ 🚫 BTC 급락 차단 ({btc_change:+.2f}%)")
                        else:
                            print(f"  └ 💡 매수 신호 → 진입")
                            pm.buy(ticker, price)

            # 6) 정기 현황 (10 사이클마다)
            if cycle % 10 == 0:
                pm.print_status(prices)

            print(f"\n  └ ⏱ {INTERVAL_SECONDS}초 후 다음 사이클")
            time.sleep(INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\n" + "=" * 60)
            print("   종료 중...")
            all_tickers = get_all_active_tickers(pm, top_tickers)
            prices = {}
            for t in all_tickers:
                p = get_current_price(t)
                if p:
                    prices[t] = p
            pm.print_status(prices)
            print("\n종료 완료.")
            break

        except Exception as e:
            logging.error(f"오류: {e}")
            print(f"  └ 오류: {e} | 10초 후 재시도")
            time.sleep(10)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        print("\n" + "=" * 60)
        print("   📋 포트폴리오 초기화")
        print("=" * 60)
        PortfolioManager.reset_portfolio()
    else:
        run()
