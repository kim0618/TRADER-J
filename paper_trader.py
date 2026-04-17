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
    FORCE_SELL_HOURS, STOP_LOSS,
    SLIPPAGE_RATE, DCA_DROP_1,
    SELL_COOLDOWN_MINUTES, GLOBAL_STOP_LOSS,
    COOLDOWN_PATH,
    CONSECUTIVE_SL_LIMIT, CONSECUTIVE_SL_BLACKLIST_HOURS,
)

PORTFOLIO_PATH = "data/portfolio.json"

class PortfolioManager:
    """
    통합 포트폴리오 매니저
    - 하나의 계좌에서 여러 종목 관리
    - 종목당 자금 배분 (1/3씩)
    - 보유 시간별 동적 익절 기준
    - 슬리피지 시뮬레이션
    - 분할매수 하락 조건 (같은 가격 연속 매수 방지)
    - 쿨다운 파일 영속화
    - 글로벌 포트폴리오 손절
    """

    def __init__(self):
        self.portfolio = self._load_portfolio()
        self._migrate_portfolio_stats()
        self._sell_cooldowns = self._load_cooldowns()
        self._consecutive_sl = {}       # 종목별 연속 손절 횟수
        self._sl_blacklist = {}         # 연속 손절 블랙리스트 {symbol: datetime}

    def _load_portfolio(self):
        if os.path.exists(PORTFOLIO_PATH):
            with open(PORTFOLIO_PATH, "r") as f:
                return json.load(f)
        return {
            "cash": INITIAL_BALANCE,
            "positions": {},
            "buy_orders": 0,
            "sell_orders": 0,
            "closed_trades": 0,
            "win_trades": 0,
            "total_profit": 0.0,
        }

    def _save_portfolio(self):
        os.makedirs(os.path.dirname(PORTFOLIO_PATH), exist_ok=True)
        with open(PORTFOLIO_PATH, "w") as f:
            json.dump(self.portfolio, f, indent=2, ensure_ascii=False)


    def _migrate_portfolio_stats(self):
        """기존 포트폴리오 통계 키를 신규 구조로 보정"""
        self.portfolio.setdefault("buy_orders", 0)
        self.portfolio.setdefault("sell_orders", 0)
        self.portfolio.setdefault("closed_trades", 0)
        self.portfolio.setdefault("win_trades", 0)
        self.portfolio.setdefault("total_profit", 0.0)

        legacy_total = self.portfolio.pop("total_trades", None)
        if legacy_total is not None:
            # 기존 데이터는 매수/매도 주문 수가 합쳐진 값이라 참고치로만 남기고,
            # 승률은 청산 기준(closed_trades)으로 새로 집계한다.
            if self.portfolio.get("buy_orders", 0) == 0 and self.portfolio.get("sell_orders", 0) == 0:
                self.portfolio["sell_orders"] = int(legacy_total)
            if self.portfolio.get("closed_trades", 0) == 0:
                self.portfolio["closed_trades"] = int(self.portfolio.get("sell_orders", 0))


    def _load_cooldowns(self):
        """쿨다운 파일에서 로드 (재시작 시에도 유지)"""
        if os.path.exists(COOLDOWN_PATH):
            try:
                with open(COOLDOWN_PATH, "r") as f:
                    data = json.load(f)
                # ISO 문자열을 datetime으로 변환
                return {k: datetime.fromisoformat(v) for k, v in data.items()}
            except Exception:
                pass
        return {}

    def _save_cooldowns(self):
        """쿨다운을 파일로 영속화"""
        os.makedirs(os.path.dirname(COOLDOWN_PATH), exist_ok=True)
        data = {k: v.isoformat() for k, v in self._sell_cooldowns.items()}
        with open(COOLDOWN_PATH, "w") as f:
            json.dump(data, f)

    def _apply_slippage(self, price, is_buy):
        """슬리피지 적용 (매수 시 불리하게, 매도 시 불리하게)"""
        if is_buy:
            return price * (1 + SLIPPAGE_RATE)   # 매수: 더 비싸게
        else:
            return price * (1 - SLIPPAGE_RATE)   # 매도: 더 싸게

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
            return TAKE_PROFIT_24H
        elif hours >= 12:
            return TAKE_PROFIT_12H
        elif hours >= 6:
            return TAKE_PROFIT_6H
        else:
            return TAKE_PROFIT_BASE

    def is_force_sell(self, symbol):
        """강제 청산 여부"""
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

    def check_global_stop_loss(self, prices):
        """글로벌 포트폴리오 손절 체크 (보유 포지션이 있을 때만)"""
        active = [s for s, p in self.portfolio["positions"].items()
                  if p.get("quantity", 0) > 0]
        if not active:
            return False, 0.0
        total_value = self.get_total_value(prices)
        loss_rate = (total_value - INITIAL_BALANCE) / INITIAL_BALANCE
        if loss_rate <= GLOBAL_STOP_LOSS:
            return True, loss_rate
        return False, loss_rate

    def can_buy(self, symbol, current_price=None):
        """매수 가능 여부 체크"""
        # 연속 손절 블랙리스트 체크
        if symbol in self._sl_blacklist:
            elapsed = (datetime.now() - self._sl_blacklist[symbol]).total_seconds() / 3600
            if elapsed < CONSECUTIVE_SL_BLACKLIST_HOURS:
                remaining = CONSECUTIVE_SL_BLACKLIST_HOURS - elapsed
                print(f"  └ 🚫 [{symbol}] 연속 손절 블랙리스트 ({remaining:.1f}시간 남음)")
                return False
            else:
                del self._sl_blacklist[symbol]
                self._consecutive_sl.pop(symbol, None)

        # 쿨다운 체크
        if symbol in self._sell_cooldowns:
            elapsed = (datetime.now() - self._sell_cooldowns[symbol]).total_seconds()
            cooldown_sec = SELL_COOLDOWN_MINUTES * 60
            if elapsed < cooldown_sec:
                remaining = int((cooldown_sec - elapsed) / 60)
                print(f"  └ 🚫 [{symbol}] 매도 후 재진입 쿨다운 ({remaining}분 남음)")
                return False
            else:
                del self._sell_cooldowns[symbol]
                self._save_cooldowns()

        # 최대 종목수 체크
        active = [s for s, p in self.portfolio["positions"].items()
                  if p.get("quantity", 0) > 0]
        pos = self.portfolio["positions"].get(symbol, {})
        is_new = symbol not in active

        if is_new and len(active) >= TOP_TICKER_LIMIT:
            print(f"  └ ⚠️ 최대 종목수 도달 ({TOP_TICKER_LIMIT}개)")
            return False

        # 분할매수 간격 제한 (10분)
        last_buy_time = pos.get("last_buy_time")
        if last_buy_time and pos.get("buy_count", 0) > 0:
            elapsed = (datetime.now() - datetime.fromisoformat(last_buy_time)).total_seconds()
            if elapsed < 600:
                remaining_min = int((600 - elapsed) / 60)
                remaining_sec = int((600 - elapsed) % 60)
                print(f"  └ ⏳ [{symbol}] 추가매수 대기 ({remaining_min}분 {remaining_sec}초 후 가능)")
                return False

        # 분할매수 하락 조건 체크 (같은 가격에 연속 매수 방지)
        buy_count = pos.get("buy_count", 0)
        avg_price = pos.get("avg_buy_price", 0)
        if buy_count > 0 and current_price and avg_price > 0:
            drop_rate = (current_price - avg_price) / avg_price
            if buy_count == 1 and drop_rate > DCA_DROP_1:
                print(f"  └ ⏳ [{symbol}] 2차 매수 대기 (현재 {drop_rate*100:+.2f}%, "
                      f"기준: {DCA_DROP_1*100:.0f}% 이하 필요)")
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
        """매수 - 슬리피지 적용, 배분 한도 내 분할매수"""
        if not self.can_buy(symbol, current_price=price):
            return False

        # 슬리피지 적용 (실제 체결가)
        fill_price = self._apply_slippage(price, is_buy=True)

        alloc = self.get_alloc_cash()
        pos = self.portfolio["positions"].get(symbol, {})
        used = pos.get("total_invested", 0)
        remaining_alloc = alloc - used

        # 매수 금액 = 배분 잔여금의 TRADE_RATIO(현재 60%)
        buy_amount = min(remaining_alloc * TRADE_RATIO, self.portfolio["cash"])
        if buy_amount < 10000:
            return False

        fee = buy_amount * FEE_RATE
        quantity = (buy_amount - fee) / fill_price

        # 포지션 업데이트
        now_iso = datetime.now().isoformat()
        if symbol not in self.portfolio["positions"] or pos.get("quantity", 0) == 0:
            self.portfolio["positions"][symbol] = {
                "quantity": quantity,
                "avg_buy_price": fill_price,
                "total_invested": buy_amount,
                "buy_count": 1,
                "buy_time": now_iso,
                "last_buy_time": now_iso,
                "peak_price": fill_price,
            }
        else:
            prev_qty = pos["quantity"]
            prev_avg = pos["avg_buy_price"]
            new_qty = prev_qty + quantity
            new_avg = (prev_qty * prev_avg + quantity * fill_price) / new_qty

            self.portfolio["positions"][symbol].update({
                "quantity": new_qty,
                "avg_buy_price": new_avg,
                "total_invested": pos["total_invested"] + buy_amount,
                "buy_count": pos["buy_count"] + 1,
                "last_buy_time": now_iso,
            })

        self.portfolio["cash"] -= buy_amount
        self.portfolio["buy_orders"] += 1
        self._save_portfolio()
        self._log_trade(symbol, "매수", fill_price, quantity, fee)

        pos = self.portfolio["positions"][symbol]
        slip_info = f" (슬리피지: {price:,.0f}→{fill_price:,.0f})" if abs(fill_price - price) > 0.5 else ""
        print(f"  └ [{symbol}] 매수! {quantity:.4f}개 @ {fill_price:,.0f}원{slip_info}")
        print(f"  └ 분할매수: {pos['buy_count']}회 | 평균단가: {pos['avg_buy_price']:,.0f}원")
        print(f"  └ 전체 잔액: {self.portfolio['cash']:,.0f}원")
        return True

    def sell(self, symbol, price, ratio=None, reason=""):
        """매도 - 슬리피지 적용"""
        pos = self.portfolio["positions"].get(symbol)
        if not pos or pos.get("quantity", 0) <= 0:
            print(f"  └ [{symbol}] 보유 코인 없음")
            return False

        if ratio is None:
            ratio = SELL_RATIO

        # 슬리피지 적용 (실제 체결가)
        fill_price = self._apply_slippage(price, is_buy=False)

        # 잔여 매도 금액이 5,000원 미만이면 전량 매도
        remaining_value = pos["quantity"] * fill_price * (1 - ratio)
        if remaining_value < 5000:
            ratio = 1.0
            print(f"  └ [{symbol}] 잔여금액 소액으로 전량 매도 전환")

        quantity = pos["quantity"] * ratio
        sell_amount = quantity * fill_price
        fee = sell_amount * FEE_RATE
        actual_amount = sell_amount - fee

        avg_buy_price = pos["avg_buy_price"]
        profit = actual_amount - (quantity * avg_buy_price)
        profit_rate = (fill_price - avg_buy_price) / avg_buy_price * 100

        self.portfolio["sell_orders"] += 1
        self.portfolio["total_profit"] += profit
        self.portfolio["cash"] += actual_amount

        new_qty = pos["quantity"] - quantity
        is_closed_trade = new_qty < 1e-9
        if is_closed_trade:
            self.portfolio["closed_trades"] += 1
            if profit > 0:
                self.portfolio["win_trades"] += 1
            del self.portfolio["positions"][symbol]
        else:
            self.portfolio["positions"][symbol]["quantity"] = new_qty
            self.portfolio["positions"][symbol]["total_invested"] *= (1 - ratio)

        self._save_portfolio()
        self._log_trade(symbol, "매도", fill_price, quantity, fee, profit)

        sell_type = "전량" if ratio >= 1.0 else f"{ratio*100:.0f}%"
        reason_str = f" ({reason})" if reason else ""
        slip_info = f" [체결: {fill_price:,.0f}]" if abs(fill_price - price) > 0.5 else ""
        print(f"  └ [{symbol}] {sell_type} 매도{reason_str}!{slip_info}")
        print(f"  └ 손익: {profit:+,.0f}원 ({profit_rate:+.2f}%)")
        print(f"  └ 전체 잔액: {self.portfolio['cash']:,.0f}원")
        return True

    def sell_all(self, symbol, price, reason=""):
        """전량 매도 + 쿨다운 등록 + 연속 손절 추적"""
        # 연속 손절 추적
        if reason == "손절":
            self._consecutive_sl[symbol] = self._consecutive_sl.get(symbol, 0) + 1
            if self._consecutive_sl[symbol] >= CONSECUTIVE_SL_LIMIT:
                self._sl_blacklist[symbol] = datetime.now()
                print(f"  └ 🚫 [{symbol}] 연속 {self._consecutive_sl[symbol]}회 손절 → {CONSECUTIVE_SL_BLACKLIST_HOURS}시간 블랙리스트")
        else:
            # 손절 아닌 매도는 연속 손절 카운터 리셋
            self._consecutive_sl.pop(symbol, None)

        self._sell_cooldowns[symbol] = datetime.now()
        self._save_cooldowns()
        self.sell(symbol, price, ratio=1.0, reason=reason)

    def global_stop_loss_sell(self, prices):
        """글로벌 손절: 전체 포지션 청산"""
        triggered, loss_rate = self.check_global_stop_loss(prices)
        if not triggered:
            return False

        print(f"\n  🚨 글로벌 손절 발동! 전체 자산 {loss_rate*100:.2f}% 하락")
        print(f"  🚨 전 포지션 긴급 청산 중...")

        symbols = list(self.portfolio["positions"].keys())
        for symbol in symbols:
            pos = self.portfolio["positions"].get(symbol)
            if pos and pos.get("quantity", 0) > 0:
                price = prices.get(symbol)
                if price:
                    self.sell_all(symbol, price, reason="글로벌손절")

        print(f"  🚨 글로벌 손절 완료. 잔액: {self.portfolio['cash']:,.0f}원")
        return True

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

        buy_orders = self.portfolio.get("buy_orders", 0)
        sell_orders = self.portfolio.get("sell_orders", 0)
        closed_trades = self.portfolio.get("closed_trades", 0)
        wins = self.portfolio.get("win_trades", 0)
        win_rate = (wins / closed_trades * 100) if closed_trades > 0 else 0
        print(f"\n  주문 수:     매수 {buy_orders}회 / 매도 {sell_orders}회")
        print(f"  청산 거래:   {closed_trades}건 | 승률: {win_rate:.1f}%")
        print(f"{'='*50}\n")

    @staticmethod
    def reset_portfolio():
        """포트폴리오 초기화 (새 전략 테스트용)"""
        import shutil
        from datetime import datetime

        # 기존 데이터 백업
        backup_dir = f"data/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if os.path.exists("data"):
            shutil.copytree("data", backup_dir)
            print(f"  📦 기존 데이터 백업 완료: {backup_dir}")

        # 포트폴리오 초기화
        os.makedirs("data", exist_ok=True)
        initial = {
            "cash": INITIAL_BALANCE,
            "positions": {},
            "buy_orders": 0,
            "sell_orders": 0,
            "closed_trades": 0,
            "win_trades": 0,
            "total_profit": 0.0,
        }
        with open(PORTFOLIO_PATH, "w") as f:
            json.dump(initial, f, indent=2)

        # 쿨다운 초기화
        cooldown_path = "data/cooldowns.json"
        if os.path.exists(cooldown_path):
            os.remove(cooldown_path)

        # 거래 CSV 삭제
        for f_name in os.listdir("data"):
            if f_name.startswith("trades_") and f_name.endswith(".csv"):
                os.remove(os.path.join("data", f_name))

        # cycle_info 초기화
        cycle_path = "data/cycle_info.json"
        if os.path.exists(cycle_path):
            os.remove(cycle_path)

        print(f"  ✅ 포트폴리오 초기화 완료! 초기자금: {INITIAL_BALANCE:,}원")
