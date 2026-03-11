# config.py

# 초기 자금 (전체 통합 자금)
INITIAL_BALANCE = 1_000_000

# 종목 설정
TOP_TICKER_LIMIT = 3          # 최대 종목 수 3개
ALLOC_PER_TICKER = 1 / 3      # 종목당 자금 배분 (1/3씩)

# 이동평균
SHORT_MA = 5
LONG_MA = 20

# RSI 기준값
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 60
RSI_EXTREME_LOW = 30
RSI_EXTREME_HIGH = 70

# 볼린저밴드
BB_LOWER_THRESHOLD = 0.3
BB_UPPER_THRESHOLD = 0.7

# 매매 설정
TRADE_RATIO = 0.3             # 배분금액의 30%씩 분할매수
MAX_BUY_COUNT = 3             # 최대 분할매수 횟수
SELL_RATIO = 0.5              # 분할매도 50%
STOP_LOSS = -0.03             # 손절 -3%
FEE_RATE = 0.0025             # 수수료 0.25%

# 익절 기준 (보유 시간별 동적 조정)
TAKE_PROFIT_BASE = 0.06       # 기본 익절 +6% (6시간 이내)
TAKE_PROFIT_6H = 0.04         # 6시간 이상 보유 시 +4%
TAKE_PROFIT_12H = 0.02        # 12시간 이상 보유 시 +2%
TAKE_PROFIT_24H = 0.005       # 24시간 이상 보유 시 +0.5%
FORCE_SELL_HOURS = 48         # 48시간 강제 청산

# 트레일링 스탑
TRAILING_STOP_TRIGGER = 0.04  # +4% 수익부터 추적
TRAILING_STOP_DROP = 0.015    # 고점 대비 -1.5% 하락 시 매도

# 캔들 설정
CANDLE_INTERVAL = "5m"
CANDLE_COUNT = 200

# 실행 주기
INTERVAL_SECONDS = 60

# 파일 경로
LOG_PATH = "logs/bot.log"
PORTFOLIO_PATH = "data/portfolio.json"  # 통합 포트폴리오