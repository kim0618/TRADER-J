# reporter.py
import json
import csv
import os
from datetime import datetime

import requests

from config import INITIAL_BALANCE

PORTFOLIO_PATH = "data/portfolio.json"
BITHUMB_API_URL = "https://api.bithumb.com/public/ticker"


def load_portfolio():
    """통합 포트폴리오 로드"""
    if not os.path.exists(PORTFOLIO_PATH):
        return None
    with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_trades(symbol):
    """종목별 거래 내역 로드"""
    path = f"data/trades_{symbol}.csv"
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades


def get_all_trade_symbols():
    """거래 내역 있는 종목 전체 조회"""
    symbols = []
    if not os.path.exists("data"):
        return symbols
    for f_name in os.listdir("data"):
        if f_name.startswith("trades_") and f_name.endswith(".csv"):
            sym = f_name.replace("trades_", "").replace(".csv", "")
            symbols.append(sym)
    return sorted(symbols)


def _to_float(value, default=0.0):
    try:
        text = str(value).replace(",", "").strip()
        if not text or text == "-":
            return default
        return float(text)
    except Exception:
        return default


def get_current_price(symbol):
    try:
        res = requests.get(f"{BITHUMB_API_URL}/{symbol}_KRW", timeout=5)
        data = res.json()
        if data.get("status") == "0000":
            return float(data["data"]["closing_price"])
    except Exception:
        pass
    return None


def build_trade_rounds(symbol):
    """매수 진입 후 보유 수량이 0이 될 때까지를 1개 청산 거래로 묶는다."""
    trades = load_trades(symbol)
    rounds = []
    current = None
    open_qty = 0.0

    for row in trades:
        action = row.get("액션")
        qty = _to_float(row.get("수량"))
        price = _to_float(row.get("가격"))
        fee = _to_float(row.get("수수료"))
        pnl = _to_float(row.get("손익"), default=0.0) if row.get("손익") not in (None, "-") else 0.0
        ts = row.get("날짜시간", "-")

        if action == "매수":
            if current is None or open_qty <= 1e-9:
                current = {
                    "coin": symbol,
                    "buys": [],
                    "sells": [],
                    "buy_qty": 0.0,
                    "buy_amount": 0.0,
                    "sell_qty": 0.0,
                    "sell_amount": 0.0,
                    "total_fee": 0.0,
                    "total_pnl": 0.0,
                    "first_buy_time": ts,
                    "last_sell_time": "-",
                }
                open_qty = 0.0
            current["buys"].append(row)
            current["buy_qty"] += qty
            current["buy_amount"] += price * qty
            current["total_fee"] += fee
            open_qty += qty

        elif action == "매도" and current is not None:
            current["sells"].append(row)
            current["sell_qty"] += qty
            current["sell_amount"] += price * qty
            current["total_fee"] += fee
            current["total_pnl"] += pnl
            current["last_sell_time"] = ts
            open_qty -= qty

            if open_qty <= 1e-8:
                buy_qty = current["buy_qty"]
                sell_qty = current["sell_qty"]
                avg_buy = current["buy_amount"] / buy_qty if buy_qty > 0 else 0.0
                avg_sell = current["sell_amount"] / sell_qty if sell_qty > 0 else 0.0
                pnl_rate = (avg_sell - avg_buy) / avg_buy * 100 if avg_buy > 0 else 0.0
                rounds.append({
                    "coin": symbol,
                    "avg_buy": avg_buy,
                    "avg_sell": avg_sell,
                    "total_pnl": current["total_pnl"],
                    "total_fee": current["total_fee"],
                    "pnl_rate": pnl_rate,
                    "buy_count": len(current["buys"]),
                    "sell_count": len(current["sells"]),
                    "first_buy_time": current["first_buy_time"],
                    "last_sell_time": current["last_sell_time"],
                })
                current = None
                open_qty = 0.0

    return rounds


def calculate_statistics(symbols):
    """청산 완료 거래 기준 통계 계산"""
    closed_rounds = []
    profits = []
    losses = []
    holding_times = []

    for sym in symbols:
        rounds = build_trade_rounds(sym)
        closed_rounds.extend(rounds)
        for round_info in rounds:
            pnl = round_info["total_pnl"]
            if pnl > 0:
                profits.append(pnl)
            elif pnl < 0:
                losses.append(pnl)

            try:
                first_buy = datetime.strptime(round_info["first_buy_time"], "%Y-%m-%d %H:%M:%S")
                last_sell = datetime.strptime(round_info["last_sell_time"], "%Y-%m-%d %H:%M:%S")
                holding_times.append((last_sell - first_buy).total_seconds() / 3600)
            except Exception:
                pass

    closed_rounds.sort(key=lambda x: x.get("last_sell_time", ""))

    stats = {
        "total_sell_trades": len(closed_rounds),
        "win_count": len(profits),
        "loss_count": len(losses),
        "avg_profit": sum(profits) / len(profits) if profits else 0,
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "max_profit": max(profits) if profits else 0,
        "max_loss": min(losses) if losses else 0,
        "total_profit_sum": sum(profits),
        "total_loss_sum": sum(losses),
        "avg_holding_hours": sum(holding_times) / len(holding_times) if holding_times else 0,
        "profit_factor": abs(sum(profits) / sum(losses)) if losses and sum(losses) != 0 else float("inf"),
    }

    max_consecutive_losses = 0
    current_streak = 0
    for round_info in closed_rounds:
        pnl = round_info["total_pnl"]
        if pnl < 0:
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        else:
            current_streak = 0
    stats["max_consecutive_losses"] = max_consecutive_losses

    cumulative = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    mdd = 0.0
    for round_info in closed_rounds:
        cumulative += round_info["total_pnl"]
        if cumulative > peak:
            peak = cumulative
        dd = (cumulative - peak) / peak * 100 if peak > 0 else 0.0
        if dd < mdd:
            mdd = dd
    stats["mdd"] = mdd

    return stats


def print_report():
    """전체 포트폴리오 리포트 출력"""
    portfolio = load_portfolio()

    print("\n" + "=" * 55)
    print("         📊 TRADER_J 통합 리포트")
    print("=" * 55)

    if not portfolio:
        print("  포트폴리오 데이터 없음 (아직 거래 없음)")
        print("=" * 55 + "\n")
        return

    cash = portfolio.get("cash", INITIAL_BALANCE)
    buy_orders = portfolio.get("buy_orders", 0)
    sell_orders = portfolio.get("sell_orders", 0)
    closed_trades = portfolio.get("closed_trades", sell_orders)
    win_trades = portfolio.get("win_trades", 0)
    total_profit = portfolio.get("total_profit", 0.0)
    win_rate = (win_trades / closed_trades * 100) if closed_trades > 0 else 0

    positions = portfolio.get("positions", {})
    total_market_value = 0.0
    invested_cost = 0.0

    print(f"  초기 자금:     {INITIAL_BALANCE:>15,.0f}원")
    print(f"  현재 현금:     {cash:>15,.0f}원")

    if positions:
        print(f"\n  ── 현재 보유 종목 ({len(positions)}개) ──")
        for symbol, pos in positions.items():
            avg = pos.get("avg_buy_price", 0)
            qty = pos.get("quantity", 0)
            invested = pos.get("total_invested", 0)
            buy_count = pos.get("buy_count", 0)
            buy_time = pos.get("buy_time", "")
            price = get_current_price(symbol)
            market_value = qty * price if price and qty > 0 else 0.0
            unrealized = market_value - invested if market_value > 0 else 0.0
            hours = 0
            if buy_time:
                try:
                    hours = (datetime.now() - datetime.fromisoformat(buy_time)).total_seconds() / 3600
                except Exception:
                    pass

            invested_cost += invested
            total_market_value += market_value

            current_price_text = f"{price:,.0f}원" if price else "조회실패"
            print(f"\n  [{symbol}]")
            print(f"    평균단가: {avg:,.0f}원 | 현재가: {current_price_text}")
            print(f"    수량: {qty:.4f}개 | 평가금액: {market_value:,.0f}원")
            print(f"    투자원가: {invested:,.0f}원 | 미실현손익: {unrealized:+,.0f}원")
            print(f"    보유시간: {hours:.1f}시간 | {buy_count}회 분할매수")
    else:
        print("\n  현재 보유 종목 없음")

    total_assets = cash + total_market_value
    overall_profit = total_assets - INITIAL_BALANCE
    overall_rate = overall_profit / INITIAL_BALANCE * 100 if INITIAL_BALANCE else 0

    print("-" * 55)
    print(f"  투자 원가:     {invested_cost:>15,.0f}원")
    print(f"  평가 금액:     {total_market_value:>15,.0f}원")
    print(f"  총 자산:       {total_assets:>15,.0f}원")
    print(f"  전체 손익:     {overall_profit:>+15,.0f}원 ({overall_rate:+.2f}%)")
    print(f"  실현 손익:     {total_profit:>+15,.0f}원")
    print("-" * 55)
    print(f"  주문 수:       매수 {buy_orders:>6}회 / 매도 {sell_orders:>6}회")
    print(f"  청산 거래:     {closed_trades:>15}건")
    print(f"  승률:          {win_rate:>14.1f}%  ({win_trades}승 {closed_trades - win_trades}패)")
    print("-" * 55)

    symbols = get_all_trade_symbols()
    if symbols:
        print(f"\n  ── 종목별 거래 요약 ──")
        print(f"  {'종목':>6} | {'매수':>4} | {'매도':>4} | {'청산':>4} | {'실현손익':>12} | {'최근거래'}")
        print("  " + "-" * 64)
        total_realized = 0
        for sym in symbols:
            trades = load_trades(sym)
            buys = [t for t in trades if t["액션"] == "매수"]
            sells = [t for t in trades if t["액션"] == "매도"]
            rounds = build_trade_rounds(sym)
            pnl = sum(r["total_pnl"] for r in rounds)
            total_realized += pnl
            last_time = trades[-1]["날짜시간"][5:16] if trades else "-"
            print(f"  {sym:>6} | {len(buys):>4}회 | {len(sells):>4}회 | {len(rounds):>4}건 | {pnl:>+12,.0f}원 | {last_time}")
        print(f"  {'합계':>6} |      |      |      | {total_realized:>+12,.0f}원")

    if symbols:
        stats = calculate_statistics(symbols)
        print(f"\n  ── 상세 거래 통계 ──")
        print(f"  평균 수익 거래:   {stats['avg_profit']:>+12,.0f}원")
        print(f"  평균 손실 거래:   {stats['avg_loss']:>+12,.0f}원")
        print(f"  최대 단일 수익:   {stats['max_profit']:>+12,.0f}원")
        print(f"  최대 단일 손실:   {stats['max_loss']:>+12,.0f}원")
        print(f"  수익 합계:        {stats['total_profit_sum']:>+12,.0f}원")
        print(f"  손실 합계:        {stats['total_loss_sum']:>+12,.0f}원")
        print(f"  Profit Factor:    {stats['profit_factor']:>11.2f}x")
        print(f"  평균 보유시간:    {stats['avg_holding_hours']:>11.1f}h")
        print(f"  최대 연속 손실:   {stats['max_consecutive_losses']:>11}회")
        print(f"  MDD (최대낙폭):   {stats['mdd']:>10.2f}%")

    print(f"\n  리포트 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    print_report()
