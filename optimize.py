"""
파라미터 최적화 스크립트 v2
- 직접 변수 패치 (reload 없이)
- 대표 종목 15개로 1차 스크리닝
- 상위 10개를 전체 종목으로 검증
"""
import os
import sys
import time
from collections import Counter

import pandas as pd

# 모듈 임포트 전에 환경변수 기본값 설정
os.environ.setdefault("BUY_THRESHOLD_UP", "2")
os.environ.setdefault("BUY_THRESHOLD_SW", "3")

import config
from backtest import BacktestEngine, load_local_ohlcv, calculate_indicators

# ── 샘플 종목 (데이터 충분한 대형+중형 혼합) ──
SAMPLE_SYMBOLS = [
    "BTC", "ETH", "XRP", "SOL", "SUI", "DOGE", "ADA",
    "LINK", "AVAX", "DOT", "NEAR", "OP", "ARB", "TAO", "H",
]


def load_data_cache(symbols):
    """데이터를 미리 로드해서 캐싱"""
    cache = {}
    for s in symbols:
        df5 = load_local_ohlcv(s, "5m")
        df1 = load_local_ohlcv(s, "1h")
        if df5 is not None and len(df5) >= 120 and df1 is not None and len(df1) >= 50:
            cache[s] = (df5.copy(), df1.copy())
    return cache


def run_backtest_with_params(data_cache, params):
    """파라미터를 직접 적용하여 백테스트 실행"""
    import config as cfg
    import backtest as bt
    from backtest import (RSI_OVERSOLD, RSI_OVERBOUGHT, RSI_EXTREME_LOW,
                          RSI_EXTREME_HIGH, BB_LOWER_THRESHOLD, BB_UPPER_THRESHOLD)

    # config 모듈 직접 패치
    cfg.RSI_OVERSOLD = params["rsi_oversold"]
    cfg.BB_LOWER_THRESHOLD = params["bb_lower"]
    cfg.STOP_LOSS = params["stop_loss"]
    cfg.ATR_STOP_MIN = params["atr_min"]
    cfg.ATR_STOP_MAX = params["atr_max"]
    cfg.ATR_STOP_MULTIPLIER = params["atr_mult"]
    cfg.TAKE_PROFIT_BASE = params["tp_base"]
    cfg.TAKE_PROFIT_6H = params["tp_6h"]
    cfg.TAKE_PROFIT_12H = params["tp_12h"]
    cfg.TAKE_PROFIT_24H = params["tp_24h"]
    cfg.TRAILING_STOP_TRIGGER = params["tr_trigger"]
    cfg.TRAILING_STOP_DROP = params["tr_drop"]
    cfg.VOLUME_RATIO_MIN = params["vol_min"]

    # 환경변수로 매수 임계값 전달
    os.environ["BUY_THRESHOLD_UP"] = str(params["buy_up"])
    os.environ["BUY_THRESHOLD_SW"] = str(params["buy_sw"])

    results = {}
    for symbol, (df5, df1) in data_cache.items():
        engine = BacktestEngine(initial_balance=cfg.INITIAL_BALANCE)
        # backtest 모듈의 상수도 직접 패치
        bt.RSI_OVERSOLD = params["rsi_oversold"]
        bt.BB_LOWER_THRESHOLD = params["bb_lower"]
        bt.RSI_OVERBOUGHT = cfg.RSI_OVERBOUGHT
        bt.RSI_EXTREME_LOW = cfg.RSI_EXTREME_LOW
        bt.RSI_EXTREME_HIGH = cfg.RSI_EXTREME_HIGH
        bt.BB_UPPER_THRESHOLD = cfg.BB_UPPER_THRESHOLD
        bt.STOP_LOSS = params["stop_loss"]
        bt.ATR_STOP_MIN = params["atr_min"]
        bt.ATR_STOP_MAX = params["atr_max"]
        bt.ATR_STOP_MULTIPLIER = params["atr_mult"]
        bt.TAKE_PROFIT_BASE = params["tp_base"]
        bt.TAKE_PROFIT_6H = params["tp_6h"]
        bt.TAKE_PROFIT_12H = params["tp_12h"]
        bt.TAKE_PROFIT_24H = params["tp_24h"]
        bt.TRAILING_STOP_TRIGGER = params["tr_trigger"]
        bt.TRAILING_STOP_DROP = params["tr_drop"]
        bt.VOLUME_RATIO_MIN = params["vol_min"]

        # stdout 억제
        old_print = __builtins__.__dict__.get("print", print)
        __builtins__.__dict__["print"] = lambda *a, **k: None
        try:
            result = engine.run(symbol, df5.copy(), df1.copy())
        finally:
            __builtins__.__dict__["print"] = old_print

        if result:
            results[symbol] = result

    return results


def score_results(results):
    """결과를 종합 점수로 환산"""
    if not results:
        return -9999, {}

    total_pnl = sum(r["total_pnl"] for r in results.values())
    avg_win = sum(r["win_rate"] for r in results.values()) / len(results)
    worst_mdd = min(r["mdd"] for r in results.values())
    total_trades = sum(r["total_trades"] for r in results.values())
    profitable = sum(1 for r in results.values() if r["total_pnl"] > 0)
    total_fee = sum(r["total_fee"] for r in results.values())

    # 수익 종목 비율
    profit_ratio = profitable / len(results) if results else 0

    # 종합 점수: 수익 + 승률 보너스 + 수익종목비율 - MDD 패널티
    score = (
        total_pnl / 500
        + avg_win * 3
        + profit_ratio * 200
        + worst_mdd * 2  # 음수이므로 패널티
    )

    metrics = {
        "total_pnl": round(total_pnl),
        "avg_win_rate": round(avg_win, 1),
        "worst_mdd": round(worst_mdd, 2),
        "total_trades": total_trades,
        "total_fee": round(total_fee),
        "profitable": profitable,
        "total": len(results),
        "profit_ratio": round(profit_ratio * 100, 1),
    }
    return round(score, 1), metrics


# ── 파라미터 조합 생성 ──
def generate_param_combos():
    combos = []

    entries = [
        (2, 2, "UP2_SW2"),
        (2, 3, "UP2_SW3"),
        (3, 3, "ALL3"),
        (2, 99, "UP_ONLY"),
    ]

    indicators = [
        (40, 0.30, 0.5, "RSI40_BB30_V05"),
        (35, 0.25, 0.5, "RSI35_BB25_V05"),
        (30, 0.20, 0.5, "RSI30_BB20_V05"),
        (35, 0.25, 0.8, "RSI35_BB25_V08"),
    ]

    stoplosses = [
        (-0.04, -0.02, -0.06, 2.0, "TIGHT"),
        (-0.06, -0.03, -0.08, 2.5, "MID"),
        (-0.08, -0.04, -0.10, 3.0, "WIDE"),
    ]

    exits = [
        (0.03, 0.025, 0.02, 0.015, 0.02, 0.01, "TP3"),
        (0.04, 0.035, 0.03, 0.025, 0.025, 0.012, "TP4"),
        (0.05, 0.04, 0.035, 0.03, 0.03, 0.015, "TP5"),
    ]

    for bu, bs, elabel in entries:
        for rsi, bb, vol, ilabel in indicators:
            for sl, amin, amax, amul, slabel in stoplosses:
                for tp, tp6, tp12, tp24, trt, trd, xlabel in exits:
                    label = f"{elabel}|{ilabel}|{slabel}|{xlabel}"
                    combos.append({
                        "label": label,
                        "buy_up": bu, "buy_sw": bs,
                        "rsi_oversold": rsi, "bb_lower": bb, "vol_min": vol,
                        "stop_loss": sl, "atr_min": amin, "atr_max": amax, "atr_mult": amul,
                        "tp_base": tp, "tp_6h": tp6, "tp_12h": tp12, "tp_24h": tp24,
                        "tr_trigger": trt, "tr_drop": trd,
                    })

    return combos


def main():
    combos = generate_param_combos()
    print(f"\n{'='*72}")
    print(f"  파라미터 최적화 v2 (총 {len(combos)}개 조합)")
    print(f"  1차: 샘플 {len(SAMPLE_SYMBOLS)}종목으로 스크리닝")
    print(f"{'='*72}")

    # 데이터 캐싱
    print(f"\n  데이터 로딩 중...")
    cache = load_data_cache(SAMPLE_SYMBOLS)
    print(f"  로드 완료: {len(cache)}종목")

    all_results = []
    start = time.time()

    for i, params in enumerate(combos):
        results = run_backtest_with_params(cache, params)
        score, metrics = score_results(results)

        all_results.append((score, params, metrics))

        elapsed = time.time() - start
        eta = (elapsed / (i + 1)) * (len(combos) - i - 1)
        pnl_s = f"{metrics.get('total_pnl', 0):+,}"
        sys.stdout.write(f"\r  [{i+1:3d}/{len(combos)}] {params['label']:45s} "
                         f"PnL:{pnl_s:>8s} WR:{metrics.get('avg_win_rate',0):5.1f}% "
                         f"Score:{score:7.1f} ETA:{eta:.0f}s")
        sys.stdout.flush()

    print()

    # 정렬
    all_results.sort(key=lambda x: x[0], reverse=True)

    print(f"\n{'='*72}")
    print(f"  🏆 TOP 15 파라미터 조합 (1차 스크리닝)")
    print(f"{'='*72}")
    for i, (score, params, metrics) in enumerate(all_results[:15]):
        pnl_s = f"{metrics['total_pnl']:+,}"
        print(f"  #{i+1:2d} Score:{score:7.1f} | PnL:{pnl_s:>8s} | "
              f"WR:{metrics['avg_win_rate']:5.1f}% | MDD:{metrics['worst_mdd']:6.2f}% | "
              f"수익:{metrics['profitable']}/{metrics['total']} | "
              f"거래:{metrics['total_trades']}회")
        print(f"      {params['label']}")

    # ── 2차: 상위 10개를 전체 종목으로 검증 ──
    print(f"\n{'='*72}")
    print(f"  🔍 2차 검증: 상위 10개를 전체 종목으로 테스트")
    print(f"{'='*72}")

    # 전체 종목 데이터 로드
    all_symbols = []
    for f in os.listdir(config.OHLCV_DATA_DIR):
        if f.endswith("_5m.csv"):
            s = f.replace("_5m.csv", "")
            if s not in config.BLACKLIST:
                all_symbols.append(s)
    all_symbols.sort()
    print(f"\n  전체 종목 데이터 로딩 중... ({len(all_symbols)}종목)")
    full_cache = load_data_cache(all_symbols)
    print(f"  로드 완료: {len(full_cache)}종목")

    final_results = []
    for i, (_, params, _) in enumerate(all_results[:10]):
        sys.stdout.write(f"\r  검증 중... [{i+1}/10] {params['label']:45s}")
        sys.stdout.flush()
        results = run_backtest_with_params(full_cache, params)
        score, metrics = score_results(results)
        final_results.append((score, params, metrics))

    print()
    final_results.sort(key=lambda x: x[0], reverse=True)

    print(f"\n{'='*72}")
    print(f"  🏆🏆 최종 TOP 5 (전체 종목 검증)")
    print(f"{'='*72}")
    for i, (score, params, metrics) in enumerate(final_results[:5]):
        pnl_s = f"{metrics['total_pnl']:+,}"
        print(f"\n  #{i+1} [{params['label']}]")
        print(f"      Score: {score:.1f}")
        print(f"      PnL: {pnl_s}원 | 승률: {metrics['avg_win_rate']:.1f}% | MDD: {metrics['worst_mdd']:.2f}%")
        print(f"      수익종목: {metrics['profitable']}/{metrics['total']} ({metrics['profit_ratio']:.0f}%)")
        print(f"      거래: {metrics['total_trades']}회 | 수수료: {metrics['total_fee']:,}원")
        print(f"      ──────────────────────────────────")
        print(f"      진입: UP={params['buy_up']}/3, SW={params['buy_sw']}/3")
        print(f"      RSI≤{params['rsi_oversold']} BB≤{params['bb_lower']} Vol≥{params['vol_min']}x")
        print(f"      손절: {params['stop_loss']*100:.0f}% (ATR: {params['atr_min']*100:.0f}%~{params['atr_max']*100:.0f}%, x{params['atr_mult']})")
        print(f"      익절: {params['tp_base']*100:.0f}%/{params['tp_6h']*100:.1f}%/{params['tp_12h']*100:.1f}%/{params['tp_24h']*100:.1f}%")
        print(f"      트레일링: 시작 {params['tr_trigger']*100:.0f}%, 하락 {params['tr_drop']*100:.1f}%")

    elapsed = time.time() - start
    print(f"\n  ⏱ 총 소요: {elapsed/60:.1f}분")

    return final_results[0] if final_results else None


if __name__ == "__main__":
    best = main()
