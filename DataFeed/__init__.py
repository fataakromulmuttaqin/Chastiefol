"""
Chastiefol — Data Feed Package
Provides real-time and historical XAUUSD price data.

Primary: TradingView WebSocket (FREE, no API key required)
Fallback: CCXT/Binance (PAXG/USDT gold proxy)

REMOVED: TwelveData, AlphaVantage, GoldAPI (rate limited, no historical, requires paid keys)
"""

from .data_feed import (
    DataFeedManager,
    DataFeedConfig,
    PriceQuote,
    Timeframe,
)
from .tradingview_ws import (
    TradingViewWSProvider,
    TVWebSocketConfig,
    TVTick,
    TVBar,
)

__all__ = [
    "DataFeedManager",
    "DataFeedConfig",
    "PriceQuote",
    "Timeframe",
    "TradingViewWSProvider",
    "TVWebSocketConfig",
    "TVTick",
    "TVBar",
]
