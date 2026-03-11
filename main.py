# main.py
import time
import logging
import os
from datetime import datetime
from config import (
    INTERVAL_SECONDS, LOG_PATH, TOP_TICKER_LIMIT,
    MAX_BUY_COUNT, STOP_LOSS, TRAILING_STOP_TRIGGER,
    TRAILING_STOP_DROP
)
from collector import get_current_price, get_ohlcv, get_ohlcv_1h, get_smart_tickers
from strategy import check_signal, calculate_indicators, get_market_trend
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

def save_cycle_info(cycle, tickers):
    """대시보드용 사이클 정보 저장"""
    import json
    os.makedirs("data", exist_ok=True)
    with open("data/cycle_info.json", "w") as f:
        json.dump({
            "cycle": cycle,
            "tickers": tickers,
            "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }, f, ensure_ascii=False)

def get_all_active_tickers(pm, top_tickers):
    """
    실제로 관리해야 할 전체 종목 반환
    - 현재 포지션 보유 중인 종목 (top_tickers에 없어도 포함)
    - top_tickers 신규 종목
    """
    positions = pm.portfolio["positions"]
    # 현재 보유 중인 종목
    active = [s for s, p in positions.items() if p.get("quantity", 0) > 0]
    # top_tickers와 합치기 (중복 제거)
    all_tickers = list(dict.fromkeys(active + top_tickers))
    return all_tickers

def check_sell_conditions(pm, symbol, price, df_5m, df_1h):
    """
    매도 조건 종합 체크
    1. 강제 청산 (48시간)
    2. 트레일링 스탑
    3. 손절
    4. 동적 익절
    5. 전략 매도 신호
    """
    pos = pm.portfolio["positions"].get(symbol)
    if not pos or pos.get("quantity", 0) <= 0:
        return None

    avg = pos["avg_buy_price"]
    peak = pm.get_peak_price(symbol)
    profit_rate = (price - avg) / avg
    hours = pm.get_holding_hours(symbol)
    take_profit = pm.get_dynamic_take_profit(symbol)

    # 1. 강제 청산 (48시간)
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

    # 3. 손절
    if profit_rate <= STOP_LOSS:
        print(f"  └ 🔴 손절! {profit_rate*100:.2f}%")
        return ("sell_all", "손절")

    # 4. 동적 익절
    if profit_rate >= take_profit:
        print(f"  └ 🟢 익절! {profit_rate*100:.2f}% (기준: +{take_profit*100:.1f}%)")
        return ("sell_all", f"익절({hours:.0f}h)")

    # 5. 전략 매도 신호
    avg_buy = pm.get_avg_buy_price(symbol)
    signal = check_signal(df_5m, price, avg_buy, df_1h, peak)
    if signal == "SELL":
        return ("sell_half", "전략매도")

    return None

REPLACE_LOSS_THRESHOLD = -0.01  # -1% 이하 손실 시 즉시 교체

def handle_ticker_refresh(pm, top_tickers):
    """
    종목 재선정 시 교체 처리
    - 손실 -1% 이하: 즉시 청산 후 교체
    - 수익 중 or 손실 -1% 미만: 3시간 보유 후 교체
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
                # 손실 -1% 이하 → 즉시 교체
                print(f"  └ 🔄 [{symbol}] 즉시 교체 청산! "
                      f"손실 {profit_rate*100:.2f}% (기준: -1%)")
                pm.sell_all(symbol, price, reason="종목교체(손실)")
                replaced.append(symbol)
            elif hours >= 3:
                # 수익 중이지만 3시간 이상 보유 → 교체
                print(f"  └ 🔄 [{symbol}] 교체 청산 "
                      f"({hours:.1f}h 보유, {profit_rate*100:+.2f}%)")
                pm.sell_all(symbol, price, reason="종목교체(시간)")
                replaced.append(symbol)
            else:
                # 수익 중 + 3시간 미만 → 대기
                print(f"  └ ⏳ [{symbol}] 교체 대기 "
                      f"({profit_rate*100:+.2f}% | {hours:.1f}h / 3h)")

    return replaced

def run():
    print("\n" + "="*55)
    print("   🤖 코인 자동매매 시뮬레이터 시작!")
    print(f"   최대 {TOP_TICKER_LIMIT}개 종목 | 통합 포트폴리오")
    print(f"   동적 익절 | 48시간 강제청산 | 트레일링스탑")
    print("="*55)
    print("   종료하려면 Ctrl+C 를 누르세요")
    print("="*55 + "\n")

    pm = PortfolioManager()
    top_tickers = []
    cycle = 0
    TICKER_REFRESH = 10

    while True:
        try:
            cycle += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now}] 🔄 {cycle}번째 체크")

            # 1. 종목 재선정 (1번째 or 10사이클마다)
            if cycle == 1 or cycle % TICKER_REFRESH == 0:
                top_tickers = get_smart_tickers(limit=TOP_TICKER_LIMIT)
                print(f"  └ 신규 선정 종목: {top_tickers}")

                # 기존 보유 종목 중 새 리스트에 없는 것 교체 처리
                handle_ticker_refresh(pm, top_tickers)

            save_cycle_info(cycle, top_tickers)

            # ★ 핵심 수정: 보유 중인 모든 종목 + 신규 종목 합쳐서 관리
            all_tickers = get_all_active_tickers(pm, top_tickers)

            # 현재가 수집 (전체 종목)
            prices = {}
            for ticker in all_tickers:
                p = get_current_price(ticker)
                if p:
                    prices[ticker] = p

            # 2. 종목별 매매 (보유 중인 모든 종목 관리)
            for ticker in all_tickers:
                price = prices.get(ticker)
                df_5m = get_ohlcv(ticker)
                df_1h = get_ohlcv_1h(ticker)

                if price is None or df_5m is None:
                    print(f"  └ [{ticker}] 데이터 수집 실패, 스킵")
                    continue

                # top_tickers에 없는 종목은 매도만 체크 (매수 안 함)
                is_active_ticker = ticker in top_tickers

                # 고점 업데이트
                pm.update_peak_price(ticker, price)

                avg = pm.get_avg_buy_price(ticker)
                buy_count = pm.get_buy_count(ticker)
                hours = pm.get_holding_hours(ticker)
                take_profit = pm.get_dynamic_take_profit(ticker)

                # top_tickers에 없는 잔여 보유 종목은 표시만 간략하게
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

                # 매도 조건 체크 (보유 중인 모든 종목)
                if avg:
                    sell_result = check_sell_conditions(
                        pm, ticker, price, df_5m, df_1h
                    )
                    if sell_result:
                        action, reason = sell_result
                        if action == "sell_all":
                            pm.sell_all(ticker, price, reason=reason)
                        else:
                            pm.sell(ticker, price, reason=reason)
                        continue

                # 매수 조건 체크 (top_tickers에 있는 종목만)
                if is_active_ticker:
                    # 현재 활성 포지션 수 확인
                    active_count = len([
                        s for s, p in pm.portfolio["positions"].items()
                        if p.get("quantity", 0) > 0
                    ])

                    if buy_count < MAX_BUY_COUNT:
                        if active_count >= TOP_TICKER_LIMIT and buy_count == 0:
                            print(f"  └ ⚠️ 최대 종목수 도달 ({TOP_TICKER_LIMIT}개)")
                        else:
                            signal = check_signal(df_5m, price, avg, df_1h,
                                                  pm.get_peak_price(ticker))
                            print(f"  └ 매매 신호: {signal}")

                            if signal == "BUY":
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
            # 종료 시 전체 현황 출력
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
    run()