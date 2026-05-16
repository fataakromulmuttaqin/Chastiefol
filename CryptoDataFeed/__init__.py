"""
Chastiefol — Crypto Data Feed Module
Multi-provider crypto market data with failover support.

Providers:
1. CCXT (Primary) — Binance + multi-exchange OHLCV/ticker via unified API
2. Binance WebSocket (Real-time) — Live kline streaming
3. CoinGecko (Secondary) — Free market data API
4. CoinMarketCap (Tertiary) — Market cap & pricing
5. CoinStats (Quaternary) — Portfolio & market data
"""

from .crypto_data_feed import (
    CryptoDataFeedManager,
    CryptoDataFeedConfig,
    CryptoTimeframe,
    CryptoPriceQuote,
)
from .binance_ws import BinanceWebSocket
from .providers import (
    CCXTProvider,
    CoinGeckoProvider,
    CoinMarketCapProvider,
    CoinStatsProvider,
)

__all__ = [
    "CryptoDataFeedManager",
    "CryptoDataFeedConfig",
    "CryptoTimeframe",
    "CryptoPriceQuote",
    "BinanceWebSocket",
    "CCXTProvider",
    "CoinGeckoProvider",
    "CoinMarketCapProvider",
    "CoinStatsProvider",
]
