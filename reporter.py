# reporter.py
import json
import csv
import os
from datetime import datetime
from config import INITIAL_BALANCE

def load_trades(symbol="BTC"):
    """거래 내역 불러오기"""
    path = f"data/trades_{symbol}.csv"
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades

def load_portfolio(symbol="BTC"):
    """포트폴리오 불러오기"""
    path = f"data/portfolio_{symbol}.json"
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)

def print_report(symbol="BTC", current_price=None):
    """수익률 리포트 출력"""
    trades = load_trades(symbol)
    portfolio = load_portfolio(symbol)

    print("\n" + "="*50)
    print(f"         📊 [{symbol}] 페이퍼 트레이딩 리포트")
    print("="*50)

    if not portfolio:
        print("포트폴리오 데이터 없음")
        return

    cash = portfolio["cash"]
    btc = portfolio["btc"]
    btc_value = btc * current_price if current_price and btc > 0 else 0
    total_value = cash + btc_value
    total_profit = total_value - INITIAL_BALANCE
    total_profit_rate = total_profit / INITIAL_BALANCE * 100

    total_trades = portfolio["total_trades"]
    win_trades = portfolio["win_trades"]
    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0

    print(f"  초기 자금:     {INITIAL_BALANCE:>15,.0f}원")
    print(f"  현재 자산:     {total_value:>15,.0f}원")
    print(f"  총 손익:       {total_profit:>+15,.0f}원")
    print(f"  수익률:        {total_profit_rate:>+14.2f}%")
    print("-"*50)
    print(f"  총 거래 횟수:  {total_trades:>15}회")
    print(f"  수익 거래:     {win_trades:>15}회")
    print(f"  승률:          {win_rate:>14.1f}%")
    print("-"*50)

    # 거래 내역 출력 (최근 10개)
    if trades:
        print(f"\n  {'날짜시간':<20} {'액션':<5} {'가격':>15} {'손익':>12}")
        print("  " + "-"*55)
        for t in trades[-10:]:
            profit_str = t.get("손익", "-")
            print(f"  {t['날짜시간']:<20} {t['액션']:<5} {t['가격']:>15} {profit_str:>12}")

    # MDD 계산
    if trades:
        profits = []
        cumulative = INITIAL_BALANCE
        for t in trades:
            pnl = t.get("손익", "-")
            if pnl != "-":
                try:
                    cumulative += float(pnl.replace(",", ""))
                    profits.append(cumulative)
                except:
                    pass

        if profits:
            peak = profits[0]
            mdd = 0
            for p in profits:
                if p > peak:
                    peak = p
                drawdown = (p - peak) / peak * 100
                if drawdown < mdd:
                    mdd = drawdown
            print(f"\n  MDD (최대낙폭): {mdd:.2f}%")

    print("="*50)
    print(f"  리포트 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*50 + "\n")


# 테스트 실행
if __name__ == "__main__":
    from collector import get_current_price

    print("=== 리포트 테스트 ===")
    for symbol in ["BTC", "ETH"]:
        price = get_current_price(symbol)
        print_report(symbol=symbol, current_price=price)