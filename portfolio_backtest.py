# portfolio_backtest.py - 다종목 동시 운영 시뮬레이션
"""
실전 환경 재현:
- 자본 100만원 공유 (단일 종목당 25% × 4 종목 동시)
- 화이트리스트 N개 후보 풀에서 신호 발생 순서대로 진입
- 4종목 가득 차면 새 신호 무시 (실전 자본 제약)
- 실제 자본곡선 / Sharpe / MDD / 거래 빈도 산출

비교 포인트:
- 단일 종목 백테스트와 차이
- 자본 제약으로 놓친 신호 비율
- 동시 보유 평균 / 최대
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from config import (
    INITIAL_BALANCE,
    ALLOC_PER_TICKER,
    TRADE_RATIO,
    FEE_RATE,
    SLIPPAGE_RATE,
    TOP_TICKER_LIMIT,
    SELL_COOLDOWN_MINUTES,
    STOP_LOSS,
    TAKE_PROFIT_BASE,
    TAKE_PROFIT_6H,
    TAKE_PROFIT_12H,
    TAKE_PROFIT_24H,
    FORCE_SELL_HOURS,
    TIME_STOP_HOURS,
    TIME_STOP_THRESHOLD,
    TRAILING_STOP_TRIGGER,
    TRAILING_STOP_DROP,
    ATR_STOP_MULTIPLIER,
    ATR_STOP_MIN,
    ATR_STOP_MAX,
    DAILY_TREND_FILTER,
    BACKTEST_MIN_1H_BARS,
    EMA_FAST, EMA_MID,
    RSI_PULLBACK_LOW, RSI_PULLBACK_HIGH,
    DONCHIAN_PERIOD, BREAKOUT_BUFFER, BREAKOUT_VOLUME_MIN,
    ADX_BREAKOUT_MIN, VOLUME_RATIO_MIN, BUY_VOLUME_MIN,
)
from backtest import load_local_ohlcv, resolve_symbols, BacktestEngine
from strategy import calculate_indicators, aggregate_to_daily
from walkforward import compute_period_stats


TEST_DAYS = 30


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_buy_price: float
    invested: float
    buy_time: pd.Timestamp
    peak_price: float


class PortfolioBacktest:
    def __init__(self, initial_balance: float = INITIAL_BALANCE,
                 max_positions: int = TOP_TICKER_LIMIT):
        self.initial_balance = initial_balance
        self.cash = initial_balance
        self.max_positions = max_positions
        self.positions: Dict[str, Position] = {}
        self.trades: List[Dict] = []
        self.equity_curve: List[Dict] = []
        self.cooldowns: Dict[str, pd.Timestamp] = {}
        # 통계
        self.signals_skipped_full = 0   # max_positions 도달로 놓친 신호
        self.signals_skipped_cash = 0   # 현금 부족으로 놓친 신호
        self.signals_skipped_cool = 0   # 쿨다운 중 신호
        self.signals_taken = 0

    def _slip(self, price: float, is_buy: bool) -> float:
        return price * (1 + SLIPPAGE_RATE) if is_buy else price * (1 - SLIPPAGE_RATE)

    def _dynamic_tp(self, pos: Position, now: pd.Timestamp) -> float:
        hours = (now - pos.buy_time).total_seconds() / 3600
        if hours >= 24: return TAKE_PROFIT_24H
        if hours >= 12: return TAKE_PROFIT_12H
        if hours >= 6:  return TAKE_PROFIT_6H
        return TAKE_PROFIT_BASE

    def _can_buy(self, symbol: str, now: pd.Timestamp) -> str:
        if symbol in self.positions:
            return "이미보유"
        if symbol in self.cooldowns and now < self.cooldowns[symbol]:
            return "쿨다운"
        if len(self.positions) >= self.max_positions:
            return "한도초과"
        # 자본 체크: 1회 매수에 alloc * trade_ratio 필요
        alloc = self.initial_balance * ALLOC_PER_TICKER
        need = alloc * TRADE_RATIO
        if self.cash < need or self.cash < 10000:
            return "현금부족"
        return "OK"

    def buy(self, symbol: str, raw_price: float, now: pd.Timestamp, reason: str) -> bool:
        status = self._can_buy(symbol, now)
        if status == "한도초과":
            self.signals_skipped_full += 1
            return False
        if status == "현금부족":
            self.signals_skipped_cash += 1
            return False
        if status == "쿨다운":
            self.signals_skipped_cool += 1
            return False
        if status != "OK":
            return False

        fill = self._slip(raw_price, True)
        alloc = self.initial_balance * ALLOC_PER_TICKER
        buy_amount = min(alloc * TRADE_RATIO, self.cash)
        fee = buy_amount * FEE_RATE
        qty = (buy_amount - fee) / fill

        self.positions[symbol] = Position(
            symbol=symbol, quantity=qty, avg_buy_price=fill,
            invested=buy_amount, buy_time=now, peak_price=fill,
        )
        self.cash -= buy_amount
        self.signals_taken += 1
        self.trades.append({
            "time": now.isoformat(), "symbol": symbol,
            "action": "BUY", "price": fill, "qty": qty,
            "fee": fee, "pnl": 0.0, "reason": reason,
            "cash_after": self.cash,
        })
        return True

    def sell(self, symbol: str, raw_price: float, now: pd.Timestamp, reason: str) -> bool:
        pos = self.positions.get(symbol)
        if not pos:
            return False
        fill = self._slip(raw_price, False)
        amount = pos.quantity * fill
        fee = amount * FEE_RATE
        actual = amount - fee
        pnl = actual - pos.invested
        hold_h = (now - pos.buy_time).total_seconds() / 3600

        self.cash += actual
        del self.positions[symbol]
        self.cooldowns[symbol] = now + timedelta(minutes=SELL_COOLDOWN_MINUTES)

        self.trades.append({
            "time": now.isoformat(), "symbol": symbol,
            "action": "SELL", "price": fill, "qty": pos.quantity,
            "fee": fee, "pnl": pnl, "reason": reason,
            "holding_hours": hold_h, "cash_after": self.cash,
        })
        return True

    def mark_equity(self, now: pd.Timestamp, prices: Dict[str, float]):
        total = self.cash
        for sym, pos in self.positions.items():
            if sym in prices:
                total += pos.quantity * prices[sym]
            else:
                total += pos.quantity * pos.avg_buy_price  # 폴백
        self.equity_curve.append({
            "time": now.isoformat(),
            "equity": total,
            "cash": self.cash,
            "n_positions": len(self.positions),
        })


def _check_entry_signal(curr: pd.Series, prev: pd.Series, daily_trend: str) -> bool:
    """v3.0 진입 신호 (백테스트와 동일 로직)"""
    if DAILY_TREND_FILTER and daily_trend != "UP":
        return False

    rsi = curr.get('rsi')
    if rsi is None or pd.isna(rsi):
        return False
    ema_fast = curr.get('ema_fast')
    ema_mid = curr.get('ema_mid')
    if ema_fast is None or pd.isna(ema_fast) or ema_mid is None or pd.isna(ema_mid):
        return False

    macd_hist = curr.get('macd_hist', 0)
    if pd.isna(macd_hist):
        macd_hist = 0
    high_n = curr.get('high_n')
    volume_ratio = curr.get('volume_ratio', 1.0)
    if pd.isna(volume_ratio):
        volume_ratio = 1.0
    adx = curr.get('adx', 0)
    if pd.isna(adx):
        adx = 0
    price = curr['close']

    if volume_ratio < VOLUME_RATIO_MIN:
        return False

    # Donchian 돌파
    if (high_n is not None and not pd.isna(high_n)
            and price > high_n * (1 + BREAKOUT_BUFFER)
            and volume_ratio >= BREAKOUT_VOLUME_MIN
            and adx >= ADX_BREAKOUT_MIN):
        return True

    # 추세 풀백
    rsi_prev = prev['rsi'] if not pd.isna(prev['rsi']) else rsi
    rsi_rising = rsi >= rsi_prev
    if (ema_fast > ema_mid
            and RSI_PULLBACK_LOW <= rsi <= RSI_PULLBACK_HIGH
            and rsi_rising
            and macd_hist > 0
            and volume_ratio >= BUY_VOLUME_MIN):
        return True

    return False


def _check_exit_conditions(pos: Position, curr: pd.Series,
                           now: pd.Timestamp, daily_trend: str,
                           dynamic_tp: float) -> Optional[tuple]:
    """청산 조건 체크. 발동 시 (사유, 청산가격) 반환"""
    high = float(curr['high'])
    low = float(curr['low'])
    close = float(curr['close'])
    avg = pos.avg_buy_price
    peak = pos.peak_price
    hold_h = (now - pos.buy_time).total_seconds() / 3600

    # peak 갱신
    if high > peak:
        pos.peak_price = high
        peak = high

    # 1) 강제 청산
    if hold_h >= FORCE_SELL_HOURS:
        return ("강제청산", close)

    # 2) ATR 손절
    atr_val = curr.get('atr')
    atr_stop_pct = STOP_LOSS
    if atr_val and not pd.isna(atr_val) and avg > 0:
        atr_pct = -(ATR_STOP_MULTIPLIER * atr_val) / avg
        atr_stop_pct = max(ATR_STOP_MAX, min(ATR_STOP_MIN, atr_pct))
    stop_price = avg * (1 + atr_stop_pct)
    if low <= stop_price:
        return ("손절", min(stop_price, close))

    # 3) 트레일링 스탑
    trail_armed = (peak - avg) / avg >= TRAILING_STOP_TRIGGER if avg > 0 else False
    if trail_armed:
        trail_stop = peak * (1 - TRAILING_STOP_DROP)
        if low <= trail_stop:
            return ("트레일링스탑", trail_stop)

    # 4) 동적 익절
    tp_price = avg * (1 + dynamic_tp)
    if high >= tp_price:
        return ("동적익절", tp_price)

    # 5) 추세 반전
    if daily_trend == "DOWN":
        return ("추세반전", close)

    # 6) 횡보 정리
    if hold_h >= TIME_STOP_HOURS:
        profit_rate = (close - avg) / avg if avg > 0 else 0
        if abs(profit_rate) <= TIME_STOP_THRESHOLD:
            return ("횡보정리", close)

    return None


def precompute_data(symbols: List[str]) -> Dict[str, Dict]:
    """모든 종목의 지표 + 1D 추세 사전 계산"""
    cache = {}
    for sym in symbols:
        df_1h = load_local_ohlcv(sym, "1h")
        if df_1h is None or len(df_1h) < BACKTEST_MIN_1H_BARS:
            continue
        df = df_1h.copy().sort_values("time").reset_index(drop=True)
        df = calculate_indicators(df)

        # 1D 추세 맵
        daily = aggregate_to_daily(df_1h)
        daily_map = {}
        if daily is not None:
            for _, row in daily.iterrows():
                t = row['time']
                p = row['close']
                e20 = row['ema20']
                e50 = row['ema50']
                if pd.isna(e20) or pd.isna(e50):
                    daily_map[t] = "SIDEWAYS"
                elif p > e50 and e20 > e50:
                    daily_map[t] = "UP"
                elif p < e50 and e20 < e50:
                    daily_map[t] = "DOWN"
                else:
                    daily_map[t] = "SIDEWAYS"

        # 시간 인덱스
        df = df.set_index('time')
        cache[sym] = {
            "df": df,
            "daily_map": daily_map,
            "times": df.index.tolist(),
        }
    return cache


def trend_at(daily_map: dict, t: pd.Timestamp) -> str:
    idx = pd.Timestamp(t).normalize() - pd.Timedelta(days=1)
    for _ in range(30):
        if idx in daily_map:
            return daily_map[idx]
        idx -= pd.Timedelta(days=1)
    return "SIDEWAYS"


def trend_persisted(daily_map: dict, t: pd.Timestamp, days_required: int = 3) -> bool:
    base = pd.Timestamp(t).normalize() - pd.Timedelta(days=1)
    for i in range(days_required):
        d = base - pd.Timedelta(days=i)
        if daily_map.get(d) != "UP":
            return False
    return True


def compute_metrics(portfolio: PortfolioBacktest) -> Dict:
    if not portfolio.equity_curve:
        return {}

    eq_df = pd.DataFrame(portfolio.equity_curve)
    eq_df['time'] = pd.to_datetime(eq_df['time'])

    initial = portfolio.initial_balance
    final = eq_df['equity'].iloc[-1]
    total_return_pct = (final / initial - 1) * 100

    # MDD
    eq_df['peak'] = eq_df['equity'].cummax()
    eq_df['dd'] = (eq_df['equity'] - eq_df['peak']) / eq_df['peak']
    mdd_pct = eq_df['dd'].min() * 100

    # Sharpe (1H 단위)
    eq_df = eq_df.set_index('time')
    hourly_eq = eq_df['equity'].resample('1h').last().ffill()
    hourly_ret = hourly_eq.pct_change().dropna()
    sharpe = None
    if hourly_ret.std() > 0:
        # 연환산: 24*365 = 8760시간
        sharpe = (hourly_ret.mean() / hourly_ret.std()) * math.sqrt(24 * 365)

    sells = [t for t in portfolio.trades if t['action'] == 'SELL']
    wins = [t for t in sells if t['pnl'] > 0]
    losses = [t for t in sells if t['pnl'] < 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0
    win_sum = sum(t['pnl'] for t in wins)
    loss_sum = abs(sum(t['pnl'] for t in losses))
    pf = win_sum / loss_sum if loss_sum > 0 else float('inf') if win_sum > 0 else 0
    total_fee = sum(t.get('fee', 0) for t in portfolio.trades)
    avg_pos = eq_df['n_positions'].mean()
    max_pos = eq_df['n_positions'].max()

    return {
        "initial_balance": initial,
        "final_balance": final,
        "total_return_pct": total_return_pct,
        "mdd_pct": mdd_pct,
        "sharpe": sharpe,
        "n_trades": len(sells),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": pf if pf != float('inf') else None,
        "total_fee": total_fee,
        "avg_positions": avg_pos,
        "max_positions": max_pos,
        "signals_taken": portfolio.signals_taken,
        "signals_skipped_full": portfolio.signals_skipped_full,
        "signals_skipped_cash": portfolio.signals_skipped_cash,
        "signals_skipped_cool": portfolio.signals_skipped_cool,
    }


def run_portfolio_backtest(symbol_pool: List[str], test_start: pd.Timestamp,
                           test_end: pd.Timestamp) -> tuple:
    """주어진 종목 풀로 TEST 기간 동안 포트폴리오 시뮬레이션"""
    print(f"\n  데이터 사전 계산 중 ({len(symbol_pool)}종목)...")
    cache = precompute_data(symbol_pool)
    print(f"  로드 완료: {len(cache)}종목")

    portfolio = PortfolioBacktest()

    # 모든 종목의 시간 인덱스 통합
    all_times = set()
    for data in cache.values():
        for t in data['times']:
            if test_start <= t <= test_end:
                all_times.add(t)
    sorted_times = sorted(all_times)
    print(f"  시뮬 구간: {len(sorted_times)} 시간봉")

    for t in sorted_times:
        # 현재 시점 가격 캐시 (mark_equity용)
        prices_now = {}
        for sym, data in cache.items():
            if t in data['df'].index:
                prices_now[sym] = float(data['df'].loc[t, 'close'])

        # 1) 보유 포지션 청산 체크 (먼저)
        to_sell = []
        for sym, pos in list(portfolio.positions.items()):
            if sym not in cache or t not in cache[sym]['df'].index:
                continue
            curr = cache[sym]['df'].loc[t]
            daily_t = trend_at(cache[sym]['daily_map'], t)
            dyn_tp = portfolio._dynamic_tp(pos, t)
            exit_result = _check_exit_conditions(pos, curr, t, daily_t, dyn_tp)
            if exit_result:
                reason, price = exit_result
                to_sell.append((sym, price, reason))
        for sym, price, reason in to_sell:
            portfolio.sell(sym, price, t, reason)

        # 2) 진입 신호 체크 (포지션 없는 화이트리스트 종목)
        for sym in symbol_pool:
            if sym in portfolio.positions:
                continue
            if sym not in cache or t not in cache[sym]['df'].index:
                continue
            df_sym = cache[sym]['df']
            idx_pos = df_sym.index.get_loc(t)
            if idx_pos < 50:  # 최소 데이터
                continue
            curr = df_sym.iloc[idx_pos]
            prev = df_sym.iloc[idx_pos - 1]
            daily_t = trend_at(cache[sym]['daily_map'], t)
            if not trend_persisted(cache[sym]['daily_map'], t, 3):
                continue
            if _check_entry_signal(curr, prev, daily_t):
                portfolio.buy(sym, float(curr['close']), t, "전략매수")

        # 3) equity 마킹 (매시간)
        portfolio.mark_equity(t, prices_now)

    # 종료 시 잔여 포지션 청산
    final_t = sorted_times[-1] if sorted_times else test_end
    for sym in list(portfolio.positions.keys()):
        if sym in cache and final_t in cache[sym]['df'].index:
            price = float(cache[sym]['df'].loc[final_t, 'close'])
        else:
            price = portfolio.positions[sym].avg_buy_price
        portfolio.sell(sym, price, final_t, "백테스트종료")

    return portfolio, compute_metrics(portfolio)


def build_whitelist_from_train(all_symbols: List[str], train_start: pd.Timestamp,
                                train_end: pd.Timestamp) -> List[str]:
    """TRAIN 기간 백테스트로 화이트리스트 생성 (v3.0 기준)"""
    whitelist = []
    for sym in all_symbols:
        df_1h = load_local_ohlcv(sym, "1h")
        if df_1h is None or len(df_1h) < BACKTEST_MIN_1H_BARS:
            continue
        engine = BacktestEngine()
        engine.run(sym, df_1h=df_1h)
        s = compute_period_stats(engine.trades, train_start, train_end)
        if not s or s['trades'] < 3:
            continue
        pf = s.get('profit_factor') or 0
        sharpe = s.get('sharpe') or 0
        if pf >= 1.5 and s['win_rate'] >= 50 and sharpe > 0:
            whitelist.append(sym)
    return whitelist


def main():
    print("=" * 72)
    print("   포트폴리오 백테스트 (다종목 동시 운영 시뮬레이션)")
    print("=" * 72)

    all_symbols = resolve_symbols(None, all_collected=True)
    print(f"\n  전체 풀: {len(all_symbols)}종목")

    # TRAIN/TEST 분할 시점 찾기 (가장 최근 데이터 기준)
    latest = pd.Timestamp("2026-01-01")
    for sym in all_symbols[:30]:
        df_1h = load_local_ohlcv(sym, "1h")
        if df_1h is not None and len(df_1h) > 0:
            latest = max(latest, df_1h['time'].max())
    test_start = latest - pd.Timedelta(days=TEST_DAYS)
    test_end = latest
    train_start = test_start - pd.Timedelta(days=365)
    train_end = test_start

    print(f"  TRAIN: {train_start.date()} ~ {train_end.date()}")
    print(f"  TEST:  {test_start.date()} ~ {test_end.date()}\n")

    # 1) 화이트리스트 구축
    print("=" * 72)
    print("  Step 1: TRAIN 기간 화이트리스트 구축")
    print("=" * 72)
    whitelist = build_whitelist_from_train(all_symbols, train_start, train_end)
    print(f"\n  화이트리스트 (PF≥1.5 + Win≥50% + Sharpe+): {len(whitelist)}개")
    print(f"  {whitelist}")

    if not whitelist:
        print("  화이트리스트 비어있음. 종료.")
        return

    # 2) 포트폴리오 시뮬레이션
    print(f"\n{'=' * 72}")
    print(f"  Step 2: TEST 30일 포트폴리오 시뮬레이션")
    print(f"{'=' * 72}")
    portfolio, metrics = run_portfolio_backtest(whitelist, test_start, test_end)

    # 3) 결과 출력
    print(f"\n{'=' * 72}")
    print("  📊 포트폴리오 성과 (실전 환경 시뮬레이션)")
    print(f"{'=' * 72}")
    print(f"\n  자본곡선")
    print(f"    초기: {metrics['initial_balance']:,.0f}원")
    print(f"    종료: {metrics['final_balance']:,.0f}원")
    print(f"    수익률: {metrics['total_return_pct']:+.2f}% (30일)")
    print(f"    연환산 추정: {metrics['total_return_pct'] * 365 / TEST_DAYS:+.1f}% (단리)")

    print(f"\n  리스크")
    print(f"    MDD: {metrics['mdd_pct']:.2f}%")
    print(f"    Sharpe (연환산): {metrics['sharpe']:.2f}" if metrics['sharpe'] else "    Sharpe: n/a")

    print(f"\n  거래")
    print(f"    총 거래: {metrics['n_trades']}회 ({metrics['n_wins']}승 {metrics['n_losses']}패)")
    print(f"    승률: {metrics['win_rate']:.1f}%")
    print(f"    PF: {metrics['profit_factor']:.2f}" if metrics['profit_factor'] else "    PF: n/a")
    print(f"    총 수수료: {metrics['total_fee']:,.0f}원")

    print(f"\n  포지션 운영")
    print(f"    평균 동시 보유: {metrics['avg_positions']:.2f}개")
    print(f"    최대 동시 보유: {metrics['max_positions']}개")

    print(f"\n  신호 처리")
    total_signals = (metrics['signals_taken'] + metrics['signals_skipped_full']
                     + metrics['signals_skipped_cash'] + metrics['signals_skipped_cool'])
    print(f"    총 발생 신호: {total_signals}")
    print(f"    체결: {metrics['signals_taken']} ({metrics['signals_taken']/max(1,total_signals)*100:.1f}%)")
    print(f"    한도초과로 놓침: {metrics['signals_skipped_full']} ({metrics['signals_skipped_full']/max(1,total_signals)*100:.1f}%)")
    print(f"    현금부족으로 놓침: {metrics['signals_skipped_cash']} ({metrics['signals_skipped_cash']/max(1,total_signals)*100:.1f}%)")
    print(f"    쿨다운 중: {metrics['signals_skipped_cool']} ({metrics['signals_skipped_cool']/max(1,total_signals)*100:.1f}%)")

    # 거래 사유 분포
    sells = [t for t in portfolio.trades if t['action'] == 'SELL']
    if sells:
        from collections import Counter
        reasons = Counter(t['reason'] for t in sells)
        print(f"\n  매도 사유")
        for r, c in reasons.most_common():
            print(f"    {r}: {c} ({c/len(sells)*100:.1f}%)")

    # 종목별 결과
    sym_pnl = {}
    for t in sells:
        sym = t['symbol']
        sym_pnl.setdefault(sym, {'pnl': 0, 'trades': 0, 'wins': 0})
        sym_pnl[sym]['pnl'] += t['pnl']
        sym_pnl[sym]['trades'] += 1
        if t['pnl'] > 0:
            sym_pnl[sym]['wins'] += 1

    print(f"\n  종목별 기여")
    for sym, s in sorted(sym_pnl.items(), key=lambda x: -x[1]['pnl']):
        wr = s['wins']/s['trades']*100
        print(f"    {sym:8s}: {s['pnl']:+8,.0f}원 ({s['wins']}/{s['trades']}, {wr:.0f}%)")

    print(f"\n{'=' * 72}\n")


if __name__ == "__main__":
    main()
