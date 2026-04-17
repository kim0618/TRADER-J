# main.py
import time
import logging
import os
import builtins
from datetime import datetime
from config import (
    INTERVAL_SECONDS, LOG_PATH, TOP_TICKER_LIMIT,
    MAX_BUY_COUNT, STOP_LOSS, TRAILING_STOP_TRIGGER,
    TRAILING_STOP_DROP, REPLACE_LOSS_THRESHOLD, SELL_COOLDOWN_MINUTES,
    TIME_STOP_HOURS, TIME_STOP_THRESHOLD, INITIAL_BALANCE,
    ATR_STOP_MULTIPLIER, ATR_STOP_MIN, ATR_STOP_MAX,
    MIN_HOLD_MINUTES, STRATEGY_SELL_ENABLED,
    TICKER_REFRESH_CYCLES, REPLACE_HOLD_HOURS,
)
from collector import get_current_price, get_ohlcv, get_ohlcv_1h, get_smart_tickers, get_btc_trend
from strategy import check_signal, calculate_indicators, get_market_trend, get_atr
from paper_trader import PortfolioManager

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

_original_print = builtins.print
def print(*args, **kwargs):
    _original_print(*args, **kwargs)
    logging.info(" ".join(str(a) for a in args))

builtins.print = print

def save_cycle_info(cycle, tickers, start_time=None):
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
        }, f, ensure_ascii=False)

def get_all_active_tickers(pm, top_tickers):
    """
    실제로 관리해야 할 전체 종목 반환
    - 현재 포지션 보유 중인 종목 (top_tickers에 없어도 포함)
    - top_tickers 신규 종목
    """
    positions = pm.portfolio["positions"]
    active = [s for s, p in positions.items() if p.get("quantity", 0) > 0]
    all_tickers = list(dict.fromkeys(active + top_tickers))
    return all_tickers

def check_sell_conditions(pm, symbol, price, df_5m, df_1h):
    """
    매도 조건 종합 체크
    1. 강제 청산 (36시간)
    2. 트레일링 스탑
    3. ATR 기반 동적 손절
    4. 시간 기반 횡보 정리 (손실 포지션만, 수익 중 면제)
    5. 동적 익절
    6. 전략 매도 신호 (호출 쪽에서 처리)
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

    # 1. 강제 청산 (36시간)
    if pm.is_force_sell(symbol):
        print(f"  └ ⏰ 강제 청산! {hours:.1f}시간 보유")
        return ("sell_all", "강제청산")

    # 2. 트레일링 스탑
    if peak > 0:
        peak_profit = (peak - avg) / avg
        drop_from_peak = (price - peak) / peak
        if peak_profit >= TRAILING_STOP_TRIGGER and drop_from_peak <= -TRAILING_STOP_DROP:
            print(f"  └ 🎯 트레일링 스탑! 고점: {peak:,.0f} → 현재: {price:,.0f}")
            return ("sell_all", "트레일링스탑")

    # 3. ATR 기반 동적 손절
    atr_stop = STOP_LOSS  # 폴백 기본값
    atr = get_atr(df_5m)
    if atr and price > 0:
        atr_pct = -(ATR_STOP_MULTIPLIER * atr) / price
        atr_stop = max(ATR_STOP_MAX, min(ATR_STOP_MIN, atr_pct))

    if profit_rate <= atr_stop:
        print(f"  └ 🔴 손절! {profit_rate*100:.2f}% (ATR기준: {atr_stop*100:.1f}%)")
        return ("sell_all", "손절")

    # 4. 시간 기반 횡보 정리 (수익 중인 포지션은 면제)
    if hours >= TIME_STOP_HOURS and profit_rate <= 0 and abs(profit_rate) <= TIME_STOP_THRESHOLD:
        print(f"  └ ⏳ 횡보 정리! {hours:.1f}h 보유, 수익률 {profit_rate*100:+.2f}% (±{TIME_STOP_THRESHOLD*100:.1f}% 이내)")
        return ("sell_all", f"횡보정리({hours:.0f}h)")

    # 5. 동적 익절
    if profit_rate >= take_profit:
        print(f"  └ 🟢 익절! {profit_rate*100:.2f}% (기준: +{take_profit*100:.1f}%)")
        return ("sell_all", f"익절({hours:.0f}h)")

    return None

def get_price_with_retry(ticker, retries=3):
    """API 실패 시 최대 3회 재시도"""
    for attempt in range(retries):
        p = get_current_price(ticker)
        if p:
            return p
        if attempt < retries - 1:
            time.sleep(1)
    return None

def handle_ticker_refresh(pm, top_tickers, replacing_tickers):
    """
    종목 재선정 시 교체 처리
    - 손실 -3% 이하: 즉시 청산 후 교체
    - 수익 중 or 손실 -3% 미만: 3시간 보유 후 교체
    - 교체 대기 중인 종목은 replacing_tickers에 추가 → 추가 매수 차단
    """
    positions = pm.portfolio["positions"]
    active = [s for s, p in positions.items() if p.get("quantity", 0) > 0]

    replaced = []
    for symbol in active:
        if symbol not in top_tickers:
            price = get_current_price(symbol)
            if not price:
                continue

            hours = pm.get_holding_hours(symbol)
            avg = pm.get_avg_buy_price(symbol)
            profit_rate = (price - avg) / avg if avg else 0

            if profit_rate <= REPLACE_LOSS_THRESHOLD:
                print(f"  └ 🔄 [{symbol}] 즉시 교체 청산! "
                      f"손실 {profit_rate*100:.2f}% (기준: {REPLACE_LOSS_THRESHOLD*100:.0f}%)")
                pm.sell_all(symbol, price, reason="종목교체(손실)")
                replaced.append(symbol)
                replacing_tickers.discard(symbol)
            elif hours >= REPLACE_HOLD_HOURS:
                print(f"  └ 🔄 [{symbol}] 교체 청산 "
                      f"({hours:.1f}h 보유, {profit_rate*100:+.2f}%)")
                pm.sell_all(symbol, price, reason="종목교체(시간)")
                replaced.append(symbol)
                replacing_tickers.discard(symbol)
            else:
                print(f"  └ ⏳ [{symbol}] 교체 대기 "
                      f"({profit_rate*100:+.2f}% | {hours:.1f}h / {REPLACE_HOLD_HOURS}h) → 추가 매수 차단")
                replacing_tickers.add(symbol)

    return replaced

def run():
    print("\n" + "="*55)
    print("   🤖 코인 자동매매 시뮬레이터 시작!")
    print(f"   최대 {TOP_TICKER_LIMIT}개 종목 | 통합 포트폴리오")
    print(f"   동적 익절 | {TIME_STOP_HOURS}h 횡보정리 | 트레일링스탑 | 글로벌손절")
    print("="*55)
    print("   종료하려면 Ctrl+C 를 누르세요")
    print("="*55 + "\n")

    pm = PortfolioManager()
    top_tickers = []
    replacing_tickers = set()
    cycle = 0
    TICKER_REFRESH = TICKER_REFRESH_CYCLES
    START_TIME = datetime.now()

    while True:
        try:
            cycle += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now}] 🔄 {cycle}번째 체크")

            # 1. 종목 재선정 (1번째 or 10사이클마다)
            if cycle == 1 or cycle % TICKER_REFRESH == 0:
                top_tickers = get_smart_tickers(limit=TOP_TICKER_LIMIT)
                print(f"  └ 신규 선정 종목: {top_tickers}")

                handle_ticker_refresh(pm, top_tickers, replacing_tickers)

            save_cycle_info(cycle, top_tickers, START_TIME)

            # 보유 중인 모든 종목 + 신규 종목 합쳐서 관리
            all_tickers = get_all_active_tickers(pm, top_tickers)

            # 현재가 수집 (전체 종목) - 재시도 로직 적용
            prices = {}
            for ticker in all_tickers:
                p = get_price_with_retry(ticker)
                if p:
                    prices[ticker] = p

            # 글로벌 포트폴리오 손절 체크
            if pm.global_stop_loss_sell(prices):
                print(f"\n  🚨 글로벌 손절 발동으로 모든 포지션 청산됨")
                print(f"  🚨 {INTERVAL_SECONDS * 5}초 후 재시작...")
                time.sleep(INTERVAL_SECONDS * 5)
                continue

            # 2. 종목별 매매 (보유 중인 모든 종목 관리)
            for ticker in all_tickers:
                price = prices.get(ticker)
                df_5m = get_ohlcv(ticker)
                df_1h = get_ohlcv_1h(ticker)

                if price is None or df_5m is None:
                    print(f"  └ [{ticker}] 데이터 수집 실패, 스킵")
                    continue

                is_active_ticker = ticker in top_tickers

                # 고점 업데이트
                pm.update_peak_price(ticker, price)

                avg = pm.get_avg_buy_price(ticker)
                buy_count = pm.get_buy_count(ticker)
                hours = pm.get_holding_hours(ticker)
                take_profit = pm.get_dynamic_take_profit(ticker)

                # 잔여 보유 종목은 간략하게 표시
                if not is_active_ticker:
                    profit_rate = (price - avg) / avg * 100 if avg else 0
                    print(f"\n  ── [{ticker}] 잔여 보유 (교체 대기) ──")
                    print(f"  └ 현재가: {price:,.0f}원 | 수익률: {profit_rate:+.2f}% | 보유: {hours:.1f}h")
                else:
                    print(f"\n  ── [{ticker}] 분석 중 ──")
                    print(f"  └ 현재가: {price:,.0f}원 | 분할매수: {buy_count}/{MAX_BUY_COUNT}회")
                    if avg:
                        profit_rate = (price - avg) / avg * 100
                        print(f"  └ 평균단가: {avg:,.0f}원 | 수익률: {profit_rate:+.2f}% | "
                              f"보유: {hours:.1f}h | 익절기준: +{take_profit*100:.1f}%")

                # signal 한 번만 계산 (매도+매수 공유)
                signal = None
                if df_5m is not None:
                    signal = check_signal(df_5m, price, avg, df_1h,
                                          pm.get_peak_price(ticker))

                # 매도 조건 체크 (보유 중인 모든 종목)
                if avg:
                    sell_result = check_sell_conditions(
                        pm, ticker, price, df_5m, df_1h
                    )
                    # 전략 매도 신호 (STRATEGY_SELL_ENABLED가 True일 때만)
                    if sell_result is None and signal == "SELL" and STRATEGY_SELL_ENABLED:
                        profit_pct = (price - avg) / avg
                        min_hold_hours = MIN_HOLD_MINUTES / 60
                        if hours < min_hold_hours:
                            print(f"  └ ⏳ 최소보유시간 미달 ({hours*60:.0f}분 / {MIN_HOLD_MINUTES}분 필요)")
                        elif hours < 0.5:
                            print(f"  └ ⏳ 전략매도 대기 중 ({hours*60:.0f}분 / 30분 보유 필요)")
                        elif profit_pct < 0:
                            print(f"  └ ⏳ 전략매도 신호지만 손실 중 ({profit_pct*100:+.2f}%), 기계적 출구에 위임")
                        else:
                            sell_result = ("sell_all", "전략매도")

                    if sell_result:
                        action, reason = sell_result
                        if action == "sell_all":
                            pm.sell_all(ticker, price, reason=reason)
                        else:
                            pm.sell(ticker, price, reason=reason)
                        continue

                # 매수 조건 체크 (top_tickers에 있는 종목만, 교체 대기 종목 제외)
                if is_active_ticker and ticker not in replacing_tickers:
                    active_count = len([
                        s for s, p in pm.portfolio["positions"].items()
                        if p.get("quantity", 0) > 0
                    ])

                    if buy_count < MAX_BUY_COUNT:
                        if active_count >= TOP_TICKER_LIMIT and buy_count == 0:
                            print(f"  └ ⚠️ 최대 종목수 도달 ({TOP_TICKER_LIMIT}개)")
                        else:
                            if signal == "BUY":
                                # BTC 추세 필터: BTC 급락 중이면 신규 매수 차단
                                btc_trend, btc_change = get_btc_trend()
                                if buy_count == 0 and btc_trend == "DOWN" and btc_change <= -2.0:
                                    print(f"  └ 🚫 BTC 급락 중 ({btc_change:+.2f}%), 신규 매수 차단")
                                else:
                                    print(f"  └ 매매 신호: {signal}")
                                    pm.buy(ticker, price)
                    else:
                        print(f"  └ 최대 분할매수 도달 ({MAX_BUY_COUNT}회)")

            # 3. 10사이클마다 전체 현황 출력
            if cycle % 10 == 0:
                pm.print_status(prices)

            print(f"\n  └ ⏱ {INTERVAL_SECONDS}초 후 다음 체크...")
            time.sleep(INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\n" + "="*55)
            print("   시뮬레이터 종료 중...")
            all_tickers = get_all_active_tickers(pm, top_tickers)
            prices = {}
            for ticker in all_tickers:
                p = get_current_price(ticker)
                if p:
                    prices[ticker] = p
            pm.print_status(prices)
            print("\n종료 완료!")
            break

        except Exception as e:
            logging.error(f"오류 발생: {e}")
            print(f"  └ 오류: {e}, 10초 후 재시도...")
            time.sleep(10)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        print("\n" + "="*55)
        print("   📋 포트폴리오 초기화")
        print("="*55)
        PortfolioManager.reset_portfolio()
    else:
        run()
