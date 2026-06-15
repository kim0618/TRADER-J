# symbol_filter.py
"""
백테스트 결과 기반 동적 화이트/블랙리스트 관리
- data/backtests/ 의 최근 결과를 집계해 종목별 수익성 평가
- whitelist: PF >= 1.2 AND 승률 >= 50% → 종목 선정 시 우선 / 점수 보너스
- blacklist: PF < 0.5 OR 승률 < 30% → 종목 선정 후보에서 완전 제외
"""
from __future__ import annotations

import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

FILTER_PATH = "data/symbol_filter.json"
FILTER_DAYS = 14                  # 30→14: 시장 변화에 빠른 적응
FILTER_MIN_TRADES = 10            # 5→10: 표본 신뢰도 강화
FILTER_MIN_LOSSES = 2             # NEW: 손실 거래 최소 2건 (PF=99 비현실 케이스 차단)
WHITELIST_MIN_PF = 1.5            # 1.2→1.5: 더 엄격한 수익성 기준
WHITELIST_MIN_WIN_RATE = 50.0
BLACKLIST_MAX_PF = 0.7            # 0.5→0.7: 더 적극적 차단
BLACKLIST_MAX_WIN_RATE = 35.0     # 30→35: 더 적극적 차단
WHITELIST_SCORE_BONUS = 20


def load_filter() -> Dict:
    """저장된 필터 로드 (없으면 빈 필터 반환)"""
    if os.path.exists(FILTER_PATH):
        try:
            with open(FILTER_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"whitelist": [], "blacklist": [], "updated_at": None, "stats": {}}


def _aggregate_stats(days: int = FILTER_DAYS) -> Dict:
    """최근 N일 백테스트 JSON 파일에서 종목별 통계 집계"""
    cutoff = datetime.now() - timedelta(days=days)
    stats: Dict[str, Dict] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "wins_pnl": 0.0, "losses_pnl": 0.0, "pnl": 0.0}
    )
    loaded = 0

    # backtest_*.json (v2) + swing_*.json (v3.0) 둘 다 읽기
    files = sorted(glob.glob("data/backtests/backtest_*.json") +
                   glob.glob("data/backtests/swing_*.json"))
    for fpath in files:
        try:
            ts_str = (os.path.basename(fpath)
                      .replace("backtest_", "").replace("swing_", "")
                      .replace(".json", ""))
            ts = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            if ts < cutoff:
                continue
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            for sym, r in data.get("results", {}).items():
                n = r.get("total_trades", 0)
                if n < 1:
                    continue
                wr = r.get("win_rate", 0) / 100
                wins = round(n * wr)
                losses = n - wins
                avg_win = r.get("avg_profit") or 0.0
                avg_loss = abs(r.get("avg_loss") or 0.0)
                s = stats[sym]
                s["trades"] += n
                s["wins"] += wins
                s["wins_pnl"] += avg_win * wins
                s["losses_pnl"] += avg_loss * losses
                s["pnl"] += r.get("total_pnl", 0.0)
            loaded += 1
        except Exception:
            continue

    return dict(stats), loaded


def build_and_save_filter(days: int = FILTER_DAYS) -> Dict:
    """백테스트 통계 집계 → 화이트/블랙리스트 생성 및 저장"""
    raw_stats, loaded = _aggregate_stats(days)

    whitelist: List[str] = []
    blacklist: List[str] = []
    summary: Dict[str, Dict] = {}

    for sym, s in raw_stats.items():
        if s["trades"] < FILTER_MIN_TRADES:
            continue
        wins = s["wins"]
        losses = s["trades"] - wins
        win_rate = wins / s["trades"] * 100
        # 손실 거래 0건이면 PF 계산 불가 → 화이트리스트 부적격
        has_valid_pf = s["losses_pnl"] > 0 and losses >= FILTER_MIN_LOSSES
        pf = s["wins_pnl"] / s["losses_pnl"] if s["losses_pnl"] > 0 else 0.0
        summary[sym] = {
            "trades": s["trades"],
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "pf": round(pf, 3) if has_valid_pf else None,
            "total_pnl": round(s["pnl"], 0),
        }

        # 화이트리스트: 신뢰 가능한 PF + 승률 + 손실 거래 충분히 있음
        if has_valid_pf and pf >= WHITELIST_MIN_PF and win_rate >= WHITELIST_MIN_WIN_RATE:
            whitelist.append(sym)
        # 블랙리스트: 명확히 부진한 종목
        elif (has_valid_pf and pf < BLACKLIST_MAX_PF) or win_rate < BLACKLIST_MAX_WIN_RATE:
            blacklist.append(sym)

    result = {
        "whitelist": sorted(whitelist),
        "blacklist": sorted(blacklist),
        "updated_at": datetime.now().isoformat(),
        "source_files": loaded,
        "stats": summary,
    }

    os.makedirs(os.path.dirname(FILTER_PATH), exist_ok=True)
    with open(FILTER_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  ✅ 심볼 필터 업데이트 완료")
    print(f"     화이트리스트: {len(whitelist)}개 {sorted(whitelist)[:10]}")
    print(f"     블랙리스트:   {len(blacklist)}개 (PF<{BLACKLIST_MAX_PF} 또는 승률<{BLACKLIST_MAX_WIN_RATE}%)")
    print(f"     참조 파일:    {loaded}개")
    return result


def is_blacklisted(symbol: str, filter_data: Optional[Dict] = None) -> bool:
    if filter_data is None:
        filter_data = load_filter()
    return symbol in filter_data.get("blacklist", [])


def is_whitelisted(symbol: str, filter_data: Optional[Dict] = None) -> bool:
    if filter_data is None:
        filter_data = load_filter()
    return symbol in filter_data.get("whitelist", [])


if __name__ == "__main__":
    print("=" * 60)
    print("  심볼 필터 빌드 (최근 30일 백테스트 기반)")
    print("=" * 60)
    result = build_and_save_filter()
    wl = result["whitelist"]
    bl = result["blacklist"]
    print(f"\n  화이트리스트 전체 ({len(wl)}): {wl}")
    print(f"\n  블랙리스트 상위 20: {bl[:20]}")
