"""
엣지 후보 전략 검증: 구조를 바꾼 접근
A. 모멘텀 로테이션: 매주 상승률 상위 K개 보유 (절대 모멘텀 필터)
B. 추세필터 보유: EMA 위면 보유, 아래면 현금
C. 콤보: 로테이션 + 추세필터
vs Buy-and-hold 베이스라인 / 앞뒤 절반 walk-forward
"""
import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings("ignore")

INITIAL = 1_000_000
COST = 0.0045  # 편도: 수수료 0.25% + 슬리피지 0.2%

SYMBOLS = ["BTC","ETH","XRP","SOL","ADA","DOGE","TRX","BCH","AVAX","DOT",
           "LINK","ATOM","MATIC","ARB","NEAR"]


def load_4h_closes():
    """모든 종목 4H 종가를 시간 정렬된 wide DataFrame으로"""
    frames = {}
    for s in SYMBOLS:
        p = f"data/ohlcv/{s}_1h.csv"
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, parse_dates=["time"]).sort_values("time")
        df = df.set_index("time")
        c = df["close"].resample("4h").last().dropna()
        if len(c) >= 100:
            frames[s] = c
    wide = pd.DataFrame(frames)
    return wide


def momentum_rotation(closes, lookback_bars=84, rebal_bars=42, top_k=2,
                      abs_filter=True, trend_ema=None, start=None, end=None):
    """
    매 rebal_bars마다 트레일링 수익률 상위 top_k 보유.
    abs_filter: 모멘텀 > 0 인 종목만 (아니면 그 슬롯 현금)
    trend_ema: 지정 시 해당 EMA 위에 있는 종목만 보유 허용
    """
    idx = closes.index
    if start is not None:
        idx = idx[idx >= start]
    if end is not None:
        idx = idx[idx <= end]
    if len(idx) < lookback_bars + rebal_bars:
        return None

    ema = closes.ewm(span=trend_ema, adjust=False).mean() if trend_ema else None

    cash = INITIAL
    units = {}  # sym -> units held
    equity_curve = []
    n_trades = 0

    rebal_points = range(lookback_bars, len(idx), rebal_bars)
    for ri in rebal_points:
        t = idx[ri]
        t_look = idx[ri - lookback_bars]

        # 현재 평가액
        port_val = cash + sum(u * closes.at[t, s] for s, u in units.items()
                              if not pd.isna(closes.at[t, s]))

        # 모멘텀 랭킹
        moms = {}
        for s in closes.columns:
            p_now = closes.at[t, s]
            p_then = closes.at[t_look, s] if t_look in closes.index else np.nan
            if pd.isna(p_now) or pd.isna(p_then) or p_then <= 0:
                continue
            m = p_now / p_then - 1
            if abs_filter and m <= 0:
                continue
            if ema is not None:
                e = ema.at[t, s]
                if pd.isna(e) or p_now < e:
                    continue
            moms[s] = m

        target = sorted(moms, key=moms.get, reverse=True)[:top_k]

        # 매도: 타깃에서 빠진 종목
        for s in list(units.keys()):
            if s not in target:
                p = closes.at[t, s]
                if not pd.isna(p):
                    cash += units[s] * p * (1 - COST)
                    n_trades += 1
                del units[s]

        # 매수: 새로 들어온 종목 (동일가중)
        new_syms = [s for s in target if s not in units]
        if new_syms:
            alloc_each = port_val / top_k
            for s in new_syms:
                p = closes.at[t, s]
                if pd.isna(p) or cash < alloc_each * 0.5:
                    continue
                spend = min(alloc_each, cash)
                units[s] = spend * (1 - COST) / p
                cash -= spend
                n_trades += 1

        equity_curve.append((t, cash + sum(u * closes.at[t, s] for s, u in units.items()
                                            if not pd.isna(closes.at[t, s]))))

    # 종료 청산
    t_end = idx[-1]
    for s, u in units.items():
        p = closes.at[t_end, s]
        if not pd.isna(p):
            cash += u * p * (1 - COST)
    final = cash

    eq = pd.Series(dict(equity_curve))
    mdd = ((eq / eq.cummax()) - 1).min() * 100 if len(eq) else 0
    return {"final": final, "ret": (final/INITIAL-1)*100, "trades": n_trades, "mdd": mdd}


def trend_hold(closes, syms, ema_span=100, confirm=2, start=None, end=None):
    """지정 종목 동일가중, 각 종목 EMA 위면 보유 / confirm봉 연속 아래면 현금"""
    idx = closes.index
    if start is not None: idx = idx[idx >= start]
    if end is not None: idx = idx[idx <= end]

    per_alloc = INITIAL / len(syms)
    total_final = 0
    total_trades = 0
    for s in syms:
        c = closes[s].reindex(idx).dropna()
        if len(c) < ema_span + 10:
            total_final += per_alloc  # 데이터 없으면 현금 보유
            continue
        e = c.ewm(span=ema_span, adjust=False).mean()
        below = (c < e)
        cash = per_alloc
        u = 0.0
        held = False
        for i in range(ema_span, len(c)):
            if not held and c.iloc[i] > e.iloc[i]:
                u = cash * (1 - COST) / c.iloc[i]
                cash = 0; held = True; total_trades += 1
            elif held and i >= confirm-1 and all(below.iloc[i-j] for j in range(confirm)):
                cash = u * c.iloc[i] * (1 - COST)
                u = 0; held = False; total_trades += 1
        if held:
            cash = u * c.iloc[-1] * (1 - COST)
        total_final += cash
    return {"final": total_final, "ret": (total_final/INITIAL-1)*100, "trades": total_trades}


def buy_hold(closes, syms, start=None, end=None):
    idx = closes.index
    if start is not None: idx = idx[idx >= start]
    if end is not None: idx = idx[idx <= end]
    per = INITIAL / len(syms)
    final = 0
    for s in syms:
        c = closes[s].reindex(idx).dropna()
        if len(c) < 10:
            final += per; continue
        u = per * (1 - COST) / c.iloc[0]
        final += u * c.iloc[-1] * (1 - COST)
    return {"final": final, "ret": (final/INITIAL-1)*100}


if __name__ == "__main__":
    closes = load_4h_closes()
    print(f"로드: {list(closes.columns)} / {len(closes)}봉 ({closes.index[0]} ~ {closes.index[-1]})")
    mid = closes.index[len(closes)//2]

    print("\n" + "="*95)
    print("  📊 구조 변경 전략 검증 (전체 87일 / 앞절반 / 뒷절반)")
    print("="*95)

    def show(name, full, a, b, extra=""):
        f = f"{full['ret']:+6.2f}%" if full else "  n/a"
        aa = f"{a['ret']:+6.2f}%" if a else "  n/a"
        bb = f"{b['ret']:+6.2f}%" if b else "  n/a"
        tr = f"거래{full.get('trades','-'):>3}" if full and 'trades' in full else ""
        mdd = f"MDD{full.get('mdd',0):>6.1f}%" if full and 'mdd' in full else ""
        print(f"  {name:38} | 전체 {f} | 앞 {aa} | 뒤 {bb} | {tr} {mdd} {extra}")

    # 베이스라인
    bh_all = buy_hold(closes, list(closes.columns))
    bh_a = buy_hold(closes, list(closes.columns), end=mid)
    bh_b = buy_hold(closes, list(closes.columns), start=mid)
    show("[기준] Buy&Hold 전체 동일가중", bh_all, bh_a, bh_b)

    bh2_all = buy_hold(closes, ["BTC","ETH"])
    bh2_a = buy_hold(closes, ["BTC","ETH"], end=mid)
    bh2_b = buy_hold(closes, ["BTC","ETH"], start=mid)
    show("[기준] Buy&Hold BTC/ETH 50:50", bh2_all, bh2_a, bh2_b)

    print("  " + "-"*90)

    # A. 모멘텀 로테이션 변형들
    for lb, rb, k, label in [
        (84, 42, 2, "롯테이션 14d룩백/주간/top2"),
        (126, 42, 2, "로테이션 21d룩백/주간/top2"),
        (84, 42, 3, "로테이션 14d룩백/주간/top3"),
        (126, 84, 2, "로테이션 21d룩백/2주/top2"),
        (42, 42, 2, "로테이션 7d룩백/주간/top2"),
    ]:
        full = momentum_rotation(closes, lb, rb, k)
        a = momentum_rotation(closes, lb, rb, k, end=mid)
        b = momentum_rotation(closes, lb, rb, k, start=mid)
        show(f"A. {label}", full, a, b)

    print("  " + "-"*90)

    # B. 추세필터 보유
    for span, syms, label in [
        (100, ["BTC","ETH"], "추세보유 BTC/ETH EMA100"),
        (50, ["BTC","ETH"], "추세보유 BTC/ETH EMA50"),
        (100, ["BTC","ETH","SOL","XRP"], "추세보유 메이저4 EMA100"),
    ]:
        full = trend_hold(closes, syms, span)
        a = trend_hold(closes, syms, span, end=mid)
        b = trend_hold(closes, syms, span, start=mid)
        show(f"B. {label}", full, a, b)

    print("  " + "-"*90)

    # C. 콤보: 로테이션 + 추세필터
    for lb, k, te, label in [
        (84, 2, 100, "콤보 14d/top2 + EMA100필터"),
        (126, 2, 100, "콤보 21d/top2 + EMA100필터"),
        (84, 3, 50, "콤보 14d/top3 + EMA50필터"),
    ]:
        full = momentum_rotation(closes, lb, 42, k, trend_ema=te)
        a = momentum_rotation(closes, lb, 42, k, trend_ema=te, end=mid)
        b = momentum_rotation(closes, lb, 42, k, trend_ema=te, start=mid)
        show(f"C. {label}", full, a, b)
