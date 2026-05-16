"""
Chastiefol — Crypto Data Feed Manager
Orchestrates multiple crypto data providers with automatic failover.

Provider Priority:
1. CCXT/Binance (primary — real-time + full historical OHLCV)
2. Binance WebSocket (real-time streaming, parallel)
3. CoinGecko (secondary — free, no key required)
4. CoinMarketCap (tertiary — market metrics)
5. CoinStats (quaternary — supplementary data)

Data Sources:
- OHLCV: CCXT (api.binance.com/api/v3/klines) + historical data.binance.vision
- Real-time: Binance WebSocket (wss://stream.binance.com:9443)
- Market metrics: CoinGecko / CoinMarketCap / CoinStats
- Quotes: CCXT ticker API

Usage:
    config = CryptoDataFeedConfig(
        binance_api_key="...",
        binance_secret="...",
        coingecko_api_key="",  # optional
    )
    feed = CryptoDataFeedManager(config)
    await feed.initialize()

    # Get OHLCV data
    df = await feed.get_ohlcv("BTC/USDT", CryptoTimeframe.H1, bars=200)

    # Get real-time quote
    quote = await feed.get_quote("ETH/USDT")

    # Start WebSocket streaming
    await feed.start_streaming(["BTC/USDT", "ETH/USDT"], interval="1h")

    await feed.shutdown()
"""

import logging
import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Set
from datetime import datetime, timezone

import pandas as pd

from .providers import (
    CryptoDataProvider,
    CCXTProvider,
    CoinGeckoProvider,
    CoinMarketCapProvider,
    CoinStatsProvider,
    CryptoTimeframe,
    CryptoPriceQuote,
)
from .binance_ws import BinanceWebSocket, BinanceWSConfig, KlineUpdate, TickerUpdate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("CryptoDataFeed")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

@dataclass
class CryptoDataFeedConfig:
    """Configuration for crypto data feed."""
    # Binance / CCXT (Primary)
    binance_api_key: str = ""
    binance_secret: str = ""
    exchange_id: str = "binance"        # CCXT exchange ID
    sandbox_mode: bool = False          # Use Binance testnet

    # CoinGecko (Secondary — free)
    coingecko_api_key: str = ""         # Optional pro key

    # CoinMarketCap (Tertiary)
    coinmarketcap_api_key: str = ""

    # CoinStats (Quaternary)
    coinstats_api_key: str = ""

    # WebSocket
    enable_websocket: bool = True
    ws_base_url: str = "wss://stream.binance.com:9443"

    # Cache
    cache_ttl_seconds: int = 10         # Crypto moves fast — short cache
    ohlcv_cache_ttl: int = 30           # OHLCV cache slightly longer

    # Default settings
    default_timeframe: CryptoTimeframe = CryptoTimeframe.H1
    default_bars: int = 200

    # Rate limiting
    max_concurrent_requests: int = 5


# ──────────────────────────────────────────────
# Crypto Data Feed Manager
# ──────────────────────────────────────────────

class CryptoDataFeedManager:
    """
    Manages multiple crypto data providers with automatic failover.

    Architecture:
    ┌─────────────────────────────────────────────────────┐
    │           CryptoDataFeedManager                      │
    ├─────────────────────────────────────────────────────┤
    │  CCXTProvider (Binance) ─── Primary OHLCV + Quotes  │
    │  BinanceWebSocket ───────── Real-time Streaming     │
    │  CoinGeckoProvider ───────── Fallback + Market Cap  │
    │  CoinMarketCapProvider ──── Global Metrics          │
    │  CoinStatsProvider ──────── Supplementary           │
    ├─────────────────────────────────────────────────────┤
    │  Cache Layer ──── Reduces API calls                 │
    │  Failover Logic ─ Auto-switch on provider failure   │
    └─────────────────────────────────────────────────────┘

    All providers use the same symbol format: "BTC/USDT", "ETH/USDT", etc.
    """

    def __init__(self, config: CryptoDataFeedConfig):
        self.config = config
        self._initialized = False

        # Providers (ordered by priority)
        self.ccxt_provider: Optional[CCXTProvider] = None
        self.coingecko_provider: Optional[CoinGeckoProvider] = None
        self.coinmarketcap_provider: Optional[CoinMarketCapProvider] = None
        self.coinstats_provider: Optional[CoinStatsProvider] = None

        # WebSocket
        self.websocket: Optional[BinanceWebSocket] = None
        self._ws_task: Optional[asyncio.Task] = None

        # Cache
        self._quote_cache: Dict[str, Dict] = {}  # symbol → {quote, timestamp}
        self._ohlcv_cache: Dict[str, Dict] = {}  # cache_key → {df, timestamp}

        # Callbacks for WebSocket data
        self._on_kline_callbacks: List[Callable] = []
        self._on_ticker_callbacks: List[Callable] = []

        # Available symbols (populated after initialization)
        self._available_symbols: List[str] = []
        self._usdt_pairs: List[str] = []

        log.info(f"CryptoDataFeedManager created | "
                 f"Exchange: {config.exchange_id} | "
                 f"WebSocket: {config.enable_websocket}")

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    async def initialize(self):
        """Initialize all configured providers."""
        log.info("Initializing crypto data feed providers...")

        # 1. CCXT Provider (always initialized — core provider)
        self.ccxt_provider = CCXTProvider(
            exchange_id=self.config.exchange_id,
            api_key=self.config.binance_api_key,
            secret=self.config.binance_secret,
            sandbox=self.config.sandbox_mode,
        )
        await self.ccxt_provider.initialize()

        if self.ccxt_provider.is_available:
            # Load available symbols
            self._available_symbols = await self.ccxt_provider.get_all_symbols()
            self._usdt_pairs = await self.ccxt_provider.get_usdt_pairs()
            log.info(f"  ✓ CCXT ({self.config.exchange_id}) — "
                     f"{len(self._available_symbols)} symbols, "
                     f"{len(self._usdt_pairs)} USDT pairs")
        else:
            log.warning("  ✗ CCXT provider unavailable")

        # 2. CoinGecko (always available — free)
        self.coingecko_provider = CoinGeckoProvider(
            api_key=self.config.coingecko_api_key
        )
        await self.coingecko_provider.init_session()
        log.info("  ✓ CoinGecko (free tier)")

        # 3. CoinMarketCap (if key provided)
        if self.config.coinmarketcap_api_key:
            self.coinmarketcap_provider = CoinMarketCapProvider(
                api_key=self.config.coinmarketcap_api_key
            )
            await self.coinmarketcap_provider.init_session()
            log.info("  ✓ CoinMarketCap")

        # 4. CoinStats (if key provided)
        if self.config.coinstats_api_key:
            self.coinstats_provider = CoinStatsProvider(
                api_key=self.config.coinstats_api_key
            )
            await self.coinstats_provider.init_session()
            log.info("  ✓ CoinStats")

        # 5. WebSocket
        if self.config.enable_websocket:
            ws_config = BinanceWSConfig(base_url=self.config.ws_base_url)
            self.websocket = BinanceWebSocket(config=ws_config)
            self.websocket.on_kline = self._on_ws_kline
            self.websocket.on_ticker = self._on_ws_ticker
            log.info("  ✓ Binance WebSocket ready")

        self._initialized = True
        log.info(f"Crypto data feed initialized | "
                 f"USDT pairs available: {len(self._usdt_pairs)}")

    async def shutdown(self):
        """Gracefully shut down all providers."""
        log.info("Shutting down crypto data feed...")

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            await self.websocket.stop()

        if self.ccxt_provider:
            await self.ccxt_provider.close()

        for provider in [self.coingecko_provider,
                         self.coinmarketcap_provider,
                         self.coinstats_provider]:
            if provider:
                await provider.close_session()

        self._initialized = False
        log.info("Crypto data feed shut down.")

    # ──────────────────────────────────────────
    # Public API — Quotes
    # ──────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[CryptoPriceQuote]:
        """
        Get real-time price quote with automatic failover.
        
        Args:
            symbol: Trading pair in CCXT format (e.g. "BTC/USDT")
            
        Returns:
            CryptoPriceQuote or None if all providers fail
        """
        # Check cache first
        cached = self._get_quote_cache(symbol)
        if cached:
            return cached

        # Try providers in priority order
        providers = self._get_quote_providers()
        for provider in providers:
            if not provider.is_available:
                continue
            quote = await provider.get_quote(symbol)
            if quote and quote.price > 0:
                self._set_quote_cache(symbol, quote)
                return quote

        log.error(f"All providers failed for quote: {symbol}")
        return None

    async def get_multiple_quotes(self, symbols: List[str]) -> Dict[str, CryptoPriceQuote]:
        """Get quotes for multiple symbols efficiently."""
        if self.ccxt_provider and self.ccxt_provider.is_available:
            return await self.ccxt_provider.get_multiple_tickers(symbols)
        
        # Fallback: fetch one by one
        result = {}
        for symbol in symbols:
            quote = await self.get_quote(symbol)
            if quote:
                result[symbol] = quote
        return result

    async def get_latest_price(self, symbol: str) -> float:
        """Convenience: get just the price as float."""
        quote = await self.get_quote(symbol)
        return quote.price if quote else 0.0

    # ──────────────────────────────────────────
    # Public API — Historical OHLCV
    # ──────────────────────────────────────────

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: CryptoTimeframe = None,
        bars: int = None,
    ) -> Optional[pd.DataFrame]:
        """
        Get historical OHLCV candlestick data with failover.
        Primary source: Binance via CCXT (api.binance.com/api/v3/klines)
        Fallback: CoinGecko OHLC

        Args:
            symbol: Trading pair (e.g. "BTC/USDT")
            timeframe: Candle interval (default: H1)
            bars: Number of candles (default: 200)

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        timeframe = timeframe or self.config.default_timeframe
        bars = bars or self.config.default_bars

        # Check cache
        cache_key = f"{symbol}_{timeframe.value}_{bars}"
        cached = self._get_ohlcv_cache(cache_key)
        if cached is not None:
            return cached

        # Try CCXT first (Binance klines API)
        if self.ccxt_provider and self.ccxt_provider.is_available:
            df = await self.ccxt_provider.get_historical(symbol, timeframe, bars)
            if df is not None and not df.empty:
                self._set_ohlcv_cache(cache_key, df)
                return df

        # Fallback to CoinGecko
        if self.coingecko_provider and self.coingecko_provider.is_available:
            df = await self.coingecko_provider.get_historical(symbol, timeframe, bars)
            if df is not None and not df.empty:
                self._set_ohlcv_cache(cache_key, df)
                return df

        log.error(f"All providers failed for OHLCV: {symbol} ({timeframe.value})")
        return None

    async def get_multi_timeframe_ohlcv(
        self, symbol: str,
        timeframes: List[CryptoTimeframe] = None,
        bars: int = 200,
    ) -> Dict[str, pd.DataFrame]:
        """
        Get OHLCV data across multiple timeframes for MTF analysis.

        Args:
            symbol: Trading pair
            timeframes: List of timeframes to fetch
            bars: Bars per timeframe

        Returns:
            Dict mapping timeframe string → DataFrame
        """
        if timeframes is None:
            timeframes = [
                CryptoTimeframe.M15,
                CryptoTimeframe.H1,
                CryptoTimeframe.H4,
                CryptoTimeframe.D1,
            ]

        result = {}
        tasks = []
        for tf in timeframes:
            tasks.append(self.get_ohlcv(symbol, tf, bars))

        dfs = await asyncio.gather(*tasks, return_exceptions=True)

        for tf, df in zip(timeframes, dfs):
            if isinstance(df, pd.DataFrame) and not df.empty:
                result[tf.value] = df
            elif isinstance(df, Exception):
                log.warning(f"Failed to get {tf.value} for {symbol}: {df}")

        return result

    # ──────────────────────────────────────────
    # Public API — WebSocket Streaming
    # ──────────────────────────────────────────

    async def start_streaming(
        self,
        symbols: List[str],
        interval: str = "1h",
        on_kline: Optional[Callable[[KlineUpdate], None]] = None,
        on_ticker: Optional[Callable[[TickerUpdate], None]] = None,
    ):
        """
        Start real-time WebSocket streaming for specified symbols.

        Args:
            symbols: List of pairs to stream (e.g. ["BTC/USDT", "ETH/USDT"])
            interval: Kline interval (1m, 5m, 15m, 1h, 4h, 1d)
            on_kline: Callback for kline updates
            on_ticker: Callback for ticker updates
        """
        if not self.websocket:
            log.warning("WebSocket not configured — streaming unavailable")
            return

        # Register callbacks
        if on_kline:
            self._on_kline_callbacks.append(on_kline)
        if on_ticker:
            self._on_ticker_callbacks.append(on_ticker)

        # Subscribe to streams
        for symbol in symbols:
            self.websocket.subscribe_kline(symbol, interval)
            self.websocket.subscribe_ticker(symbol)

        # Start WebSocket in background
        self._ws_task = asyncio.create_task(self.websocket.start())
        log.info(f"WebSocket streaming started for {len(symbols)} symbols ({interval})")

    async def stop_streaming(self):
        """Stop WebSocket streaming."""
        if self.websocket:
            await self.websocket.stop()
        if self._ws_task:
            self._ws_task.cancel()

    # ──────────────────────────────────────────
    # Public API — Symbol Discovery
    # ──────────────────────────────────────────

    def get_available_symbols(self) -> List[str]:
        """Get all available trading symbols."""
        return self._available_symbols

    def get_usdt_pairs(self) -> List[str]:
        """Get all USDT trading pairs (most liquid)."""
        return self._usdt_pairs

    def get_top_volume_pairs(self, limit: int = 50) -> List[str]:
        """
        Get top pairs by 24h volume.
        Note: Requires a recent fetch_tickers call.
        """
        # This is populated after calling get_multiple_quotes
        return self._usdt_pairs[:limit]

    # ──────────────────────────────────────────
    # Public API — Market Metrics
    # ──────────────────────────────────────────

    async def get_market_overview(self) -> Optional[Dict]:
        """Get global crypto market overview from CoinGecko/CMC."""
        if self.coinmarketcap_provider and self.coinmarketcap_provider.is_available:
            metrics = await self.coinmarketcap_provider.get_global_metrics()
            if metrics:
                return metrics

        if self.coingecko_provider and self.coingecko_provider.is_available:
            market_data = await self.coingecko_provider.get_market_data(limit=20)
            if market_data:
                return {"top_coins": market_data}

        return None

    # ──────────────────────────────────────────
    # WebSocket Callbacks (Internal)
    # ──────────────────────────────────────────

    async def _on_ws_kline(self, update: KlineUpdate):
        """Internal handler for WebSocket kline updates."""
        # Update quote cache with latest price
        if update.close > 0:
            self._quote_cache[update.symbol] = {
                "quote": CryptoPriceQuote(
                    symbol=update.symbol,
                    last=update.close,
                    mid=update.close,
                    bid=update.close * 0.9999,
                    ask=update.close * 1.0001,
                    spread=update.close * 0.0002,
                    volume_24h=update.quote_volume,
                    timestamp=update.timestamp,
                    provider="Binance:WS",
                ),
                "timestamp": time.time(),
            }

        # Fire registered callbacks
        for callback in self._on_kline_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(update)
                else:
                    callback(update)
            except Exception as e:
                log.error(f"Kline callback error: {e}")

    async def _on_ws_ticker(self, update: TickerUpdate):
        """Internal handler for WebSocket ticker updates."""
        for callback in self._on_ticker_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(update)
                else:
                    callback(update)
            except Exception as e:
                log.error(f"Ticker callback error: {e}")

    # ──────────────────────────────────────────
    # Cache Layer
    # ──────────────────────────────────────────

    def _get_quote_cache(self, symbol: str) -> Optional[CryptoPriceQuote]:
        """Get cached quote if still valid."""
        entry = self._quote_cache.get(symbol)
        if entry and (time.time() - entry["timestamp"]) < self.config.cache_ttl_seconds:
            return entry["quote"]
        return None

    def _set_quote_cache(self, symbol: str, quote: CryptoPriceQuote):
        """Cache a quote."""
        self._quote_cache[symbol] = {
            "quote": quote,
            "timestamp": time.time(),
        }

    def _get_ohlcv_cache(self, key: str) -> Optional[pd.DataFrame]:
        """Get cached OHLCV data if still valid."""
        entry = self._ohlcv_cache.get(key)
        if entry and (time.time() - entry["timestamp"]) < self.config.ohlcv_cache_ttl:
            return entry["df"]
        return None

    def _set_ohlcv_cache(self, key: str, df: pd.DataFrame):
        """Cache OHLCV data."""
        self._ohlcv_cache[key] = {
            "df": df,
            "timestamp": time.time(),
        }

    def clear_cache(self):
        """Clear all cached data."""
        self._quote_cache.clear()
        self._ohlcv_cache.clear()

    # ──────────────────────────────────────────
    # Provider Management
    # ──────────────────────────────────────────

    def _get_quote_providers(self) -> List[CryptoDataProvider]:
        """Get ordered list of quote providers."""
        providers = []
        if self.ccxt_provider:
            providers.append(self.ccxt_provider)
        if self.coingecko_provider:
            providers.append(self.coingecko_provider)
        if self.coinmarketcap_provider:
            providers.append(self.coinmarketcap_provider)
        if self.coinstats_provider:
            providers.append(self.coinstats_provider)
        return providers

    def get_status(self) -> Dict:
        """Get status of all providers and connections."""
        status = {
            "initialized": self._initialized,
            "available_symbols": len(self._available_symbols),
            "usdt_pairs": len(self._usdt_pairs),
            "cache_quotes": len(self._quote_cache),
            "cache_ohlcv": len(self._ohlcv_cache),
            "providers": {},
        }

        if self.ccxt_provider:
            status["providers"]["ccxt"] = {
                "available": self.ccxt_provider.is_available,
                "exchange": self.config.exchange_id,
            }
        if self.coingecko_provider:
            status["providers"]["coingecko"] = {
                "available": self.coingecko_provider.is_available,
            }
        if self.coinmarketcap_provider:
            status["providers"]["coinmarketcap"] = {
                "available": self.coinmarketcap_provider.is_available,
            }
        if self.coinstats_provider:
            status["providers"]["coinstats"] = {
                "available": self.coinstats_provider.is_available,
            }
        if self.websocket:
            status["websocket"] = self.websocket.get_stats()

        return status


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

async def main():
    """Test crypto data feed standalone."""
    import os

    config = CryptoDataFeedConfig(
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_secret=os.getenv("BINANCE_SECRET", ""),
        coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
        coinmarketcap_api_key=os.getenv("COINMARKETCAP_API_KEY", ""),
        coinstats_api_key=os.getenv("COINSTATS_API_KEY", ""),
        enable_websocket=False,  # Disable for test
    )

    feed = CryptoDataFeedManager(config)
    await feed.initialize()

    # Get available pairs
    pairs = feed.get_usdt_pairs()
    log.info(f"Available USDT pairs: {len(pairs)}")
    if pairs:
        log.info(f"Sample: {pairs[:10]}")

    # Get quote
    quote = await feed.get_quote("BTC/USDT")
    if quote:
        log.info(f"BTC/USDT price: ${quote.price:,.2f} (via {quote.provider})")

    # Get OHLCV
    df = await feed.get_ohlcv("BTC/USDT", CryptoTimeframe.H1, bars=100)
    if df is not None:
        log.info(f"BTC/USDT H1: {len(df)} bars | "
                 f"Latest close: ${df['close'].iloc[-1]:,.2f}")

    # Status
    log.info(f"Status: {feed.get_status()}")

    await feed.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
