# backtest.py
"""
로컬 OHLCV CSV 기반 백테스터
- data/ohlcv 아래에 누적된 5분봉/1시간봉을 직접 읽음
- 실전 main.py + paper_trader.py 규칙과 최대한 비슷하게 검증
- 데이터가 충분하지 않으면 안전하게 스킵
- 결과를 data/backtests 아래 JSON/CSV로 저장
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config import (
    INITIAL_BALANCE,
    ALLOC_PER_TICKER,
    TRADE_RATIO,
    MAX_BUY_COUNT,
    SELL_COOLDOWN_MINUTES,
    STOP_LOSS,
    FEE_RATE,
    SLIPPAGE_RATE,
    TAKE_PROFIT_BASE,
    TAKE_PROFIT_6H,
    TAKE_PROFIT_12H,
    TAKE_PROFIT_24H,
    FORCE_SELL_HOURS,
    TIME_STOP_HOURS,
    TIME_STOP_THRESHOLD,
    TRAILING_STOP_TRIGGER,
    TRAILING_STOP_DROP,
    DCA_DROP_1,
    OHLCV_DATA_DIR,
    BACKTEST_RESULTS_DIR,
    BACKTEST_MIN_5M_BARS,
    BACKTEST_MIN_1H_BARS,
    DATA_FETCH_TOP_VOLUME_LIMIT,
    BLACKLIST,
    ATR_STOP_MULTIPLIER,
    ATR_STOP_MIN,
    ATR_STOP_MAX,
    VOLUME_RATIO_MIN,
    CONSECUTIVE_SL_LIMIT,
    CONSECUTIVE_SL_BLACKLIST_HOURS,
)
from strategy import check_signal, calculate_indicators, _calculate_adx
from config import (
    RSI_OVERSOLD, RSI_OVERBOUGHT, RSI_EXTREME_LOW, RSI_EXTREME_HIGH,
    BB_LOWER_THRESHOLD, BB_UPPER_THRESHOLD,
    BB_WIDTH_MIN, MIN_HOLD_MINUTES, STRATEGY_SELL_ENABLED,
    ADX_PERIOD, ADX_RANGE_THRESHOLD,
)
from data_fetcher import get_all_symbols


@dataclass
class Position:
    quantity: float
    avg_buy_price: float
    total_invested: float
    buy_count: int
    buy_time: datetime
    last_buy_time: datetime
    peak_price: float


class BacktestEngine:
    def __init__(self, initial_balance: float = INITIAL_BALANCE):
        self.initial_balance = initial_balance
        self.cash = initial_balance
        self.position: Optional[Position] = None
        self.trades: List[Dict] = []
        self.equity_curve: List[Dict] = []
        self.cooldown_until: Optional[datetime] = None
        self.current_time: Optional[datetime] = None
        self._consecutive_sl: int = 0
        self._sl_blacklist_until: Optional[datetime] = None

    def _apply_slippage(self, price: float, is_buy: bool) -> float:
        return price * (1 + SLIPPAGE_RATE) if is_buy else price * (1 - SLIPPAGE_RATE)

    def _get_dynamic_take_profit(self) -> float:
        if not self.position or not self.current_time:
            return TAKE_PROFIT_BASE
        hours = (self.current_time - self.position.buy_time).total_seconds() / 3600
        if hours >= 24:
            return TAKE_PROFIT_24H
        if hours >= 12:
            return TAKE_PROFIT_12H
        if hours >= 6:
            return TAKE_PROFIT_6H
        return TAKE_PROFIT_BASE

    def _can_buy(self, price: float) -> Tuple[bool, str]:
        if not self.current_time:
            return False, "시간없음"
        if self._sl_blacklist_until and self.current_time < self._sl_blacklist_until:
            return False, "연속손절블랙리스트"
        if self.cooldown_until and self.current_time < self.cooldown_until:
            return False, "쿨다운"

        alloc = self.initial_balance * ALLOC_PER_TICKER
        used = self.position.total_invested if self.position else 0.0
        remaining_alloc = alloc - used
        if remaining_alloc < 10000:
            return False, "배분한도"
        if self.cash < 10000:
            return False, "현금부족"

        if self.position:
            elapsed = (self.current_time - self.position.last_buy_time).total_seconds()
            if elapsed < 600:
                return False, "추가매수대기"

            drop_rate = (price - self.position.avg_buy_price) / self.position.avg_buy_price
            if self.position.buy_count == 1 and drop_rate > DCA_DROP_1:
                return False, "2차매수조건미달"
            if self.position.buy_count >= MAX_BUY_COUNT:
                return False, "최대분할도달"

        return True, "OK"

    def _buy(self, raw_price: float, reason: str) -> bool:
        can_buy, _ = self._can_buy(raw_price)
        if not can_buy or not self.current_time:
            return False

        fill_price = self._apply_slippage(raw_price, is_buy=True)
        alloc = self.initial_balance * ALLOC_PER_TICKER
        used = self.position.total_invested if self.position else 0.0
        remaining_alloc = max(0.0, alloc - used)
        buy_amount = min(remaining_alloc * TRADE_RATIO, self.cash)
        if buy_amount < 10000:
            return False

        fee = buy_amount * FEE_RATE
        quantity = (buy_amount - fee) / fill_price

        if self.position is None:
            self.position = Position(
                quantity=quantity,
                avg_buy_price=fill_price,
                total_invested=buy_amount,
                buy_count=1,
                buy_time=self.current_time,
                last_buy_time=self.current_time,
                peak_price=fill_price,
            )
        else:
            prev_qty = self.position.quantity
            new_qty = prev_qty + quantity
            new_avg = ((prev_qty * self.position.avg_buy_price) + (quantity * fill_price)) / new_qty
            self.position.quantity = new_qty
            self.position.avg_buy_price = new_avg
            self.position.total_invested += buy_amount
            self.position.buy_count += 1
            self.position.last_buy_time = self.current_time
            self.position.peak_price = max(self.position.peak_price, fill_price)

        self.cash -= buy_amount
        self.trades.append({
            "time": self.current_time.isoformat(),
            "action": "BUY",
            "price": fill_price,
            "quantity": quantity,
            "fee": fee,
            "pnl": 0.0,
            "reason": reason,
            "buy_count": self.position.buy_count if self.position else 1,
        })
        return True

    def _sell_all(self, raw_price: float, reason: str) -> bool:
        if not self.position or self.position.quantity <= 0 or not self.current_time:
            return False

        fill_price = self._apply_slippage(raw_price, is_buy=False)
        qty = self.position.quantity
        amount = qty * fill_price
        fee = amount * FEE_RATE
        actual = amount - fee
        pnl = actual - (qty * self.position.avg_buy_price)
        holding_hours = (self.current_time - self.position.buy_time).total_seconds() / 3600
        buy_count = self.position.buy_count

        self.cash += actual
        self.trades.append({
            "time": self.current_time.isoformat(),
            "action": "SELL",
            "price": fill_price,
            "quantity": qty,
            "fee": fee,
            "pnl": pnl,
            "reason": reason,
            "holding_hours": holding_hours,
            "buy_count": buy_count,
        })
        self.position = None
        self.cooldown_until = self.current_time + timedelta(minutes=SELL_COOLDOWN_MINUTES)

        # 연속 손절 블랙리스트 추적
        if reason == "손절":
            self._consecutive_sl += 1
            if self._consecutive_sl >= CONSECUTIVE_SL_LIMIT:
                self._sl_blacklist_until = self.current_time + timedelta(hours=CONSECUTIVE_SL_BLACKLIST_HOURS)
        else:
            self._consecutive_sl = 0

        return True

    def _mark_equity(self, price: float):
        if not self.current_time:
            return
        total = self.cash
        if self.position:
            total += self.position.quantity * price
        self.equity_curve.append({"time": self.current_time.isoformat(), "equity": total})

    def _fast_signal(self, curr: pd.Series, prev: pd.Series, prev2: pd.Series, trend: str) -> str:
        """사전 계산된 지표로 신호 판단 (v2: ADX레짐 + 2/3 매수 + 2/4 매도)"""
        rsi = curr['rsi']
        bb_pct = curr['bb_pct']
        macd_hist = curr['macd_hist']
        volume_ratio = curr['volume_ratio']
        bb_width = curr['bb_width']

        if pd.isna(rsi) or pd.isna(bb_pct):
            return "HOLD"
        if not pd.isna(volume_ratio) and volume_ratio < VOLUME_RATIO_MIN:
            return "HOLD"
        if not pd.isna(bb_width) and bb_width < BB_WIDTH_MIN:
            return "HOLD"

        prev_rsi = prev['rsi'] if not pd.isna(prev['rsi']) else rsi
        prev2_rsi = prev2['rsi'] if not pd.isna(prev2['rsi']) else prev_rsi

        def _rsi_rising():
            """RSI 2봉 연속 반등 확인"""
            return rsi >= prev_rsi and rsi > prev2_rsi

        # 극단 과매도 즉시 매수 (DOWN 추세만 차단 + 2봉 반등 확인)
        if rsi <= RSI_EXTREME_LOW:
            if trend == "DOWN":
                pass
            elif _rsi_rising():
                return "BUY"

        if rsi >= RSI_EXTREME_HIGH:
            return "SELL"

        # v2: UP/SIDEWAYS 모두 2/3 충족 시 매수 (DOWN=차단)
        if trend != "DOWN":
            buy_score = 0
            if rsi <= RSI_OVERSOLD: buy_score += 1
            if bb_pct <= BB_LOWER_THRESHOLD: buy_score += 1
            if not pd.isna(macd_hist) and macd_hist > 0: buy_score += 1

            required = 2  # v2: UP/SIDEWAYS 모두 2/3
            if buy_score >= required:
                if _rsi_rising():
                    return "BUY"

        # v2: 매도 판단 - 4개 조건 중 3개 이상
        sell_score = 0
        if rsi >= RSI_OVERBOUGHT: sell_score += 1
        if bb_pct >= BB_UPPER_THRESHOLD: sell_score += 1
        if trend == "DOWN": sell_score += 1
        if not pd.isna(macd_hist) and macd_hist < 0: sell_score += 1

        if sell_score >= 3:
            return "SELL"

        return "HOLD"

    def run(self, symbol: str, df_5m: pd.DataFrame, df_1h: pd.DataFrame) -> Optional[Dict]:
        if df_5m is None or len(df_5m) < BACKTEST_MIN_5M_BARS:
            return None
        if df_1h is None or len(df_1h) < BACKTEST_MIN_1H_BARS:
            return None

        df_5m = df_5m.copy().sort_values("time").reset_index(drop=True)
        df_1h = df_1h.copy().sort_values("time").reset_index(drop=True)

        # ── 1) 5분봉 지표 전체 사전 계산 (루프 밖에서 1회만) ──
        df_5m = calculate_indicators(df_5m)

        # ── 2) 1시간봉 추세 사전 계산 (v2: ADX + EMA 복합) ──
        df_1h['ma20'] = df_1h['close'].rolling(window=20).mean()
        df_1h['ma50'] = df_1h['close'].rolling(window=50).mean()
        df_1h['adx'] = _calculate_adx(df_1h, ADX_PERIOD)
        def _row_trend(row):
            if pd.isna(row['ma20']) or pd.isna(row['ma50']):
                return "SIDEWAYS"
            adx = row['adx'] if not pd.isna(row['adx']) else 0
            # ADX < 20: 횡보 (추세 없음)
            if adx < ADX_RANGE_THRESHOLD:
                return "SIDEWAYS"
            p = row['close']
            if p > row['ma20'] and row['ma20'] > row['ma50']:
                return "UP"
            elif p < row['ma20'] and row['ma20'] < row['ma50']:
                return "DOWN"
            return "SIDEWAYS"
        df_1h['trend'] = df_1h.apply(_row_trend, axis=1)
        df_1h_times = df_1h['time'].values

        def _trend_at(t):
            idx = df_1h_times.searchsorted(t, side='right') - 1
            if idx < 0 or len(df_1h) <= idx or idx < 49:
                return "SIDEWAYS"
            return df_1h.iloc[idx]['trend']

        start_index = max(30, 20)

        for i in range(start_index, len(df_5m)):
            curr = df_5m.iloc[i]
            self.current_time = curr["time"]
            price = float(curr["close"])

            if self.position and price > self.position.peak_price:
                self.position.peak_price = price

            if self.position:
                avg = self.position.avg_buy_price
                profit_rate = (price - avg) / avg if avg > 0 else 0.0
                peak = self.position.peak_price or price
                holding_hours = (self.current_time - self.position.buy_time).total_seconds() / 3600

                if holding_hours >= FORCE_SELL_HOURS:
                    self._sell_all(price, "강제청산")
                    self._mark_equity(price)
                    continue

                if peak > 0:
                    peak_profit = (peak - avg) / avg if avg > 0 else 0.0
                    drop_from_peak = (price - peak) / peak if peak > 0 else 0.0
                    if peak_profit >= TRAILING_STOP_TRIGGER and drop_from_peak <= -TRAILING_STOP_DROP:
                        self._sell_all(price, "트레일링스탑")
                        self._mark_equity(price)
                        continue

                # ATR 기반 동적 손절
                atr_val = curr.get('atr', None)
                atr_stop = STOP_LOSS  # 폴백
                if not pd.isna(atr_val) and atr_val and price > 0:
                    atr_pct = -(ATR_STOP_MULTIPLIER * atr_val) / price
                    atr_stop = max(ATR_STOP_MAX, min(ATR_STOP_MIN, atr_pct))

                if profit_rate <= atr_stop:
                    self._sell_all(price, "손절")
                    self._mark_equity(price)
                    continue

                # 횡보정리 (수익 중 면제: profit_rate <= 0 조건 추가)
                if holding_hours >= TIME_STOP_HOURS and profit_rate <= 0 and abs(profit_rate) <= TIME_STOP_THRESHOLD:
                    self._sell_all(price, "횡보정리")
                    self._mark_equity(price)
                    continue

                if profit_rate >= self._get_dynamic_take_profit():
                    self._sell_all(price, "동적익절")
                    self._mark_equity(price)
                    continue

            prev = df_5m.iloc[i - 1] if i > 0 else curr
            prev2 = df_5m.iloc[i - 2] if i > 1 else prev
            trend = _trend_at(self.current_time)
            signal = self._fast_signal(curr, prev, prev2, trend)

            if self.position and signal == "SELL" and STRATEGY_SELL_ENABLED:
                holding_hours = (self.current_time - self.position.buy_time).total_seconds() / 3600
                holding_minutes = holding_hours * 60
                avg = self.position.avg_buy_price
                profit_pct = (price - avg) / avg if avg > 0 else 0.0
                # v2: 최소 30분 보유 + 수익 중일 때만 전략매도
                if holding_minutes >= MIN_HOLD_MINUTES and holding_hours >= 0.5 and profit_pct >= 0:
                    self._sell_all(price, "전략매도")
            elif not self.position and signal == "BUY":
                self._buy(price, "전략매수")
            elif self.position and signal == "BUY":
                self._buy(price, "추가매수")

            self._mark_equity(price)

        if self.position:
            self.current_time = df_5m.iloc[-1]["time"]
            self._sell_all(float(df_5m.iloc[-1]["close"]), "백테스트종료")
            self._mark_equity(float(df_5m.iloc[-1]["close"]))

        return self._summary(symbol)

    def _summary(self, symbol: str) -> Dict:
        sells = [t for t in self.trades if t["action"] == "SELL"]
        wins = [t for t in sells if t["pnl"] > 0]
        losses = [t for t in sells if t["pnl"] < 0]
        total_pnl = sum(t["pnl"] for t in sells)
        total_fee = sum(t["fee"] for t in self.trades)
        win_rate = (len(wins) / len(sells) * 100) if sells else 0.0

        peak_eq = self.initial_balance
        mdd = 0.0
        for point in self.equity_curve:
            eq = point["equity"]
            if eq > peak_eq:
                peak_eq = eq
            dd = (eq - peak_eq) / peak_eq * 100
            if dd < mdd:
                mdd = dd

        sell_reasons = Counter(t.get("reason", "-") for t in sells)
        holding_hours_avg = sum(t.get("holding_hours", 0.0) for t in sells) / len(sells) if sells else 0.0

        dca_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
        for t in sells:
            key = str(t.get("buy_count", 1))
            dca_stats[key]["count"] += 1
            dca_stats[key]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                dca_stats[key]["wins"] += 1

        loss_sum = abs(sum(t["pnl"] for t in losses))
        win_sum = sum(t["pnl"] for t in wins)
        profit_factor = (win_sum / loss_sum) if loss_sum > 0 else float("inf")

        return {
            "symbol": symbol,
            "final_balance": round(self.cash, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_rate": round(total_pnl / self.initial_balance * 100, 2),
            "total_trades": len(sells),
            "win_rate": round(win_rate, 2),
            "avg_profit": round(win_sum / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0.0,
            "total_fee": round(total_fee, 2),
            "mdd": round(mdd, 2),
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
            "reason_counts": dict(sell_reasons),
            "avg_holding_hours": round(holding_hours_avg, 2),
            "dca_stats": {
                level: {
                    "count": stat["count"],
                    "win_rate": round((stat["wins"] / stat["count"] * 100), 2) if stat["count"] else 0.0,
                    "total_pnl": round(stat["pnl"], 2),
                }
                for level, stat in sorted(dca_stats.items(), key=lambda x: int(x[0]))
            },
        }


def load_local_ohlcv(symbol: str, interval: str, days: Optional[int] = None,
                     start: Optional[str] = None, end: Optional[str] = None) -> Optional[pd.DataFrame]:
    path = os.path.join(OHLCV_DATA_DIR, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        return None

    try:
        df = pd.read_csv(path)
        if df.empty:
            return None
        df["time"] = pd.to_datetime(df["time"])
        numeric_cols = [c for c in ["open", "close", "high", "low", "volume"] if c in df.columns]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["time", "close"]).sort_values("time").drop_duplicates(subset=["time"], keep="last")

        if days is not None:
            cutoff = df["time"].max() - pd.Timedelta(days=days)
            df = df[df["time"] >= cutoff]
        if start:
            df = df[df["time"] >= pd.to_datetime(start)]
        if end:
            df = df[df["time"] <= pd.to_datetime(end)]
        return df.reset_index(drop=True)
    except Exception as e:
        print(f"  [오류] {symbol} {interval} 로컬 CSV 로드 실패: {e}")
        return None


def ensure_dirs():
    os.makedirs(BACKTEST_RESULTS_DIR, exist_ok=True)


def save_results(payload: Dict):
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(BACKTEST_RESULTS_DIR, f"backtest_{stamp}.json")
    csv_path = os.path.join(BACKTEST_RESULTS_DIR, f"backtest_{stamp}.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    rows = []
    for symbol, result in payload.get("results", {}).items():
        rows.append({
            "symbol": symbol,
            "total_pnl_rate": result["total_pnl_rate"],
            "win_rate": result["win_rate"],
            "profit_factor": result["profit_factor"],
            "mdd": result["mdd"],
            "total_trades": result["total_trades"],
            "total_fee": result["total_fee"],
            "avg_holding_hours": result["avg_holding_hours"],
            "reason_counts": json.dumps(result["reason_counts"], ensure_ascii=False),
        })
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    return json_path, csv_path


def resolve_symbols(cli_symbols: Optional[List[str]], all_collected: bool) -> List[str]:
    if cli_symbols:
        return [s.upper() for s in cli_symbols if s.upper() not in BLACKLIST]
    if all_collected:
        found = []
        if os.path.exists(OHLCV_DATA_DIR):
            for name in os.listdir(OHLCV_DATA_DIR):
                if name.endswith("_5m.csv"):
                    symbol = name.replace("_5m.csv", "")
                    if symbol not in found and symbol not in BLACKLIST:
                        found.append(symbol)
        return sorted(found)
    return [s for s in get_all_symbols(DATA_FETCH_TOP_VOLUME_LIMIT) if s not in BLACKLIST]


def run_backtest(symbols: List[str], days: Optional[int] = None, start: Optional[str] = None,
                 end: Optional[str] = None, save: bool = True) -> Dict:
    print("\n" + "=" * 72)
    print("   📊 로컬 OHLCV CSV 백테스트 실행")
    print(f"   종목: {symbols}")
    print(f"   데이터 경로: {OHLCV_DATA_DIR}")
    if days is not None:
        print(f"   기간: 최근 {days}일")
    elif start or end:
        print(f"   기간: {start or '-'} ~ {end or '-'}")
    print("=" * 72)

    results: Dict[str, Dict] = {}
    skipped: Dict[str, str] = {}

    for symbol in symbols:
        print(f"\n  ▶ [{symbol}] 백테스트 중...")
        df_5m = load_local_ohlcv(symbol, "5m", days=days, start=start, end=end)
        df_1h = load_local_ohlcv(symbol, "1h", days=days, start=start, end=end)

        if df_5m is None or len(df_5m) < BACKTEST_MIN_5M_BARS:
            skipped[symbol] = f"5분봉 부족 ({0 if df_5m is None else len(df_5m)}개)"
            print(f"    ❌ 스킵: {skipped[symbol]}")
            continue
        if df_1h is None or len(df_1h) < BACKTEST_MIN_1H_BARS:
            skipped[symbol] = f"1시간봉 부족 ({0 if df_1h is None else len(df_1h)}개)"
            print(f"    ❌ 스킵: {skipped[symbol]}")
            continue

        engine = BacktestEngine()
        result = engine.run(symbol, df_5m=df_5m, df_1h=df_1h)
        if not result:
            skipped[symbol] = "실행 불가"
            print("    ❌ 스킵: 실행 불가")
            continue

        results[symbol] = result
        pf = "inf" if result["profit_factor"] is None else f"{result['profit_factor']:.3f}x"
        print(f"    최종 자산: {result['final_balance']:,.0f}원 ({result['total_pnl_rate']:+.2f}%)")
        print(f"    거래 횟수: {result['total_trades']}회 | 승률: {result['win_rate']:.1f}% | PF: {pf}")
        print(f"    평균 보유: {result['avg_holding_hours']:.1f}h | MDD: {result['mdd']:.2f}% | 수수료: {result['total_fee']:,.0f}원")
        print(f"    매도 사유: {result['reason_counts']}")

    summary = {}
    if results:
        total_pnl = sum(r["total_pnl"] for r in results.values())
        avg_win_rate = sum(r["win_rate"] for r in results.values()) / len(results)
        worst_mdd = min(r["mdd"] for r in results.values())
        summary = {
            "symbols": list(results.keys()),
            "total_pnl": round(total_pnl, 2),
            "avg_win_rate": round(avg_win_rate, 2),
            "worst_mdd": round(worst_mdd, 2),
            "result_count": len(results),
            "skipped": skipped,
            "generated_at": datetime.now().isoformat(),
            "days": days,
            "start": start,
            "end": end,
        }
        print(f"\n{'=' * 72}")
        print("  📋 종합 결과")
        print(f"  총 손익:    {total_pnl:+,.0f}원")
        print(f"  평균 승률:  {avg_win_rate:.1f}%")
        print(f"  최악 MDD:   {worst_mdd:.2f}%")
        if skipped:
            print(f"  스킵 종목:  {skipped}")
        print(f"{'=' * 72}\n")
    else:
        print("\n  ⚠️ 실행 가능한 종목이 없습니다. 데이터가 더 쌓인 뒤 다시 시도하세요.\n")

    payload = {"summary": summary, "results": results}
    if save and results:
        json_path, csv_path = save_results(payload)
        print(f"  💾 결과 저장 완료: {json_path}")
        print(f"  💾 요약 CSV 저장: {csv_path}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="로컬 OHLCV CSV 백테스터")
    parser.add_argument("symbols", nargs="*", help="백테스트할 종목들 (예: BTC ETH XRP)")
    parser.add_argument("--days", type=int, default=None, help="최근 N일만 테스트")
    parser.add_argument("--start", type=str, default=None, help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--all-collected", action="store_true", help="data/ohlcv에 있는 모든 종목 대상")
    parser.add_argument("--no-save", action="store_true", help="결과 파일 저장 안 함")
    args = parser.parse_args()

    symbols = resolve_symbols(args.symbols, args.all_collected)
    run_backtest(symbols, days=args.days, start=args.start, end=args.end, save=not args.no_save)
