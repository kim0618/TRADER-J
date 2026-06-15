# backtest.py - Swing Backtest v3.0
"""
1H 봉 기반 스윙 백테스트
- data/ohlcv/<SYM>_1h.csv 만 사용 (5m 불필요)
- 1H 시그널 + 1D 추세 필터
- 바 단위 high/low 활용한 intra-bar 스탑 시뮬레이션
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
    USE_PURE_ATR_TRAILING,
    ATR_TRAIL_MULTIPLIER,
    TRAIL_USE_CLOSE,
    OHLCV_DATA_DIR,
    BACKTEST_RESULTS_DIR,
    BACKTEST_MIN_1H_BARS,
    BACKTEST_MIN_6H_BARS,
    CANDLE_INTERVAL,
    CANDLE_AGGREGATE_FROM_1H,
    DATA_FETCH_TOP_VOLUME_LIMIT,
    BLACKLIST,
    ATR_STOP_MULTIPLIER,
    ATR_STOP_MIN,
    ATR_STOP_MAX,
    CONSECUTIVE_SL_LIMIT,
    CONSECUTIVE_SL_BLACKLIST_HOURS,
    MIN_HOLD_MINUTES,
    STRATEGY_SELL_ENABLED,
    DAILY_TREND_FILTER,
    EMA_FAST, EMA_MID,
    RSI_PERIOD, RSI_PULLBACK_LOW, RSI_PULLBACK_HIGH,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    DONCHIAN_PERIOD, BREAKOUT_BUFFER, BREAKOUT_VOLUME_MIN,
    ADX_PERIOD, ADX_BREAKOUT_MIN,
    VOLUME_RATIO_MIN, BUY_VOLUME_MIN,
)
from strategy import calculate_indicators, _calculate_adx, aggregate_to_daily
from data_fetcher import get_all_symbols


def aggregate_1h_to_6h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """1H OHLCV → 6H OHLCV 집계"""
    if df_1h is None or df_1h.empty:
        return df_1h
    df = df_1h.copy().sort_values("time").reset_index(drop=True)
    df = df.set_index('time')
    agg = df.resample('6h').agg({
        'open': 'first',
        'close': 'last',
        'high': 'max',
        'low': 'min',
        'volume': 'sum',
    }).dropna(subset=['close'])
    return agg.reset_index()


@dataclass
class Position:
    quantity: float
    avg_buy_price: float
    total_invested: float
    buy_time: datetime
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

    def _can_buy(self) -> Tuple[bool, str]:
        if not self.current_time:
            return False, "시간없음"
        if self._sl_blacklist_until and self.current_time < self._sl_blacklist_until:
            return False, "연속손절블랙리스트"
        if self.cooldown_until and self.current_time < self.cooldown_until:
            return False, "쿨다운"
        if self.position is not None:
            return False, "이미보유중"

        alloc = self.initial_balance * ALLOC_PER_TICKER
        if alloc < 10000 or self.cash < 10000:
            return False, "현금부족"
        return True, "OK"

    def _buy(self, raw_price: float, reason: str) -> bool:
        can_buy, _ = self._can_buy()
        if not can_buy or not self.current_time:
            return False

        fill_price = self._apply_slippage(raw_price, is_buy=True)
        alloc = self.initial_balance * ALLOC_PER_TICKER
        buy_amount = min(alloc * TRADE_RATIO, self.cash)
        if buy_amount < 10000:
            return False

        fee = buy_amount * FEE_RATE
        quantity = (buy_amount - fee) / fill_price

        self.position = Position(
            quantity=quantity,
            avg_buy_price=fill_price,
            total_invested=buy_amount,
            buy_time=self.current_time,
            peak_price=fill_price,
        )
        self.cash -= buy_amount
        self.trades.append({
            "time": self.current_time.isoformat(),
            "action": "BUY",
            "price": fill_price,
            "quantity": quantity,
            "fee": fee,
            "pnl": 0.0,
            "reason": reason,
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
        })
        self.position = None
        self.cooldown_until = self.current_time + timedelta(minutes=SELL_COOLDOWN_MINUTES)

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

    def _signal_at(self, df_1h_window: pd.DataFrame, daily_trend: str) -> str:
        """
        사전 계산된 1H 지표로 신호 판단 (성능 최적화 버전)
        df_1h_window: calculate_indicators가 적용된 1H df 슬라이스, 마지막 row 가 현재 봉
        daily_trend: 현 시점 1D 추세 ("UP"/"DOWN"/"SIDEWAYS")
        """
        if len(df_1h_window) < 2:
            return "HOLD"
        curr = df_1h_window.iloc[-1]
        prev = df_1h_window.iloc[-2]

        price = curr['close']
        rsi = curr.get('rsi', None)
        macd_hist = curr.get('macd_hist', None)
        high_n = curr.get('high_n', None)
        volume_ratio = curr.get('volume_ratio', 1.0)
        adx = curr.get('adx', 0)
        ema_fast = curr.get('ema_fast', None)
        ema_mid = curr.get('ema_mid', None)

        if rsi is None or pd.isna(rsi):
            return "HOLD"
        if ema_fast is None or pd.isna(ema_fast) or ema_mid is None or pd.isna(ema_mid):
            return "HOLD"
        if pd.isna(macd_hist):
            macd_hist = 0

        # ===== 매수 조건 (포지션 없을 때만 의미 있음) =====
        if self.position is None:
            # A. 1D 추세 UP 필터
            if DAILY_TREND_FILTER and daily_trend != "UP":
                return "HOLD"
            # 거래량 최소 필터
            if not pd.isna(volume_ratio) and volume_ratio < VOLUME_RATIO_MIN:
                return "HOLD"

            # B-(1) Donchian 돌파
            if (high_n is not None and not pd.isna(high_n)
                    and price > high_n * (1 + BREAKOUT_BUFFER)
                    and volume_ratio >= BREAKOUT_VOLUME_MIN
                    and adx >= ADX_BREAKOUT_MIN):
                return "BUY"

            # B-(2) 추세 풀백
            rsi_prev = prev['rsi'] if not pd.isna(prev['rsi']) else rsi
            rsi_rising = rsi >= rsi_prev

            if (ema_fast > ema_mid
                    and RSI_PULLBACK_LOW <= rsi <= RSI_PULLBACK_HIGH
                    and rsi_rising
                    and macd_hist > 0
                    and volume_ratio >= BUY_VOLUME_MIN):
                return "BUY"

            return "HOLD"

        # ===== 매도 조건 (보유 중) =====
        # 스윙 철학: 들어갔으면 trailing/stop/추세반전만 작동, 중간 청산 X
        # 1) 1D 추세 반전만 작동
        if daily_trend == "DOWN":
            return "SELL"
        return "HOLD"

    def run(self, symbol: str, df_1h: pd.DataFrame) -> Optional[Dict]:
        if df_1h is None or len(df_1h) < BACKTEST_MIN_1H_BARS:
            return None

        # v4.0: 1H → 6H 집계 (config 설정에 따라)
        if CANDLE_INTERVAL == "6h":
            df_signal = aggregate_1h_to_6h(df_1h)
            if df_signal is None or len(df_signal) < BACKTEST_MIN_6H_BARS:
                return None
        else:
            df_signal = df_1h.copy()

        df = df_signal.copy().sort_values("time").reset_index(drop=True)

        # 1) 시그널 지표 사전 계산
        df = calculate_indicators(df)

        # 2) 1D 집계 + 추세 사전 계산 (각 1D 캔들 종료 시점의 추세 매핑)
        daily = aggregate_to_daily(df_1h)  # 1D 추세는 항상 1H 원본으로
        daily_trend_map = {}
        if daily is not None and len(daily) >= 50:
            for _, row in daily.iterrows():
                t = row['time']
                p = row['close']
                e20 = row['ema20']
                e50 = row['ema50']
                if pd.isna(e20) or pd.isna(e50):
                    daily_trend_map[t] = "SIDEWAYS"
                    continue
                if p > e50 and e20 > e50:
                    daily_trend_map[t] = "UP"
                elif p < e50 and e20 < e50:
                    daily_trend_map[t] = "DOWN"
                else:
                    daily_trend_map[t] = "SIDEWAYS"

        daily_keys = sorted(daily_trend_map.keys())

        def _trend_at(t: pd.Timestamp) -> str:
            """현재 1H 시점 t 의 가장 최근 1D 추세 반환 (look-ahead 방지)"""
            if not daily_keys:
                return "SIDEWAYS"
            # t보다 작거나 같은 가장 큰 daily 시점
            idx = pd.Timestamp(t).normalize() - pd.Timedelta(days=1)
            # 어제 종가 기준 추세 사용 (당일 종가는 아직 형성 중일 수 있음)
            while idx in daily_trend_map and pd.isna(daily_trend_map[idx]):
                idx -= pd.Timedelta(days=1)
            return daily_trend_map.get(idx, "SIDEWAYS")

        def _trend_persisted_at(t: pd.Timestamp, days_required: int = 3) -> bool:
            """1D 추세가 N일 연속 UP 인지 (whipsaw 회피)"""
            base = pd.Timestamp(t).normalize() - pd.Timedelta(days=1)
            for i in range(days_required):
                d = base - pd.Timedelta(days=i)
                if daily_trend_map.get(d) != "UP":
                    return False
            return True

        # 데이터 충분한 시점부터 시작 (EMA50 + ADX + 일봉 50개)
        start_index = max(60, EMA_MID + 10)

        for i in range(start_index, len(df)):
            curr = df.iloc[i]
            self.current_time = curr["time"]
            close = float(curr["close"])
            high = float(curr["high"])
            low = float(curr["low"])

            # ── 보유 중: 봉 내부 high/low 로 청산 체크 ──
            if self.position:
                # peak 갱신 (이번 봉 high 까지 도달했다고 가정)
                if high > self.position.peak_price:
                    self.position.peak_price = high

                avg = self.position.avg_buy_price
                peak = self.position.peak_price
                holding_hours = (self.current_time - self.position.buy_time).total_seconds() / 3600

                # ATR 손절 가격 계산
                atr_val = curr.get('atr', None)
                atr_stop_pct = STOP_LOSS
                if not pd.isna(atr_val) and atr_val and avg > 0:
                    atr_pct = -(ATR_STOP_MULTIPLIER * atr_val) / avg
                    atr_stop_pct = max(ATR_STOP_MAX, min(ATR_STOP_MIN, atr_pct))
                stop_price = avg * (1 + atr_stop_pct)

                # 트레일링 스탑 (v4.0: +X% 이익부터 활성, ATR×N 거리)
                trail_stop_price = None
                peak_profit = (peak - avg) / avg if avg > 0 else 0
                trail_armed = peak_profit >= TRAILING_STOP_TRIGGER
                if trail_armed:
                    if USE_PURE_ATR_TRAILING:
                        # ATR 거리 트레일링 (peak - α×ATR)
                        if not pd.isna(atr_val) and atr_val:
                            trail_stop_price = peak - ATR_TRAIL_MULTIPLIER * atr_val
                    else:
                        # v3.0 고정 % drop
                        trail_stop_price = peak * (1 - TRAILING_STOP_DROP)

                # 동적 익절 가격
                tp_pct = self._get_dynamic_take_profit()
                tp_price = avg * (1 + tp_pct)

                # ── 강제 청산 시간 도달 ──
                if holding_hours >= FORCE_SELL_HOURS:
                    self._sell_all(close, "강제청산")
                    self._mark_equity(close)
                    continue

                # ── 봉 내부 우선순위 시뮬레이션 ──
                # 일반적으로 low 가 먼저 → high 가 나중이라 보수적으로 손절 우선
                # 하지만 트레일링이 armed 상태면 트레일링 stop 도 low 에서 발동 가능
                exit_triggered = False

                # 1) 손절 (ATR 기반): low 가 stop_price 이하면 발동
                if not exit_triggered and low <= stop_price and avg > 0:
                    self._sell_all(min(stop_price, close), "손절")
                    exit_triggered = True

                # 2) 트레일링 스탑: close 또는 low 기준 (config 설정)
                if not exit_triggered and trail_stop_price is not None:
                    trigger_price = close if TRAIL_USE_CLOSE else low
                    if trigger_price <= trail_stop_price:
                        # close 기준이면 close 가격, low 기준이면 trail_stop_price
                        fill = close if TRAIL_USE_CLOSE else trail_stop_price
                        self._sell_all(fill, "트레일링스탑")
                        exit_triggered = True

                # 3) 동적 익절: high 가 tp_price 이상이면 발동
                if not exit_triggered and high >= tp_price:
                    self._sell_all(tp_price, "동적익절")
                    exit_triggered = True

                # 4) 횡보 정리 (7일 이상 + 수익률 ±2% 이내)
                if not exit_triggered and holding_hours >= TIME_STOP_HOURS:
                    profit_rate = (close - avg) / avg if avg > 0 else 0
                    if abs(profit_rate) <= TIME_STOP_THRESHOLD:
                        self._sell_all(close, "횡보정리")
                        exit_triggered = True

                # 5) 전략 매도 (추세 반전 / 모멘텀 소진)
                if not exit_triggered and STRATEGY_SELL_ENABLED:
                    holding_minutes = holding_hours * 60
                    if holding_minutes >= MIN_HOLD_MINUTES:
                        trend = _trend_at(self.current_time)
                        sig = self._signal_at(df.iloc[:i+1], trend)
                        if sig == "SELL":
                            reason = "추세반전" if trend == "DOWN" else "모멘텀소진"
                            self._sell_all(close, reason)
                            exit_triggered = True

                if exit_triggered:
                    self._mark_equity(close)
                    continue

            # ── 매수 시그널 (포지션 없을 때) ──
            if self.position is None:
                trend = _trend_at(self.current_time)
                # 추세 지속성 체크: 3일 연속 UP일 때만 매수
                if not _trend_persisted_at(self.current_time, days_required=3):
                    self._mark_equity(close)
                    continue
                sig = self._signal_at(df.iloc[:i+1], trend)
                if sig == "BUY":
                    self._buy(close, "전략매수")

            self._mark_equity(close)

        # 종료 시점 잔여 포지션 청산
        if self.position:
            self.current_time = df.iloc[-1]["time"]
            self._sell_all(float(df.iloc[-1]["close"]), "백테스트종료")
            self._mark_equity(float(df.iloc[-1]["close"]))

        return self._summary(symbol)

    def _summary(self, symbol: str) -> Dict:
        sells = [t for t in self.trades if t["action"] == "SELL"]
        wins = [t for t in sells if t["pnl"] > 0]
        losses = [t for t in sells if t["pnl"] < 0]
        total_pnl = sum(t["pnl"] for t in sells)
        total_fee = sum(t["fee"] for t in self.trades)
        win_rate = (len(wins) / len(sells) * 100) if sells else 0.0

        # MDD
        peak_eq = self.initial_balance
        mdd = 0.0
        for point in self.equity_curve:
            eq = point["equity"]
            if eq > peak_eq:
                peak_eq = eq
            dd = (eq - peak_eq) / peak_eq * 100
            if dd < mdd:
                mdd = dd

        # Sharpe 추정 (일간 수익률 표준편차 기반)
        sharpe = None
        if len(self.equity_curve) >= 30:
            eq_df = pd.DataFrame(self.equity_curve)
            eq_df['time'] = pd.to_datetime(eq_df['time'])
            eq_df = eq_df.set_index('time')
            daily_eq = eq_df['equity'].resample('1D').last().dropna()
            if len(daily_eq) >= 10:
                daily_ret = daily_eq.pct_change().dropna()
                if daily_ret.std() > 0:
                    sharpe = (daily_ret.mean() / daily_ret.std()) * (365 ** 0.5)

        sell_reasons = Counter(t.get("reason", "-") for t in sells)
        holding_hours_avg = (sum(t.get("holding_hours", 0.0) for t in sells) / len(sells)) if sells else 0.0

        loss_sum = abs(sum(t["pnl"] for t in losses))
        win_sum = sum(t["pnl"] for t in wins)
        profit_factor = (win_sum / loss_sum) if loss_sum > 0 else (float("inf") if win_sum > 0 else 0)

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
            "sharpe": round(sharpe, 3) if sharpe is not None else None,
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
            "reason_counts": dict(sell_reasons),
            "avg_holding_hours": round(holding_hours_avg, 2),
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
    json_path = os.path.join(BACKTEST_RESULTS_DIR, f"swing_{stamp}.json")
    csv_path = os.path.join(BACKTEST_RESULTS_DIR, f"swing_{stamp}.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    rows = []
    for symbol, result in payload.get("results", {}).items():
        rows.append({
            "symbol": symbol,
            "total_pnl_rate": result["total_pnl_rate"],
            "win_rate": result["win_rate"],
            "profit_factor": result["profit_factor"],
            "sharpe": result.get("sharpe"),
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
                if name.endswith("_1h.csv"):
                    symbol = name.replace("_1h.csv", "")
                    if symbol not in found and symbol not in BLACKLIST:
                        found.append(symbol)
        return sorted(found)
    return [s for s in get_all_symbols(DATA_FETCH_TOP_VOLUME_LIMIT) if s not in BLACKLIST]


def run_backtest(symbols: List[str], days: Optional[int] = None, start: Optional[str] = None,
                 end: Optional[str] = None, save: bool = True, verbose: bool = True) -> Dict:
    print("\n" + "=" * 72)
    print("   📊 Swing Backtest v3.0 (1H 시그널 + 1D 추세 필터)")
    print(f"   종목 수: {len(symbols)}")
    print(f"   데이터 경로: {OHLCV_DATA_DIR}")
    if days is not None:
        print(f"   기간: 최근 {days}일")
    elif start or end:
        print(f"   기간: {start or '-'} ~ {end or '-'}")
    print("=" * 72)

    results: Dict[str, Dict] = {}
    skipped: Dict[str, str] = {}

    for symbol in symbols:
        df_1h = load_local_ohlcv(symbol, "1h", days=days, start=start, end=end)

        if df_1h is None or len(df_1h) < BACKTEST_MIN_1H_BARS:
            skipped[symbol] = f"1H 부족 ({0 if df_1h is None else len(df_1h)}개)"
            continue

        engine = BacktestEngine()
        result = engine.run(symbol, df_1h=df_1h)
        if not result:
            skipped[symbol] = "실행 불가"
            continue

        results[symbol] = result
        if verbose and result['total_trades'] > 0:
            pf = "inf" if result["profit_factor"] is None else f"{result['profit_factor']:.2f}"
            sh = "n/a" if result.get("sharpe") is None else f"{result['sharpe']:.2f}"
            print(f"  ▶ {symbol:10s} | PnL {result['total_pnl_rate']:+6.2f}% | "
                  f"Win {result['win_rate']:5.1f}% | PF {pf:>6s} | "
                  f"Sharpe {sh:>6s} | MDD {result['mdd']:6.2f}% | "
                  f"{result['total_trades']}회 | 보유 {result['avg_holding_hours']:5.1f}h")

    summary = {}
    if results:
        total_pnl = sum(r["total_pnl"] for r in results.values())
        # 거래가 있는 종목만 평균 계산
        traded = [r for r in results.values() if r["total_trades"] > 0]
        if traded:
            avg_win_rate = sum(r["win_rate"] for r in traded) / len(traded)
            avg_pf = sum((r["profit_factor"] or 0) for r in traded if r["profit_factor"] is not None) / max(1, len([r for r in traded if r["profit_factor"] is not None]))
            avg_sharpe_vals = [r["sharpe"] for r in traded if r.get("sharpe") is not None]
            avg_sharpe = sum(avg_sharpe_vals) / len(avg_sharpe_vals) if avg_sharpe_vals else None
        else:
            avg_win_rate = 0
            avg_pf = 0
            avg_sharpe = None
        worst_mdd = min(r["mdd"] for r in results.values())

        summary = {
            "symbols": list(results.keys()),
            "total_pnl": round(total_pnl, 2),
            "avg_win_rate": round(avg_win_rate, 2),
            "avg_profit_factor": round(avg_pf, 3),
            "avg_sharpe": round(avg_sharpe, 3) if avg_sharpe else None,
            "worst_mdd": round(worst_mdd, 2),
            "result_count": len(results),
            "traded_count": len(traded),
            "no_trade_count": len(results) - len(traded),
            "skipped": skipped,
            "generated_at": datetime.now().isoformat(),
            "days": days,
            "start": start,
            "end": end,
        }
        print(f"\n{'=' * 72}")
        print("  📋 종합 결과")
        print(f"  실행 종목:    {len(results)}개 (거래 발생: {len(traded)}개)")
        print(f"  총 손익:      {total_pnl:+,.0f}원 (종목당 평균 {total_pnl/max(1,len(results)):+,.0f}원)")
        print(f"  평균 승률:    {avg_win_rate:.1f}%")
        print(f"  평균 PF:      {avg_pf:.2f}")
        if avg_sharpe:
            print(f"  평균 Sharpe:  {avg_sharpe:.2f}")
        print(f"  최악 MDD:     {worst_mdd:.2f}%")
        if skipped:
            print(f"  스킵:         {len(skipped)}개")
        print(f"{'=' * 72}\n")
    else:
        print("\n  ⚠️ 실행 가능한 종목이 없습니다.\n")

    payload = {"summary": summary, "results": results}
    if save and results:
        json_path, csv_path = save_results(payload)
        print(f"  💾 결과: {json_path}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Swing Backtest v3.0")
    parser.add_argument("symbols", nargs="*", help="백테스트 종목 (예: BTC ETH)")
    parser.add_argument("--days", type=int, default=None, help="최근 N일만")
    parser.add_argument("--start", type=str, default=None, help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--all-collected", action="store_true", help="data/ohlcv 전체")
    parser.add_argument("--no-save", action="store_true", help="결과 저장 안 함")
    parser.add_argument("--quiet", action="store_true", help="간략 출력")
    args = parser.parse_args()

    symbols = resolve_symbols(args.symbols, args.all_collected)
    run_backtest(symbols, days=args.days, start=args.start, end=args.end,
                 save=not args.no_save, verbose=not args.quiet)
