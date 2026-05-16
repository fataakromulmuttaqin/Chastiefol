"""
Chastiefol — Analysis Package
Technical analysis, market structure, and multi-timeframe confluence.
"""

from .xauusd_engine import (
    ChastiefollAgent,
    TechnicalIndicators,
    MarketStructureAnalyzer,
    Signal,
    Bias,
    TradeSetup,
    MarketStructure,
)
from .multi_timeframe import (
    MultiTimeframeAnalyzer,
    MTFConfig,
    MTFResult,
    TimeframeData,
)
from .pair_config import (
    PairConfig,
    XAUUSD_CONFIG,
    BTCUSD_CONFIG,
    PAIR_CONFIGS,
    get_pair_config,
)

__all__ = [
    "ChastiefollAgent",
    "TechnicalIndicators",
    "MarketStructureAnalyzer",
    "Signal",
    "Bias",
    "TradeSetup",
    "MarketStructure",
    "MultiTimeframeAnalyzer",
    "MTFConfig",
    "MTFResult",
    "TimeframeData",
    "PairConfig",
    "XAUUSD_CONFIG",
    "BTCUSD_CONFIG",
    "PAIR_CONFIGS",
    "get_pair_config",
]
