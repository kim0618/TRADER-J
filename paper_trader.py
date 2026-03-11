# paper_trader.py
import json
import csv
import os
from datetime import datetime
from config import (
    INITIAL_BALANCE, TRADE_RATIO, SELL_RATIO, FEE_RATE,
    ALLOC_PER_TICKER, TOP_TICKER_LIMIT,
    TAKE_PROFIT_BASE, TAKE_PROFIT_6H,
    TAKE_PROFIT_12H, TAKE_PROFIT_24H,
    FORCE_SELL_HOURS, STOP_LOSS
)

PORTFOLIO_PATH = "data/portfolio.json"

class PortfolioManager:
    """
    통합 포트폴리오 매니저
    - 하나의 계좌에서 여러 종목 관리
    - 종목당 자금 배분 (1/3씩)
    - 보유 시간별 동적 익절 기준
    """

    def __init__(self):
        self.portfolio = self._load_portfolio()

    def _load_portfolio(self):
        if os.path.exists(PORTFOLIO_PATH):
            with open(PORTFOLIO_PATH, "r") as f:
                return json.load(f)
        return {
            "cash": INITIAL_BALANCE,
            "positions": {},      # 종목별 보유 정보
            "total_trades": 0,
            "win_trades": 0,
            "total_profit": 0.0,
        }

    def _save_portfolio(self):
        os.makedirs(os.path.dirname(PORTFOLIO_PATH), exist_ok=True)
        with open(PORTFOLIO_PATH, "w") as f:
            json.dump(self.portfolio, f, indent=2, ensure_ascii=False)

    def _log_trade(self, symbol, action, price, quantity, fee, profit=None):
        log_path = f"data/trades_{symbol}.csv"
        os.makedirs("data", exist_ok=True)
        file_exists = os.path.exists(log_path)
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "날짜시간", "종목", "액션", "가격", "수량",
                    "거래금액", "수수료", "손익", "보유현금"
                ])
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol, action,
                f"{price:,.0f}", f"{quantity:.6f}",
                f"{price * quantity:,.0f}", f"{fee:,.0f}",
                f"{profit:,.0f}" if profit is not None else "-",
                f"{self.portfolio['cash']:,.0f}"
            ])

    def get_alloc_cash(self):
        """종목당 배분 가능한 최대 자금"""
        return INITIAL_BALANCE * ALLOC_PER_TICKER

    def get_dynamic_take_profit(self, symbol):
        """보유 시간에 따른 동적 익절 기준"""
        pos = self.portfolio["positions"].get(symbol)
        if not pos or not pos.get("buy_time"):
            return TAKE_PROFIT_BASE

        buy_time = datetime.fromisoformat(pos["buy_time"])
        hours = (datetime.now() - buy_time).total_seconds() / 3600

        if hours >= 24:
            return TAKE_PROFIT_24H    # +0.5%
        elif hours >= 12:
            return TAKE_PROFIT_12H   # +2%
        elif hours >= 6:
            return TAKE_PROFIT_6H    # +4%
        else:
            return TAKE_PROFIT_BASE  # +6%

    def is_force_sell(self, symbol):
        """48시간 강제 청산 여부"""
        pos = self.portfolio["positions"].get(symbol)
        if not pos or not pos.get("buy_time"):
            return False

        buy_time = datetime.fromisoformat(pos["buy_time"])
        hours = (datetime.now() - buy_time).total_seconds() / 3600
        return hours >= FORCE_SELL_HOURS

    def get_holding_hours(self, symbol):
        """보유 시간 계산"""
        pos = self.portfolio["positions"].get(symbol)
        if not pos or not pos.get("buy_time"):
            return 0
        buy_time = datetime.fromisoformat(pos["buy_time"])
        return (datetime.now() - buy_time).total_seconds() / 3600

    def get_buy_count(self, symbol):
        """분할매수 횟수"""
        pos = self.portfolio["positions"].get(symbol)
        return pos.get("buy_count", 0) if pos else 0

    def can_buy(self, symbol):
        """매수 가능 여부 체크"""
        # 최대 종목수 체크
        active = [s for s, p in self.portfolio["positions"].items()
                  if p.get("quantity", 0) > 0]
        pos = self.portfolio["positions"].get(symbol, {})
        is_new = symbol not in active

        if is_new and len(active) >= TOP_TICKER_LIMIT:
            print(f"  └ ⚠️ 최대 종목수 도달 ({TOP_TICKER_LIMIT}개)")
            return False

        # 배분 한도 내 잔여 매수 가능 금액 체크
        alloc = self.get_alloc_cash()
        used = pos.get("total_invested", 0)
        remaining = alloc - used

        if remaining < 10000:
            print(f"  └ ⚠️ [{symbol}] 배분 한도 소진")
            return False

        if self.portfolio["cash"] < 10000:
            print(f"  └ ⚠️ 전체 잔액 부족")
            return False

        return True

    def buy(self, symbol, price):
        """매수 - 배분 한도 내에서 분할매수"""
        if not self.can_buy(symbol):
            return False

        alloc = self.get_alloc_cash()
        pos = self.portfolio["positions"].get(symbol, {})
        used = pos.get("total_invested", 0)
        remaining_alloc = alloc - used

        # 매수 금액 = 배분 잔여금의 30%
        buy_amount = min(remaining_alloc * TRADE_RATIO, self.portfolio["cash"])
        if buy_amount < 10000:
            return False

        fee = buy_amount * FEE_RATE
        quantity = (buy_amount - fee) / price

        # 포지션 업데이트
        if symbol not in self.portfolio["positions"] or pos.get("quantity", 0) == 0:
            # 신규 진입
            self.portfolio["positions"][symbol] = {
                "quantity": quantity,
                "avg_buy_price": price,
                "total_invested": buy_amount,
                "buy_count": 1,
                "buy_time": datetime.now().isoformat(),
                "peak_price": price,
            }
        else:
            # 추가 매수 - 평균단가 업데이트
            prev_qty = pos["quantity"]
            prev_avg = pos["avg_buy_price"]
            new_qty = prev_qty + quantity
            new_avg = (prev_qty * prev_avg + quantity * price) / new_qty

            self.portfolio["positions"][symbol].update({
                "quantity": new_qty,
                "avg_buy_price": new_avg,
                "total_invested": pos["total_invested"] + buy_amount,
                "buy_count": pos["buy_count"] + 1,
            })

        self.portfolio["cash"] -= buy_amount
        self.portfolio["total_trades"] += 1
        self._save_portfolio()
        self._log_trade(symbol, "매수", price, quantity, fee)

        pos = self.portfolio["positions"][symbol]
        print(f"  └ [{symbol}] 매수! {quantity:.4f}개 @ {price:,.0f}원")
        print(f"  └ 분할매수: {pos['buy_count']}회 | 평균단가: {pos['avg_buy_price']:,.0f}원")
        print(f"  └ 전체 잔액: {self.portfolio['cash']:,.0f}원")
        return True

    def sell(self, symbol, price, ratio=None, reason=""):
        """매도"""
        pos = self.portfolio["positions"].get(symbol)
        if not pos or pos.get("quantity", 0) <= 0:
            print(f"  └ [{symbol}] 보유 코인 없음")
            return False

        if ratio is None:
            ratio = SELL_RATIO

        quantity = pos["quantity"] * ratio
        sell_amount = quantity * price
        fee = sell_amount * FEE_RATE
        actual_amount = sell_amount - fee

        avg_buy_price = pos["avg_buy_price"]
        profit = actual_amount - (quantity * avg_buy_price)
        profit_rate = (price - avg_buy_price) / avg_buy_price * 100

        if profit > 0:
            self.portfolio["win_trades"] += 1
        self.portfolio["total_profit"] += profit
        self.portfolio["cash"] += actual_amount
        self.portfolio["total_trades"] += 1

        new_qty = pos["quantity"] - quantity
        if new_qty < 0.000001:
            # 전량 매도
            del self.portfolio["positions"][symbol]
        else:
            self.portfolio["positions"][symbol]["quantity"] = new_qty
            self.portfolio["positions"][symbol]["total_invested"] *= (1 - ratio)

        self._save_portfolio()
        self._log_trade(symbol, "매도", price, quantity, fee, profit)

        sell_type = "전량" if ratio >= 1.0 else f"{ratio*100:.0f}%"
        reason_str = f" ({reason})" if reason else ""
        print(f"  └ [{symbol}] {sell_type} 매도{reason_str}!")
        print(f"  └ 손익: {profit:+,.0f}원 ({profit_rate:+.2f}%)")
        print(f"  └ 전체 잔액: {self.portfolio['cash']:,.0f}원")
        return True

    def sell_all(self, symbol, price, reason=""):
        return self.sell(symbol, price, ratio=1.0, reason=reason)

    def update_peak_price(self, symbol, price):
        """고점 업데이트 (트레일링 스탑용)"""
        pos = self.portfolio["positions"].get(symbol)
        if pos and price > pos.get("peak_price", 0):
            self.portfolio["positions"][symbol]["peak_price"] = price
            self._save_portfolio()

    def get_peak_price(self, symbol):
        pos = self.portfolio["positions"].get(symbol, {})
        return pos.get("peak_price", 0)

    def get_avg_buy_price(self, symbol):
        pos = self.portfolio["positions"].get(symbol, {})
        qty = pos.get("quantity", 0)
        return pos.get("avg_buy_price", 0) if qty > 0 else None

    def get_total_value(self, prices):
        """전체 자산 계산"""
        total = self.portfolio["cash"]
        for symbol, pos in self.portfolio["positions"].items():
            qty = pos.get("quantity", 0)
            price = prices.get(symbol, 0)
            total += qty * price
        return total

    def print_status(self, prices={}):
        """전체 포트폴리오 현황 출력"""
        total_value = self.get_total_value(prices)
        profit = total_value - INITIAL_BALANCE
        profit_rate = profit / INITIAL_BALANCE * 100

        print(f"\n{'='*50}")
        print(f"  📊 통합 포트폴리오 현황")
        print(f"{'='*50}")
        print(f"  보유 현금:   {self.portfolio['cash']:>15,.0f}원")
        print(f"  총 자산:     {total_value:>15,.0f}원")
        print(f"  총 손익:     {profit:>+15,.0f}원 ({profit_rate:+.2f}%)")
        print(f"  누적 수익:   {self.portfolio['total_profit']:>+15,.0f}원")

        positions = self.portfolio["positions"]
        if positions:
            print(f"\n  ── 보유 종목 ({len(positions)}/{TOP_TICKER_LIMIT}개) ──")
            for symbol, pos in positions.items():
                qty = pos.get("quantity", 0)
                avg = pos.get("avg_buy_price", 0)
                price = prices.get(symbol, avg)
                value = qty * price
                unrealized = (price - avg) / avg * 100 if avg > 0 else 0
                hours = self.get_holding_hours(symbol)
                take_profit = self.get_dynamic_take_profit(symbol)
                buy_count = pos.get("buy_count", 0)

                print(f"\n  [{symbol}]")
                print(f"    보유수량:  {qty:.4f}개")
                print(f"    평균단가:  {avg:,.0f}원 → 현재: {price:,.0f}원")
                print(f"    평가금액:  {value:,.0f}원 ({unrealized:+.2f}%)")
                print(f"    보유시간:  {hours:.1f}시간 | 분할매수: {buy_count}회")
                print(f"    익절기준:  +{take_profit*100:.1f}% (시간별 자동조정)")
        else:
            print(f"\n  보유 종목 없음")

        trades = self.portfolio["total_trades"]
        wins = self.portfolio["win_trades"]
        win_rate = (wins / trades * 100) if trades > 0 else 0
        print(f"\n  총 거래:     {trades}회 | 승률: {win_rate:.1f}%")
        print(f"{'='*50}\n")