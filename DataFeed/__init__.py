"""
Chastiefol — Data Feed Package
Provides real-time and historical XAUUSD price data from multiple providers.
"""

from .data_feed import (
    DataFeedManager,
    DataFeedConfig,
    TwelveDataProvider,
    AlphaVantageProvider,
    GoldAPIProvider,
)

__all__ = [
    "DataFeedManager",
    "DataFeedConfig",
    "TwelveDataProvider",
    "AlphaVantageProvider",
    "GoldAPIProvider",
]
