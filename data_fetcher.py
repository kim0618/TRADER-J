# data_fetcher.py - 빗썸 데이터 자동 수집기
# 5분마다 실행되며 빗썸 캔들 데이터를 CSV에 저장
import requests
import pandas as pd
import os
import time
import json
from datetime import datetime

BITHUMB_API_URL = "https://api.bithumb.com/public"
DATA_DIR = "data/ohlcv"
CONFIG_PATH = "config.py"

# 수집할 종목 (고정 주요 종목 + 운용 중인 종목 자동 추가)
DEFAULT_SYMBOLS = ["BTC", "XRP", "ETH", "SOL", "DOGE"]

def get_running_tickers():
    """현재 운용 중인 종목 가져오기"""
    try:
        cycle_path = "data/cycle_info.json"
        if os.path.exists(cycle_path):
            with open(cycle_path, "r") as f:
                info = json.load(f)
            return info.get("tickers", [])
    except:
        pass
    return []

def get_all_symbols():
    """수집할 전체 종목 리스트"""
    symbols = list(DEFAULT_SYMBOLS)
    running = get_running_tickers()
    for t in running:
        if t not in symbols:
            symbols.append(t)
    return symbols

def fetch_candles(symbol, interval="5m", count=200):
    """빗썸 캔들 데이터 수집"""
    try:
        url = f"{BITHUMB_API_URL}/candlestick/{symbol}_KRW/{interval}"
        res = requests.get(url, timeout=5)
        data = res.json()
        if data["status"] == "0000":
            df = pd.DataFrame(
                data["data"],
                columns=["time", "open", "close", "high", "low", "volume"]
            )
            df = df.astype({
                "open": float, "close": float,
                "high": float, "low": float, "volume": float
            })
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            df["time"] = df["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
            return df.tail(count)
    except Exception as e:
        print(f"  [오류] {symbol} {interval} 수집 실패: {e}")
    return None

def save_candles(symbol, interval, df):
    """
    CSV에 저장 (중복 제거 후 추가)
    기존 데이터가 있으면 새 데이터만 추가
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{symbol}_{interval}.csv")

    if os.path.exists(path):
        existing = pd.read_csv(path)
        existing_times = set(existing["time"].values)

        # 새로운 데이터만 필터링
        new_rows = df[~df["time"].isin(existing_times)]

        if len(new_rows) == 0:
            return 0

        new_rows.to_csv(path, mode="a", header=False, index=False)
        return len(new_rows)
    else:
        df.to_csv(path, index=False)
        return len(df)

def get_csv_stats(symbol, interval):
    """CSV 파일 통계"""
    path = os.path.join(DATA_DIR, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        return 0, "-", "-"
    try:
        df = pd.read_csv(path)
        count = len(df)
        if count > 0:
            first = df["time"].iloc[0]
            last = df["time"].iloc[-1]
            return count, first, last
    except:
        pass
    return 0, "-", "-"

def print_stats():
    """전체 수집 현황 출력"""
    symbols = get_all_symbols()
    print(f"\n{'='*65}")
    print(f"  📊 데이터 수집 현황")
    print(f"{'='*65}")
    print(f"  {'종목':>6} | {'타임프레임':>8} | {'봉 수':>8} | {'시작':>18} | {'최근':>18}")
    print(f"  " + "-"*63)
    for sym in symbols:
        for interval in ["5m", "1h"]:
            count, first, last = get_csv_stats(sym, interval)
            if count > 0:
                # 시간 계산
                if interval == "5m":
                    hours = count * 5 / 60
                    duration = f"{hours:.0f}시간" if hours < 48 else f"{hours/24:.0f}일"
                else:
                    duration = f"{count}시간" if count < 48 else f"{count/24:.0f}일"
                print(f"  {sym:>6} | {interval:>8} | {count:>6}개({duration}) | {str(first)[:16]:>18} | {str(last)[:16]:>18}")
    print(f"{'='*65}\n")

def run_once():
    """1회 수집 실행"""
    symbols = get_all_symbols()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_new = 0

    for symbol in symbols:
        for interval in ["5m", "1h"]:
            df = fetch_candles(symbol, interval)
            if df is not None:
                new_count = save_candles(symbol, interval, df)
                if new_count > 0:
                    total_new += new_count
                    print(f"  [{symbol}] {interval} +{new_count}개 저장")
            time.sleep(0.2)  # API 과부하 방지

    return total_new

def run():
    print("\n" + "="*65)
    print("  📡 빗썸 데이터 자동 수집기 시작!")
    print(f"  수집 종목: {get_all_symbols()}")
    print(f"  수집 주기: 5분봉, 1시간봉")
    print(f"  저장 위치: {DATA_DIR}/")
    print("="*65)
    print("  종료하려면 Ctrl+C 를 누르세요\n")

    collect_count = 0

    while True:
        try:
            collect_count += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] 🔄 {collect_count}번째 수집 중...")

            new_total = run_once()

            if new_total > 0:
                print(f"  └ 총 {new_total}개 새 데이터 저장 완료")
            else:
                print(f"  └ 새 데이터 없음 (중복)")

            # 10회마다 전체 현황 출력
            if collect_count % 10 == 0:
                print_stats()

            # 5분 대기
            print(f"  └ ⏱ 5분 후 다음 수집...")
            time.sleep(300)

        except KeyboardInterrupt:
            print("\n" + "="*65)
            print("  수집기 종료 중...")
            print_stats()
            print("  종료 완료!")
            break
        except Exception as e:
            print(f"  [오류] {e}, 30초 후 재시도...")
            time.sleep(30)

if __name__ == "__main__":
    import sys

    # 인자로 --stats 주면 현황만 출력
    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        print_stats()
    # 인자로 --once 주면 1회만 수집
    elif len(sys.argv) > 1 and sys.argv[1] == "--once":
        print("1회 수집 실행 중...")
        run_once()
        print_stats()
    else:
        run()
