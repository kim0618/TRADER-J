# data_fetcher.py - 빗썸 데이터 자동 수집기
# 5분봉/1시간봉을 CSV로 누적 저장하여 백테스트 자산으로 활용
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd
import requests

from config import (
    OHLCV_DATA_DIR,
    DATA_FETCH_TOP_VOLUME_LIMIT,
    FETCH_5M_COUNT,
    FETCH_1H_COUNT,
    BACKTEST_MIN_5M_BARS,
    BACKTEST_MIN_1H_BARS,
)

BITHUMB_API_URL = "https://api.bithumb.com/public"
BITHUMB_V1_URL = "https://api.bithumb.com/v1/candles"
DEFAULT_SYMBOLS = ["BTC", "XRP", "ETH", "SOL", "DOGE"]
REQUEST_TIMEOUT = 5
BACKFILL_PAGE_SIZE = 200
BACKFILL_REQUEST_DELAY = 0.25


def _safe_json_load(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"  [경고] JSON 로드 실패 ({path}): {e}")
    return default


def get_running_tickers() -> List[str]:
    """현재 운용 중인 종목 가져오기"""
    info = _safe_json_load("data/cycle_info.json", {})
    tickers = info.get("tickers", [])
    return [str(t).upper() for t in tickers if t]


def get_top_volume_symbols(limit: int = DATA_FETCH_TOP_VOLUME_LIMIT) -> List[str]:
    """거래대금 상위 종목 조회"""
    try:
        url = f"{BITHUMB_API_URL}/ticker/ALL_KRW"
        res = requests.get(url, timeout=REQUEST_TIMEOUT)
        data = res.json()
        if data.get("status") != "0000":
            return []

        rows = []
        for symbol, item in data.get("data", {}).items():
            if symbol == "date" or not isinstance(item, dict):
                continue
            try:
                value_24h = float(item.get("acc_trade_value_24H", 0) or 0)
                rows.append((symbol.upper(), value_24h))
            except Exception:
                continue
        rows.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in rows[: max(0, limit)]]
    except Exception as e:
        print(f"  [경고] 거래대금 상위 종목 조회 실패: {e}")
        return []


def get_all_symbols(limit: int = DATA_FETCH_TOP_VOLUME_LIMIT) -> List[str]:
    """수집할 전체 종목 리스트"""
    merged = []
    for symbol in DEFAULT_SYMBOLS + get_running_tickers() + get_top_volume_symbols(limit):
        symbol = str(symbol).upper()
        if symbol and symbol not in merged:
            merged.append(symbol)
    return merged


def fetch_candles(symbol: str, interval: str = "5m", count: Optional[int] = None) -> Optional[pd.DataFrame]:
    """빗썸 캔들 데이터 수집"""
    if count is None:
        count = FETCH_5M_COUNT if interval == "5m" else FETCH_1H_COUNT

    try:
        url = f"{BITHUMB_API_URL}/candlestick/{symbol}_KRW/{interval}"
        res = requests.get(url, timeout=REQUEST_TIMEOUT)
        data = res.json()
        if data.get("status") == "0000":
            df = pd.DataFrame(
                data["data"],
                columns=["time", "open", "close", "high", "low", "volume"],
            )
            df = df.astype(
                {
                    "open": float,
                    "close": float,
                    "high": float,
                    "low": float,
                    "volume": float,
                }
            )
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            df = df.sort_values("time").drop_duplicates(subset=["time"], keep="last")
            return df.tail(count).reset_index(drop=True)
    except Exception as e:
        print(f"  [오류] {symbol} {interval} 수집 실패: {e}")
    return None


def _csv_path(symbol: str, interval: str) -> str:
    os.makedirs(OHLCV_DATA_DIR, exist_ok=True)
    return os.path.join(OHLCV_DATA_DIR, f"{symbol}_{interval}.csv")


def save_candles(symbol: str, interval: str, df: pd.DataFrame) -> int:
    """CSV에 저장 (병합 + 시간 정렬 + 중복 제거)"""
    path = _csv_path(symbol, interval)
    incoming = df.copy()
    incoming["time"] = pd.to_datetime(incoming["time"])

    if os.path.exists(path):
        try:
            existing = pd.read_csv(path)
            existing["time"] = pd.to_datetime(existing["time"])
        except Exception:
            existing = pd.DataFrame(columns=incoming.columns)
    else:
        existing = pd.DataFrame(columns=incoming.columns)

    before = len(existing)
    merged = pd.concat([existing, incoming], ignore_index=True)
    merged["time"] = pd.to_datetime(merged["time"], errors="coerce")
    merged = merged.dropna(subset=["time"])
    merged = merged.sort_values("time").drop_duplicates(subset=["time"], keep="last")
    after = len(merged)
    added = max(0, after - before)
    merged["time"] = merged["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    merged.to_csv(path, index=False)
    return added


def get_csv_stats(symbol: str, interval: str):
    path = _csv_path(symbol, interval)
    if not os.path.exists(path):
        return 0, "-", "-", False
    try:
        df = pd.read_csv(path)
        count = len(df)
        if count > 0:
            first = df["time"].iloc[0]
            last = df["time"].iloc[-1]
            ready = count >= (BACKTEST_MIN_5M_BARS if interval == "5m" else BACKTEST_MIN_1H_BARS)
            return count, first, last, ready
    except Exception as e:
        print(f"  [경고] CSV 통계 조회 실패 ({symbol} {interval}): {e}")
    return 0, "-", "-", False


def print_stats(limit: int = DATA_FETCH_TOP_VOLUME_LIMIT):
    symbols = get_all_symbols(limit)
    print("\n" + "=" * 78)
    print("  📊 데이터 수집 현황 / 백테스트 준비 상태")
    print("=" * 78)
    print(f"  {'종목':>6} | {'타임프레임':>8} | {'봉 수':>9} | {'준비':>6} | {'시작':>18} | {'최근':>18}")
    print("  " + "-" * 76)
    for sym in symbols:
        for interval in ["5m", "1h"]:
            count, first, last, ready = get_csv_stats(sym, interval)
            status = "OK" if ready else "-"
            if count > 0:
                if interval == "5m":
                    hours = count * 5 / 60
                    duration = f"{hours:.0f}h" if hours < 48 else f"{hours/24:.1f}d"
                else:
                    duration = f"{count}h" if count < 48 else f"{count/24:.1f}d"
                print(
                    f"  {sym:>6} | {interval:>8} | {count:>6}개({duration:>5}) | {status:>6} | {str(first)[:16]:>18} | {str(last)[:16]:>18}"
                )
    print("=" * 78 + "\n")


def fetch_candles_history_v1(symbol: str, interval: str = "5m", target_days: int = 14) -> Optional[pd.DataFrame]:
    """빗썸 v1 API로 과거 캔들 데이터 페이지네이션 수집"""
    if interval == "5m":
        url = f"{BITHUMB_V1_URL}/minutes/5"
    elif interval == "1h":
        url = f"{BITHUMB_V1_URL}/minutes/60"
    else:
        return None

    market = f"KRW-{symbol}"
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=target_days)
    all_rows = []
    to_time: Optional[datetime] = None

    while True:
        params: dict = {"market": market, "count": BACKFILL_PAGE_SIZE}
        if to_time:
            params["to"] = to_time.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            res = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            data = res.json()
        except Exception as e:
            print(f"  [오류] {symbol} {interval} v1 요청 실패: {e}")
            break

        if not isinstance(data, list) or len(data) == 0:
            break

        for c in data:
            all_rows.append({
                "time": c["candle_date_time_utc"],
                "open": float(c["opening_price"]),
                "close": float(c["trade_price"]),
                "high": float(c["high_price"]),
                "low": float(c["low_price"]),
                "volume": float(c["candle_acc_trade_volume"]),
            })

        oldest_str = data[-1]["candle_date_time_utc"]
        oldest_time = datetime.fromisoformat(oldest_str)

        if oldest_time <= cutoff:
            break

        to_time = oldest_time
        time.sleep(BACKFILL_REQUEST_DELAY)

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows)
    df["time"] = pd.to_datetime(df["time"])
    df = df[df["time"] >= pd.Timestamp(cutoff)]
    df = df.sort_values("time").drop_duplicates(subset=["time"], keep="last")
    return df.reset_index(drop=True)


def run_backfill(limit: int = 30, days: int = 14):
    """거래대금 상위 종목 과거 데이터 백필"""
    symbols = get_all_symbols(limit)
    print("\n" + "=" * 78)
    print(f"  📥 역사 데이터 백필 시작")
    print(f"  대상: {len(symbols)}개 종목 | 기간: 최근 {days}일 | 인터벌: 5m, 1h")
    print("=" * 78)

    total_added = 0
    for i, symbol in enumerate(symbols, 1):
        sym_added = 0
        for interval in ["5m", "1h"]:
            df = fetch_candles_history_v1(symbol, interval, target_days=days)
            if df is not None and not df.empty:
                added = save_candles(symbol, interval, df)
                sym_added += added
                total_added += added
            time.sleep(BACKFILL_REQUEST_DELAY)
        print(f"  [{i:>2}/{len(symbols)}] {symbol:>8} | +{sym_added}개 추가")

    print("=" * 78)
    print(f"  백필 완료: 총 {total_added}개 저장")
    print_stats(limit)


def run_once(limit: int = DATA_FETCH_TOP_VOLUME_LIMIT) -> int:
    symbols = get_all_symbols(limit)
    total_new = 0

    for symbol in symbols:
        for interval in ["5m", "1h"]:
            df = fetch_candles(symbol, interval)
            if df is not None and not df.empty:
                new_count = save_candles(symbol, interval, df)
                if new_count > 0:
                    total_new += new_count
                    print(f"  [{symbol}] {interval} +{new_count}개 저장")
            time.sleep(0.15)

    return total_new


def run(limit: int = DATA_FETCH_TOP_VOLUME_LIMIT):
    print("\n" + "=" * 78)
    print("  📡 빗썸 데이터 자동 수집기 시작")
    print(f"  기본 종목: {DEFAULT_SYMBOLS}")
    print(f"  추가 수집: 거래대금 상위 {limit}개 + 운용 중 종목")
    print(f"  저장 위치: {OHLCV_DATA_DIR}/")
    print("=" * 78)
    print("  종료하려면 Ctrl+C 를 누르세요\n")

    collect_count = 0
    while True:
        try:
            collect_count += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] 🔄 {collect_count}번째 수집 중...")
            new_total = run_once(limit)
            if new_total > 0:
                print(f"  └ 총 {new_total}개 새 데이터 저장 완료")
            else:
                print("  └ 새 데이터 없음 (중복)")

            if collect_count % 10 == 0:
                print_stats(limit)

            print("  └ ⏱ 5분 후 다음 수집...")
            time.sleep(300)
        except KeyboardInterrupt:
            print("\n" + "=" * 78)
            print("  수집기 종료 중...")
            print_stats(limit)
            print("  종료 완료!")
            break
        except Exception as e:
            print(f"  [오류] {e}, 30초 후 재시도...")
            time.sleep(30)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="빗썸 OHLCV 데이터 수집기")
    parser.add_argument("--stats", action="store_true", help="현재 수집 현황만 출력")
    parser.add_argument("--once", action="store_true", help="1회만 수집")
    parser.add_argument("--backfill", action="store_true", help="과거 데이터 백필 (v1 API 페이지네이션)")
    parser.add_argument("--backfill-days", type=int, default=14, help="백필 기간 (기본 14일)")
    parser.add_argument(
        "--limit",
        type=int,
        default=DATA_FETCH_TOP_VOLUME_LIMIT,
        help="거래대금 상위 추가 수집 종목 수",
    )
    args = parser.parse_args()

    if args.stats:
        print_stats(args.limit)
    elif args.once:
        print("1회 수집 실행 중...")
        run_once(args.limit)
        print_stats(args.limit)
    elif args.backfill:
        run_backfill(limit=args.limit, days=args.backfill_days)
    else:
        run(args.limit)
