"""
Strategies — imports all strategy modules to populate StrategyRegistry.

Import `StrategyRegistry` from here (or from `strategies.registry` directly)
after this package is imported to get a fully-populated registry.
"""

from strategies.registry import StrategyRegistry  # noqa: F401

# Import order matches run_engine_cmd — triggers @StrategyRegistry.register decorators
from strategies.ema_crossover import EMACrossover  # noqa: F401
from strategies.macd import MACDStrategy  # noqa: F401
from strategies.supertrend import SupertrendStrategy  # noqa: F401
from strategies.donchian_breakout import DonchianBreakout  # noqa: F401
from strategies.bollinger_reversion import BollingerReversion  # noqa: F401
from strategies.rsi_extremes import RSIExtremes  # noqa: F401
from strategies.atr_volatility_breakout import ATRVolatilityBreakout  # noqa: F401
from strategies.keltner_squeeze import KeltnerSqueeze  # noqa: F401
from strategies.dca import DCAStrategy  # noqa: F401
from strategies.hodl_rebalance import HODLRebalance  # noqa: F401
from strategies.stat_arb import StatArbPairsStrategy  # noqa: F401
from strategies.funding_rate_arb import FundingRateArb  # noqa: F401
from strategies.smc_breakout import SMCBreakout  # noqa: F401
