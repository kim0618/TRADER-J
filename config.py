# config.py - Swing Strategy v3.0
# 단타 → 스윙 전환: 1H 시그널, 1D 추세 필터, 보유 1~14일

# 초기 자금 (전체 통합 자금)
INITIAL_BALANCE = 1_000_000

# 종목 설정
TOP_TICKER_LIMIT = 4          # 4종목 동시 보유
ALLOC_PER_TICKER = 0.25       # 종목당 자금 배분 25%

# 블랙리스트 (스테이블코인, 래핑 토큰 등 트레이딩 부적합 종목)
BLACKLIST = ["USDT", "USDC", "DAI", "TUSD", "BUSD", "WBTC", "WETH"]

# 최소 가격 필터
MIN_PRICE_FILTER = 100

# ── 이동평균 (스윙용) ────────────────────────────────────────────
EMA_FAST = 9          # 1H 단기 EMA
EMA_MID = 21          # 1H 중기 EMA
EMA_LONG = 50         # 1D 장기 EMA (추세 필터)
SHORT_MA = 9          # 호환용 (구 SHORT_MA)
LONG_MA = 21          # 호환용

# ── RSI (14, 표준) ──────────────────────────────────────────────
RSI_PERIOD = 14
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65
RSI_PULLBACK_LOW = 45     # 풀백 매수 하한 (35 → 45: 약한 약세 잡지 않음)
RSI_PULLBACK_HIGH = 60    # 풀백 매수 상한 (55 → 60)
RSI_EXTREME_LOW = 25      # 호환용
RSI_EXTREME_HIGH = 75     # 호환용

# ── MACD (표준 12-26-9) ────────────────────────────────────────
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ── Donchian 돌파 ──────────────────────────────────────────────
DONCHIAN_PERIOD = 24      # 24봉 (1일 단위) 고가 돌파 (20 → 24)
BREAKOUT_BUFFER = 0.003   # 0.3% 돌파 버퍼 (0.1% → 0.3%, 노이즈 회피)
BREAKOUT_VOLUME_MIN = 2.0 # 돌파 최소 거래량 배율 (1.5 → 2.0)

# ── ADX (추세 강도) ─────────────────────────────────────────────
ADX_PERIOD = 14
ADX_BREAKOUT_MIN = 25     # 돌파 매수 최소 ADX (20 → 25 강화)
ADX_TREND_THRESHOLD = 25
ADX_RANGE_THRESHOLD = 20

# ── 볼린저밴드 (호환용, 일부 backtest 모듈) ─────────────────────
BB_PERIOD = 20
BB_STD = 2.0
BB_LOWER_THRESHOLD = 0.15
BB_UPPER_THRESHOLD = 0.85
BB_WIDTH_MIN = 0.003

# ── 거래량 필터 ────────────────────────────────────────────────
VOLUME_RATIO_MIN = 0.7       # 평균 70% 이상 (스윙은 다소 완화)
BUY_VOLUME_MIN = 1.0         # 매수 진입 최소 거래량 배율

# ── 매매 설정 ─────────────────────────────────────────────────
TRADE_RATIO = 1.0            # 종목당 1회 매수 비율 100% (스윙은 단일 풀 진입)
MAX_BUY_COUNT = 1            # DCA 비활성화
SELL_RATIO = 1.0
FEE_RATE = 0.0025            # 빗썸 default 0.25% (쿠폰 적용 시 별도 조정)
SLIPPAGE_RATE = 0.002        # 슬리피지 0.2%

# DCA 비활성 (호환용 더미)
DCA_DROP_1 = -0.99

# 진입 조건 강화 (호환용 더미)
BUY_SIGNAL_REQUIRED_UP = 1
BUY_SIGNAL_REQUIRED_SIDEWAYS = 1

# 브레이크아웃 사용 여부 (호환용)
BREAKOUT_ENABLED = True

# ── 익절 (스윙: 큰 익절 폭) ────────────────────────────────────
TAKE_PROFIT_BASE = 0.15      # 기본 익절 +15% (스윙은 큰 폭 노림)
TAKE_PROFIT_6H = 0.13
TAKE_PROFIT_12H = 0.12
TAKE_PROFIT_24H = 0.10

# ── 시간 청산 ──────────────────────────────────────────────────
FORCE_SELL_HOURS = 14 * 24   # 14일 강제 청산
TIME_STOP_HOURS = 7 * 24     # 7일 이상 보유
TIME_STOP_THRESHOLD = 0.02   # 수익률 ±2% 이내면 정리

# ── 트레일링 스탑 (v3.0: 검증 결과 ATR 보다 고정 % 우수) ────────────────
TRAILING_STOP_TRIGGER = 0.07   # +7% 이익부터 활성
TRAILING_STOP_DROP = 0.03      # 고점 대비 -3% 하락 시 청산
USE_PURE_ATR_TRAILING = False  # 실증: ATR 트레일링이 우리 데이터에서 더 나쁨
ATR_TRAIL_MULTIPLIER = 3.0     # 미사용 (USE_PURE_ATR_TRAILING=True 시만)
TRAIL_USE_CLOSE = False        # 미사용 (False 시 low 기반)

# ── 손절 (스윙: 더 넓게) ───────────────────────────────────────
STOP_LOSS = -0.05            # 기본 손절 -5%
ATR_STOP_MULTIPLIER = 3.0    # ATR × 3.0배
ATR_STOP_MIN = -0.04         # ATR 손절 최소 -4%
ATR_STOP_MAX = -0.08         # ATR 손절 최대 -8%

# 종목 교체 손절 기준
REPLACE_LOSS_THRESHOLD = -0.05

# ── 쿨다운 / 블랙리스트 ────────────────────────────────────────
SELL_COOLDOWN_MINUTES = 720  # 12시간 (스윙은 길게)
CONSECUTIVE_SL_LIMIT = 2
CONSECUTIVE_SL_BLACKLIST_HOURS = 48

# 글로벌 포트폴리오 손절
GLOBAL_STOP_LOSS = -0.10     # 전체 -10% 도달 시 강제 정리

# 최소 보유 시간 (스윙: 1H)
MIN_HOLD_MINUTES = 60

# 전략매도 활성화 (1D 추세 반전 / 모멘텀 소진 시)
STRATEGY_SELL_ENABLED = True

# 1D 추세 필터 활성화
DAILY_TREND_FILTER = True

# Level 2 호환용 (사용 안 함)
ADX_MIN_FOR_TREND_BUY = 0

# ── 동적 화이트/블랙리스트 ───────────────────────────────────────
SYMBOL_FILTER_ENABLED = True
FILTER_WHITELIST_MIN_PF = 2.0       # 1.5 → 2.0 강화
FILTER_WHITELIST_MIN_WIN_RATE = 50
FILTER_BLACKLIST_MAX_PF = 0.7
FILTER_BLACKLIST_MAX_WIN_RATE = 35
FILTER_MIN_TRADES = 10
FILTER_MIN_LOSSES = 2
FILTER_WHITELIST_SCORE_BONUS = 20

# ── 종목 선정 풀 (메이저 + 검증 알트) ───────────────────────────
MAJOR_TICKERS = [
    "BTC", "ETH", "XRP", "SOL", "DOGE", "ADA",
    "LINK", "AVAX", "DOT", "TRX", "BCH", "LTC",
    "ATOM", "NEAR", "ARB",
]
RESTRICT_TO_PROVEN_TICKERS = True
CANDIDATE_MIN_VOLUME = 5_000_000_000  # 50억원 (스윙은 유동성 더 중요)

# ── 캔들 설정 (1H 유지 - 데이터 12개월로는 6H 표본 부족) ──────
CANDLE_INTERVAL = "1h"         # 6h 실험은 데이터 부족으로 1h 유지
CANDLE_AGGREGATE_FROM_1H = 1
CANDLE_COUNT = 500

# 종목 재선정 주기
TICKER_REFRESH_CYCLES = 6     # 6사이클 = 6시간마다
REPLACE_HOLD_HOURS = 72       # 교체 대기 시 최대 보유 72시간

# 실행 주기 (스윙: 30분 - 보호 손절은 즉시, 신호는 1H 봉 close 시)
INTERVAL_SECONDS = 1800       # 30분 (3600에서 단축: 보호 손절 빠르게)

# 데이터/백테스트 설정
OHLCV_DATA_DIR = "data/ohlcv"
BACKTEST_RESULTS_DIR = "data/backtests"
DATA_FETCH_TOP_VOLUME_LIMIT = 30
FETCH_5M_COUNT = 500
FETCH_1H_COUNT = 2200          # 약 91일치 (스윙 백테스트용)
BACKTEST_MIN_5M_BARS = 120
BACKTEST_MIN_1H_BARS = 24 * 30  # 최소 30일치 1H 필요
BACKTEST_MIN_6H_BARS = 4 * 30   # 최소 30일치 6H = 120봉

# 파일 경로
LOG_PATH = "logs/bot.log"
PORTFOLIO_PATH = "data/portfolio.json"
COOLDOWN_PATH = "data/cooldowns.json"
