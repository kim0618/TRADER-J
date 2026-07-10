# config.py - Hybrid Regime Strategy v4.1
# 백테스트 검증 (140일, 강세+약세 풀사이클): 하이브리드 +6.85% (연환산 +17.9%)
#   vs B&H BTC/ETH -8.7% / v4.0 스윙 단독 +1.4%
# 구조: 레짐 UP = BTC/ETH 코어 보유 / 레짐 DOWN = 코어 청산 + v4.0 스윙만
# v4.0 스윙 = Ensemble AND (추세 풀백 + MACD 일치) + 4시간봉, DOWN레짐 성적 4건 PF 8.39

# ── 레짐 감지 (v4.1 신설) ──────────────────────────────────────
REGIME_ENABLED = True
REGIME_SYMBOL = "BTC"         # 레짐 판단 기준 종목
REGIME_EMA_SPAN = 20          # 일봉 EMA 기간
REGIME_BAND = 0.01            # 히스테리시스 밴드 ±1% (휩쏘 억제)
REGIME_CONFIRM_DAYS = 2       # UP→DOWN 전환 확인 일수
CORE_TICKERS = ["BTC", "ETH"] # UP 레짐 코어 보유 종목

# 초기 자금
INITIAL_BALANCE = 1_000_000

# 종목 설정 (분산 효과: 15종목까지 확대해서 PF 올림)
TOP_TICKER_LIMIT = 4          # 4종목 동시 보유
ALLOC_PER_TICKER = 0.25       # 종목당 자금 25%

# 정적 블랙리스트 (스테이블코인)
BLACKLIST = ["USDT", "USDC", "DAI", "TUSD", "BUSD", "WBTC", "WETH"]

# 최소 가격
MIN_PRICE_FILTER = 100

# ── 이동평균 (v4.0: EMA 20/50/100) ─────────────────────────────
EMA_FAST = 20         # 4h 단기
EMA_MID = 50          # 4h 중기
EMA_LONG = 100        # 4h 장기 (추세 필터)
SHORT_MA = 20         # 호환용
LONG_MA = 50          # 호환용

# ── RSI (14 표준) ──────────────────────────────────────────────
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_PULLBACK_LOW = 40
RSI_PULLBACK_HIGH = 60
RSI_EXTREME_LOW = 25
RSI_EXTREME_HIGH = 75

# ── MACD (12-26-9 표준) ────────────────────────────────────────
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ── Donchian (v4.0 미사용, 호환용) ────────────────────────────
DONCHIAN_PERIOD = 20
BREAKOUT_BUFFER = 0.003
BREAKOUT_VOLUME_MIN = 1.5

# ── ADX ────────────────────────────────────────────────────────
ADX_PERIOD = 14
ADX_BREAKOUT_MIN = 25
ADX_TREND_THRESHOLD = 20
ADX_RANGE_THRESHOLD = 20
ADX_MIN_FOR_TREND_BUY = 20   # v4.0 Ensemble AND에서 사용

# ── 볼린저밴드 (호환용) ─────────────────────────────────────────
BB_PERIOD = 20
BB_STD = 2.0
BB_LOWER_THRESHOLD = 0.15
BB_UPPER_THRESHOLD = 0.85
BB_WIDTH_MIN = 0.003

# ── 거래량 필터 ────────────────────────────────────────────────
VOLUME_RATIO_MIN = 0.7       # 스윙 완화 (최소 평균 70%)
BUY_VOLUME_MIN = 1.0         # 매수 시 평균 이상

# ── 매매 설정 ─────────────────────────────────────────────────
TRADE_RATIO = 1.0            # 단일 진입 100%
MAX_BUY_COUNT = 1            # DCA 비활성화
SELL_RATIO = 1.0
FEE_RATE = 0.0025
SLIPPAGE_RATE = 0.002

# DCA 비활성
DCA_DROP_1 = -0.99

# 호환용
BUY_SIGNAL_REQUIRED_UP = 1
BUY_SIGNAL_REQUIRED_SIDEWAYS = 1
BREAKOUT_ENABLED = False     # v4.0에서 돌파 로직 미사용

# ── 익절 (스윙 v4.0: 검증된 +6% 절반 청산) ─────────────────────
TAKE_PROFIT_BASE = 0.06      # 기본 +6% (검증 결과 최적)
TAKE_PROFIT_6H = 0.055
TAKE_PROFIT_12H = 0.05
TAKE_PROFIT_24H = 0.04

# ── 시간 청산 ──────────────────────────────────────────────────
FORCE_SELL_HOURS = 10 * 24    # 10일 강제 청산
TIME_STOP_HOURS = 5 * 24      # 5일 이상 보유
TIME_STOP_THRESHOLD = 0.015   # ±1.5% 이내 정리

# ── 트레일링 스탑 ──────────────────────────────────────────────
TRAILING_STOP_TRIGGER = 0.07  # +7% 활성
TRAILING_STOP_DROP = 0.03     # -3% 하락 시 청산
USE_PURE_ATR_TRAILING = False
ATR_TRAIL_MULTIPLIER = 3.0
TRAIL_USE_CLOSE = False

# ── 손절 (v4.0: 검증된 ATR × 1.5) ─────────────────────────────
STOP_LOSS = -0.035           # 기본 -3.5%
ATR_STOP_MULTIPLIER = 1.5    # 3.0 → 1.5 (검증됨)
ATR_STOP_MIN = -0.025        # 최소 -2.5%
ATR_STOP_MAX = -0.06         # 최대 -6.0%

# 종목 교체 손절
REPLACE_LOSS_THRESHOLD = -0.04

# ── 쿨다운 / 연속 손절 ─────────────────────────────────────────
SELL_COOLDOWN_MINUTES = 480  # 8시간
CONSECUTIVE_SL_LIMIT = 2
CONSECUTIVE_SL_BLACKLIST_HOURS = 48

# 글로벌 손절
GLOBAL_STOP_LOSS = -0.08     # 전체 -8%

# 최소 보유 시간
MIN_HOLD_MINUTES = 60

# 전략매도 활성화 (EMA50 이탈 청산)
STRATEGY_SELL_ENABLED = True

# 1D 추세 필터 (v4.0에서는 미사용, EMA 3정렬로 대체)
DAILY_TREND_FILTER = False

# ── 동적 화이트/블랙리스트 (v4.0: 완화) ───────────────────────
SYMBOL_FILTER_ENABLED = True
FILTER_WHITELIST_MIN_PF = 1.2       # 2.0 → 1.2 완화 (스윙 특성상 PF 낮음)
FILTER_WHITELIST_MIN_WIN_RATE = 40  # 50 → 40
FILTER_BLACKLIST_MAX_PF = 0.6       # 0.7 → 0.6
FILTER_BLACKLIST_MAX_WIN_RATE = 25
FILTER_MIN_TRADES = 5               # 10 → 5
FILTER_MIN_LOSSES = 1               # 2 → 1
FILTER_WHITELIST_SCORE_BONUS = 20

# ── 종목 선정 풀 (검증: 15종목 최적) ───────────────────────────
MAJOR_TICKERS = [
    "BTC", "ETH", "XRP", "SOL", "DOGE", "ADA",
    "LINK", "AVAX", "DOT", "TRX", "BCH", "LTC",
    "ATOM", "NEAR", "ARB", "MATIC",
]
RESTRICT_TO_PROVEN_TICKERS = False  # False: 후보 넓게 (분산 효과)
CANDIDATE_MIN_VOLUME = 3_000_000_000  # 30억 (유동성 확보)

# ── 캔들 설정 (v4.0: 4시간봉 최적) ─────────────────────────────
CANDLE_INTERVAL = "4h"          # 스윙 최적 (검증)
CANDLE_AGGREGATE_FROM_1H = 4    # 1H → 4H 집계 배수
CANDLE_COUNT = 300              # 4h × 300 = 50일치

# 종목 재선정
TICKER_REFRESH_CYCLES = 3       # 3사이클 = 6시간마다 (스윙은 자주 안 바꿈)
REPLACE_HOLD_HOURS = 48         # 교체 대기 48시간

# 실행 주기 (스윙 4h: 2시간 체크)
INTERVAL_SECONDS = 7200         # 2시간

# 데이터/백테스트
OHLCV_DATA_DIR = "data/ohlcv"
BACKTEST_RESULTS_DIR = "data/backtests"
DATA_FETCH_TOP_VOLUME_LIMIT = 30
FETCH_5M_COUNT = 500
FETCH_1H_COUNT = 2200
BACKTEST_MIN_5M_BARS = 120
BACKTEST_MIN_1H_BARS = 24 * 30
BACKTEST_MIN_6H_BARS = 4 * 30

# 파일 경로
LOG_PATH = "logs/bot.log"
PORTFOLIO_PATH = "data/portfolio.json"
COOLDOWN_PATH = "data/cooldowns.json"
