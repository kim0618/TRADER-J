# walkforward.py - Walk-Forward 검증
"""
in-sample 종목 선별의 실제 예측력 검증
- 전체 데이터로 백테스트 1회 실행 (EMA 워밍업 보장)
- 트레이드 시간 기준으로 분할:
   - TRAIN 구간 트레이드만 모아서 종목별 통계 → 화이트리스트
   - TEST 구간에서 화이트리스트 종목만의 성과 vs 전체 종목 성과 비교
"""
import pandas as pd
from datetime import timedelta

from backtest import BacktestEngine, load_local_ohlcv, resolve_symbols


# 분할 비율: 최근 30일을 TEST, 그 이전 모두 TRAIN
TEST_DAYS = 30


def run_full_backtest(symbols):
    """전체 데이터로 1회 백테스트, 종목별 trade list 반환"""
    per_symbol = {}
    for sym in symbols:
        df_1h = load_local_ohlcv(sym, "1h")
        if df_1h is None:
            continue
        engine = BacktestEngine()
        result = engine.run(sym, df_1h=df_1h)
        if result is None:
            continue
        # trades 와 시간을 저장
        per_symbol[sym] = {
            "trades": engine.trades,
            "result": result,
        }
    return per_symbol


def compute_period_stats(trades, period_start: pd.Timestamp, period_end: pd.Timestamp):
    """주어진 시간 범위의 거래만 모아 통계 산출 (Sharpe 포함)"""
    # BUY-SELL 페어로 묶기
    period_sells = []
    for t in trades:
        t_time = pd.to_datetime(t["time"])
        if t["action"] == "SELL" and period_start <= t_time <= period_end:
            period_sells.append(t)

    if not period_sells:
        return None

    wins = [t for t in period_sells if t["pnl"] > 0]
    losses = [t for t in period_sells if t["pnl"] < 0]
    total_pnl = sum(t["pnl"] for t in period_sells)
    win_rate = len(wins) / len(period_sells) * 100
    win_sum = sum(t["pnl"] for t in wins)
    loss_sum = abs(sum(t["pnl"] for t in losses))
    pf = (win_sum / loss_sum) if loss_sum > 0 else (float("inf") if win_sum > 0 else 0)

    # Sharpe (거래 단위 - per-trade return std 기반, 연환산)
    # 거래당 수익률 = pnl / position_size 추정 (alloc 25% × 100만원 = 25만원)
    sharpe = None
    if len(period_sells) >= 3:
        position_size = 250_000  # 25% × 1M
        returns = [t["pnl"] / position_size for t in period_sells]
        mean_r = sum(returns) / len(returns)
        if len(returns) > 1:
            var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
            std = var ** 0.5
            if std > 0:
                # 연환산: 거래 빈도 기반 (period_days 동안 N건 → 연 (365/period_days)*N 건)
                period_days = (period_end - period_start).total_seconds() / 86400
                if period_days > 0:
                    trades_per_year = len(period_sells) * 365 / period_days
                    sharpe = (mean_r / std) * (trades_per_year ** 0.5)

    return {
        "trades": len(period_sells),
        "wins": len(wins),
        "losses": len(losses),
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "profit_factor": pf if pf != float("inf") else None,
        "sharpe": sharpe,
        "avg_profit": (win_sum / len(wins)) if wins else 0,
        "avg_loss": (sum(t["pnl"] for t in losses) / len(losses)) if losses else 0,
    }


def main():
    symbols = resolve_symbols(None, all_collected=True)
    print(f"\n전체 대상: {len(symbols)}종목")
    print(f"분할: TRAIN (전체-최근 {TEST_DAYS}일) / TEST (최근 {TEST_DAYS}일)\n")

    # 1) 전체 백테스트
    print("=" * 72)
    print("Step 1: 전체 기간 백테스트")
    print("=" * 72)
    per_symbol = run_full_backtest(symbols)
    print(f"  실행 완료: {len(per_symbol)}개 종목")

    # 2) 분할 시점 계산 (모든 종목의 데이터 최신 시점 기준)
    latest_times = []
    for sym, data in per_symbol.items():
        if data["trades"]:
            latest_times.append(pd.to_datetime(data["trades"][-1]["time"]))
    if not latest_times:
        print("거래 없음")
        return
    overall_end = max(latest_times)
    split_time = overall_end - pd.Timedelta(days=TEST_DAYS)
    overall_start = overall_end - pd.Timedelta(days=365)  # 1년치까지

    print(f"  분할 시점: {split_time}")
    print(f"  TRAIN: {overall_start.date()} ~ {split_time.date()}")
    print(f"  TEST:  {split_time.date()} ~ {overall_end.date()}")

    # 3) TRAIN 기간 종목별 통계 → 화이트리스트 선정
    print(f"\n{'=' * 72}")
    print("Step 2: TRAIN 구간 통계 → 화이트리스트 선정")
    print("=" * 72)
    train_stats = {}
    for sym, data in per_symbol.items():
        s = compute_period_stats(data["trades"], overall_start, split_time)
        if s and s["trades"] >= 3:
            train_stats[sym] = s

    # 화이트리스트 v4.1: PF ≥ 1.5 + 승률 ≥ 50% + 거래 ≥ 3회 (v3.0 검증 기준)
    # 보조: Sharpe 양수 (위험조정 수익 양수 보장)
    whitelist = []
    for sym, s in train_stats.items():
        pf = s["profit_factor"] or 0
        sharpe = s.get("sharpe") or 0
        if (pf >= 1.5 and s["win_rate"] >= 50
                and s["trades"] >= 3 and sharpe > 0):
            whitelist.append((sym, s))
    # PF 정렬
    whitelist.sort(key=lambda x: -(x[1].get('profit_factor') or 0))

    print(f"  TRAIN 구간 거래 발생: {len(train_stats)}개")
    print(f"  화이트리스트 (PF≥1.5 + Win≥50% + 거래≥3 + Sharpe+): {len(whitelist)}개")
    if whitelist:
        for sym, s in whitelist[:15]:
            pf = s['profit_factor'] or 0
            sh = s.get('sharpe') or 0
            print(f"    {sym:8s} | TRAIN PF {pf:5.2f} | Sharpe {sh:5.2f} | Win {s['win_rate']:5.1f}% | "
                  f"{s['trades']:2d}회 | PnL {s['total_pnl']:+,.0f}")

    if not whitelist:
        print("화이트리스트 비어있음, 종료")
        return

    wl_syms = [s for s, _ in whitelist]

    # 4) TEST 기간 화이트리스트 vs 전체 비교
    print(f"\n{'=' * 72}")
    print(f"Step 3: TEST 구간 성과 비교")
    print("=" * 72)

    # 화이트리스트
    wl_stats = []
    for sym in wl_syms:
        if sym not in per_symbol:
            continue
        s = compute_period_stats(per_symbol[sym]["trades"], split_time, overall_end)
        if s:
            wl_stats.append((sym, s))

    # 전체
    all_stats = []
    for sym, data in per_symbol.items():
        s = compute_period_stats(data["trades"], split_time, overall_end)
        if s and s["trades"] > 0:
            all_stats.append((sym, s))

    def summary(label, stats):
        if not stats:
            print(f"\n  [{label}] 거래 없음")
            return
        traded = [s for _, s in stats]
        avg_pnl_rate_pct = sum(s["total_pnl"] for s in traded) / 1_000_000 / len(traded) * 100
        avg_wr = sum(s["win_rate"] for s in traded) / len(traded)
        pfs = [s["profit_factor"] for s in traded if s["profit_factor"]]
        avg_pf = sum(pfs) / len(pfs) if pfs else 0
        winners = [s for s in traded if s["total_pnl"] > 0]
        total_pnl = sum(s["total_pnl"] for s in traded)
        total_trades = sum(s["trades"] for s in traded)

        print(f"\n  [{label}]")
        print(f"    종목 수:       {len(stats)}개")
        print(f"    총 거래:       {total_trades}회")
        print(f"    수익 종목:     {len(winners)}/{len(traded)} = {len(winners)/len(traded)*100:.0f}%")
        print(f"    종목당 평균 손익: {total_pnl/len(traded):+,.0f}원 ({avg_pnl_rate_pct:+.2f}%)")
        print(f"    평균 승률:     {avg_wr:.1f}%")
        print(f"    평균 PF:       {avg_pf:.2f}")
        if total_pnl > 0:
            return total_pnl, avg_pf
        return total_pnl, avg_pf

    a = summary(f"TEST: 화이트리스트 {len(wl_syms)}종 (TRAIN 통과)", wl_stats)
    b = summary(f"TEST: 전체 {len(all_stats)}종 (필터 X)", all_stats)

    # 종목별 TEST 성과 상세 (화이트리스트만)
    if wl_stats:
        print(f"\n  [화이트리스트 TEST 상세]")
        for sym, s in sorted(wl_stats, key=lambda x: -x[1]['total_pnl']):
            pf = s['profit_factor']
            pf_str = f"{pf:.2f}" if pf else "inf/n/a"
            print(f"    {sym:8s} | PnL {s['total_pnl']:+8,.0f} | Win {s['win_rate']:5.1f}% | "
                  f"PF {pf_str:>7s} | {s['trades']:2d}회")

    print(f"\n{'=' * 72}")


if __name__ == "__main__":
    main()
