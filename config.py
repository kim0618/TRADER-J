# config.py

# 초기 자금 (전체 통합 자금)
INITIAL_BALANCE = 1_000_000

# 종목 설정
TOP_TICKER_LIMIT = 3          # 최대 종목 수 3개
ALLOC_PER_TICKER = 1 / 3      # 종목당 자금 배분 (1/3씩)

# 블랙리스트 (스테이블코인, 래핑 토큰 등 트레이딩 부적합 종목)
BLACKLIST = ["USDT", "USDC", "DAI", "TUSD", "BUSD", "WBTC", "WETH"]

# 최소 가격 필터 (극소형 저가 코인 제외)
MIN_PRICE_FILTER = 100

# 이동평균
SHORT_MA = 5
LONG_MA = 20

# RSI 기준값 (엄격 진입: 확실한 저점에서만)
RSI_PERIOD = 9                # RSI 계산 기간 (크립토 단기매매용)
RSI_OVERSOLD = 30             # 매수 과매도 기준 (확실한 과매도에서만 진입)
RSI_OVERBOUGHT = 68           # 매도 과매수 기준
RSI_EXTREME_LOW = 22          # 극단 과매도 즉시 매수
RSI_EXTREME_HIGH = 73         # 극단 과매수 즉시 매도

# 볼린저밴드 (엄격 진입)
BB_PERIOD = 20                # BB 기간
BB_STD = 2.0                  # 표준편차 배수 (표준값)
BB_LOWER_THRESHOLD = 0.15     # 매수 BB% 기준 (확실한 하단에서만)
BB_UPPER_THRESHOLD = 0.8      # 매도 BB% 기준

# 매매 설정
TRADE_RATIO = 0.7             # 종목당 1회 매수 비율 70%
MAX_BUY_COUNT = 1             # DCA 비활성화: 2차 매수 78.3% 손실 → 단일 진입으로 변경
SELL_RATIO = 1.0              # 전량 매도
STOP_LOSS = -0.025            # 손절 기본값 -2.5% (기존 -3.5% → 손실 한도 축소)
FEE_RATE = 0.0025             # 수수료 0.25%
SLIPPAGE_RATE = 0.002         # 슬리피지 0.2%

# 거래량 필터 (엄격: 거래 빈도 줄이기)
VOLUME_RATIO_MIN = 0.5        # 평균 50% 이상에서만 진입

# 분할매수 하락 조건 (MAX_BUY_COUNT=1이므로 실제 미사용)
DCA_DROP_1 = -0.05            # 2차 매수 조건 강화 (-1% → -5%, 사실상 비활성)

# 진입 조건 강화 (추세별 매수 필요 조건 수)
BUY_SIGNAL_REQUIRED_UP = 2        # 상승장: 3개 중 2개 충족 (기존 유지)
BUY_SIGNAL_REQUIRED_SIDEWAYS = 3  # 횡보장: 3개 모두 충족 (기존 2개 → 강화)
# 조건: RSI<=30, BB%<=0.15, MACD hist>0

# 익절 기준 (트레일링스탑과 분리: TP는 상한선, 트레일링이 핵심 출구)
TAKE_PROFIT_BASE = 0.035      # 기본 익절 +3.5% (트레일링 트리거 +2.5%와 여유 확보)
TAKE_PROFIT_6H = 0.025        # 6h 이상 보유 시 +2.5%
TAKE_PROFIT_12H = 0.020       # 12h 이상 보유 시 +2.0%
TAKE_PROFIT_24H = 0.010       # 24h 이상 보유 시 +1.0%
FORCE_SELL_HOURS = 48         # 48시간 강제 청산

# 시간 기반 횡보 정리
TIME_STOP_HOURS = 24          # 24시간 이상 보유
TIME_STOP_THRESHOLD = 0.015   # 수익률 ±1.5% 이내면 정리

# 트레일링 스탑 (핵심 출구: 수익 확보)
TRAILING_STOP_TRIGGER = 0.025 # +2.5% 수익부터 추적 시작
TRAILING_STOP_DROP = 0.008    # 고점 대비 -0.8% 하락 시 매도 (기존 -1.2% → 수익 더 보존)

# ATR 기반 동적 손절
ATR_STOP_MULTIPLIER = 2.5     # ATR × 2.5배
ATR_STOP_MIN = -0.020         # ATR 손절 최소 -2.0% (기존 -2.5% → 축소)
ATR_STOP_MAX = -0.050         # ATR 손절 최대 -5.0% (기존 -8.0% → 축소)

# 종목 교체 손절 기준
REPLACE_LOSS_THRESHOLD = -0.03

# 손절/매도 후 동일 종목 재진입 금지 시간
SELL_COOLDOWN_MINUTES = 120   # 2시간

# 연속 손절 블랙리스트
CONSECUTIVE_SL_LIMIT = 2
CONSECUTIVE_SL_BLACKLIST_HOURS = 4

# 글로벌 포트폴리오 손절
GLOBAL_STOP_LOSS = -0.05

# 최소 보유 시간
MIN_HOLD_MINUTES = 30

# 전략매도 비활성화 (기계적 출구만: 트레일링스탑 + 동적익절이 핵심)
STRATEGY_SELL_ENABLED = False

# BB 최소 변동성
BB_WIDTH_MIN = 0.003

# MACD 설정 (크립토 단기매매 최적화)
MACD_FAST = 8
MACD_SLOW = 21
MACD_SIGNAL = 5

# ADX 레짐 감지
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25
ADX_RANGE_THRESHOLD = 20

# 캔들 설정
CANDLE_INTERVAL = "5m"
CANDLE_COUNT = 200

# 종목 재선정 주기 (사이클 단위)
TICKER_REFRESH_CYCLES = 60    # 60사이클 = 5시간마다 종목 재선정
REPLACE_HOLD_HOURS = 12       # 교체 대기 시 최대 보유 시간

# 실행 주기
INTERVAL_SECONDS = 300        # 5분 주기

# 데이터/백테스트 설정
OHLCV_DATA_DIR = "data/ohlcv"
BACKTEST_RESULTS_DIR = "data/backtests"
DATA_FETCH_TOP_VOLUME_LIMIT = 30
FETCH_5M_COUNT = 500
FETCH_1H_COUNT = 500
BACKTEST_MIN_5M_BARS = 120
BACKTEST_MIN_1H_BARS = 50

# 파일 경로
LOG_PATH = "logs/bot.log"
PORTFOLIO_PATH = "data/portfolio.json"
COOLDOWN_PATH = "data/cooldowns.json"
