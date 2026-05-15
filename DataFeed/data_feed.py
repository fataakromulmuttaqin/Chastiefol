"""
Chastiefol — Real-Time Data Feed
Fetches live & historical XAUUSD prices from multiple providers with failover.

Supported Providers:
1. TwelveData (primary) — real-time + historical OHLCV
2. Alpha Vantage (secondary) — historical + intraday
3. GoldAPI.io (tertiary) — spot price only

Features:
- Automatic failover between providers
- Rate limit management
- Caching layer to reduce API calls
- WebSocket support for TwelveData real-time streaming
"""

import logging
import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable
from enum import Enum

import aiohttp
import pandas as pd
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("DataFeed")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

class Timeframe(str, Enum):
    M1 = "1min"
    M5 = "5min"
    M15 = "15min"
    M30 = "30min"
    H1 = "1h"
    H4 = "4h"
    D1 = "1day"
    W1 = "1week"


@dataclass
class DataFeedConfig:
    """Configuration for data feed providers."""
    # TwelveData
    twelvedata_api_key: str = ""
    twelvedata_base_url: str = "https://api.twelvedata.com"
    twelvedata_ws_url: str = "wss://ws.twelvedata.com/v1"

    # Alpha Vantage
    alphavantage_api_key: str = ""
    alphavantage_base_url: str = "https://www.alphavantage.co/query"

    # GoldAPI
    goldapi_api_key: str = ""
    goldapi_base_url: str = "https://www.goldapi.io/api"

    # General
    symbol: str = "XAU/USD"
    default_timeframe: Timeframe = Timeframe.H1
    cache_ttl_seconds: int = 30
    max_retries: int = 3
    request_timeout: int = 15
    rate_limit_delay: float = 1.0


@dataclass
class PriceQuote:
    """Real-time price quote."""
    symbol: str = "XAUUSD"
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    spread: float = 0.0
    timestamp: str = ""
    provider: str = ""

    @property
    def price(self) -> float:
        return self.mid if self.mid > 0 else (self.bid + self.ask) / 2


@dataclass
class OHLCVBar:
    """Single OHLCV candle."""
    timestamp: str = ""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0


# ──────────────────────────────────────────────
# Abstract Provider
# ──────────────────────────────────────────────

class DataProvider(ABC):
    """Abstract base class for data providers."""

    def __init__(self, name: str):
        self.name = name
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0.0
        self._is_available = True

    async def init_session(self):
        if not self._session:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def close_session(self):
        if self._session:
            await self._session.close()
            self._session = None

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[PriceQuote]:
        """Get real-time price quote."""
        pass

    @abstractmethod
    async def get_historical(
        self, symbol: str, timeframe: Timeframe, bars: int
    ) -> Optional[pd.DataFrame]:
        """Get historical OHLCV data."""
        pass

    @property
    def is_available(self) -> bool:
        return self._is_available


# ──────────────────────────────────────────────
# TwelveData Provider
# ──────────────────────────────────────────────

class TwelveDataProvider(DataProvider):
    """
    TwelveData API provider.
    Docs: https://twelvedata.com/docs
    Free tier: 8 requests/min, 800/day
    """

    def __init__(self, config: DataFeedConfig):
        super().__init__("TwelveData")
        self.config = config
        self._api_key = config.twelvedata_api_key
        self._base_url = config.twelvedata_base_url
        self._ws_connection = None
        self._on_tick: Optional[Callable] = None

    async def get_quote(self, symbol: str) -> Optional[PriceQuote]:
        """Get real-time price from TwelveData."""
        await self.init_session()
        url = f"{self._base_url}/price"
        params = {
            "symbol": symbol,
            "apikey": self._api_key,
        }

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "price" in data:
                        price = float(data["price"])
                        return PriceQuote(
                            symbol=symbol.replace("/", ""),
                            bid=price - 0.15,  # Approximate spread
                            ask=price + 0.15,
                            mid=price,
                            spread=0.30,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            provider=self.name,
                        )
                    elif "message" in data:
                        log.warning(f"[TwelveData] {data['message']}")
                elif resp.status == 429:
                    log.warning("[TwelveData] Rate limit hit.")
                    self._is_available = False
                    await asyncio.sleep(60)
                    self._is_available = True
        except Exception as e:
            log.error(f"[TwelveData] Quote error: {e}")
        return None

    async def get_historical(
        self, symbol: str, timeframe: Timeframe, bars: int = 100
    ) -> Optional[pd.DataFrame]:
        """Get historical OHLCV data from TwelveData."""
        await self.init_session()
        url = f"{self._base_url}/time_series"
        params = {
            "symbol": symbol,
            "interval": timeframe.value,
            "outputsize": bars,
            "apikey": self._api_key,
            "format": "JSON",
        }

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "values" in data:
                        return self._parse_ohlcv(data["values"])
                    elif "message" in data:
                        log.warning(f"[TwelveData] {data['message']}")
                elif resp.status == 429:
                    log.warning("[TwelveData] Rate limit hit on historical.")
                    self._is_available = False
        except Exception as e:
            log.error(f"[TwelveData] Historical error: {e}")
        return None

    async def start_websocket(self, symbol: str, on_tick: Callable):
        """Start WebSocket streaming for real-time ticks."""
        self._on_tick = on_tick
        ws_url = self.config.twelvedata_ws_url

        try:
            await self.init_session()
            async with self._session.ws_connect(ws_url) as ws:
                self._ws_connection = ws
                # Subscribe
                subscribe_msg = {
                    "action": "subscribe",
                    "params": {
                        "symbols": symbol,
                    },
                }
                await ws.send_json(subscribe_msg)
                log.info(f"[TwelveData] WebSocket subscribed to {symbol}")

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = msg.json()
                        if "price" in data:
                            quote = PriceQuote(
                                symbol=data.get("symbol", symbol).replace("/", ""),
                                mid=float(data["price"]),
                                bid=float(data["price"]) - 0.15,
                                ask=float(data["price"]) + 0.15,
                                spread=0.30,
                                timestamp=data.get("timestamp", ""),
                                provider=self.name,
                            )
                            if self._on_tick:
                                await self._on_tick(quote)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        log.warning("[TwelveData] WebSocket closed.")
                        break
        except Exception as e:
            log.error(f"[TwelveData] WebSocket error: {e}")

    async def stop_websocket(self):
        """Stop WebSocket connection."""
        if self._ws_connection:
            await self._ws_connection.close()
            self._ws_connection = None

    def _parse_ohlcv(self, values: List[Dict]) -> pd.DataFrame:
        """Parse TwelveData time series response to DataFrame."""
        rows = []
        for v in reversed(values):  # TwelveData returns newest first
            rows.append({
                "timestamp": v.get("datetime", ""),
                "open": float(v.get("open", 0)),
                "high": float(v.get("high", 0)),
                "low": float(v.get("low", 0)),
                "close": float(v.get("close", 0)),
                "volume": float(v.get("volume", 0)),
            })
        df = pd.DataFrame(rows)
        return df


# ──────────────────────────────────────────────
# Alpha Vantage Provider
# ──────────────────────────────────────────────

class AlphaVantageProvider(DataProvider):
    """
    Alpha Vantage API provider.
    Docs: https://www.alphavantage.co/documentation/
    Free tier: 25 requests/day
    """

    TIMEFRAME_MAP = {
        Timeframe.M1: ("TIME_SERIES_INTRADAY", "1min"),
        Timeframe.M5: ("TIME_SERIES_INTRADAY", "5min"),
        Timeframe.M15: ("TIME_SERIES_INTRADAY", "15min"),
        Timeframe.M30: ("TIME_SERIES_INTRADAY", "30min"),
        Timeframe.H1: ("TIME_SERIES_INTRADAY", "60min"),
        Timeframe.D1: ("TIME_SERIES_DAILY", None),
        Timeframe.W1: ("TIME_SERIES_WEEKLY", None),
    }

    def __init__(self, config: DataFeedConfig):
        super().__init__("AlphaVantage")
        self.config = config
        self._api_key = config.alphavantage_api_key
        self._base_url = config.alphavantage_base_url

    async def get_quote(self, symbol: str) -> Optional[PriceQuote]:
        """Get real-time quote from Alpha Vantage."""
        await self.init_session()
        # Alpha Vantage uses CURRENCY_EXCHANGE_RATE for forex
        params = {
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": "XAU",
            "to_currency": "USD",
            "apikey": self._api_key,
        }

        try:
            async with self._session.get(self._base_url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rate_data = data.get("Realtime Currency Exchange Rate", {})
                    if rate_data:
                        price = float(rate_data.get("5. Exchange Rate", 0))
                        bid = float(rate_data.get("8. Bid Price", price - 0.2))
                        ask = float(rate_data.get("9. Ask Price", price + 0.2))
                        return PriceQuote(
                            symbol="XAUUSD",
                            bid=bid,
                            ask=ask,
                            mid=price,
                            spread=ask - bid,
                            timestamp=rate_data.get("6. Last Refreshed", ""),
                            provider=self.name,
                        )
                    elif "Note" in data:
                        log.warning(f"[AlphaVantage] Rate limit: {data['Note']}")
                        self._is_available = False
        except Exception as e:
            log.error(f"[AlphaVantage] Quote error: {e}")
        return None

    async def get_historical(
        self, symbol: str, timeframe: Timeframe, bars: int = 100
    ) -> Optional[pd.DataFrame]:
        """Get historical data from Alpha Vantage."""
        await self.init_session()

        func_info = self.TIMEFRAME_MAP.get(timeframe)
        if not func_info:
            log.warning(f"[AlphaVantage] Timeframe {timeframe} not supported, using daily.")
            func_info = ("TIME_SERIES_DAILY", None)

        function, interval = func_info
        params = {
            "function": function,
            "symbol": "XAUUSD",
            "apikey": self._api_key,
            "outputsize": "compact" if bars <= 100 else "full",
        }
        if interval:
            params["interval"] = interval

        try:
            async with self._session.get(self._base_url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Find the time series key
                    ts_key = None
                    for key in data:
                        if "Time Series" in key:
                            ts_key = key
                            break
                    if ts_key:
                        return self._parse_time_series(data[ts_key], bars)
                    elif "Note" in data:
                        log.warning(f"[AlphaVantage] {data['Note']}")
                        self._is_available = False
        except Exception as e:
            log.error(f"[AlphaVantage] Historical error: {e}")
        return None

    def _parse_time_series(self, ts_data: Dict, bars: int) -> pd.DataFrame:
        """Parse Alpha Vantage time series to DataFrame."""
        rows = []
        for ts, values in sorted(ts_data.items()):
            rows.append({
                "timestamp": ts,
                "open": float(values.get("1. open", 0)),
                "high": float(values.get("2. high", 0)),
                "low": float(values.get("3. low", 0)),
                "close": float(values.get("4. close", 0)),
                "volume": float(values.get("5. volume", 0)),
            })

        df = pd.DataFrame(rows[-bars:])
        return df


# ──────────────────────────────────────────────
# GoldAPI Provider
# ──────────────────────────────────────────────

class GoldAPIProvider(DataProvider):
    """
    GoldAPI.io provider — spot price only (no historical OHLCV).
    Docs: https://www.goldapi.io/dashboard
    """

    def __init__(self, config: DataFeedConfig):
        super().__init__("GoldAPI")
        self.config = config
        self._api_key = config.goldapi_api_key
        self._base_url = config.goldapi_base_url

    async def get_quote(self, symbol: str) -> Optional[PriceQuote]:
        """Get spot gold price from GoldAPI."""
        await self.init_session()
        url = f"{self._base_url}/XAU/USD"
        headers = {
            "x-access-token": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data.get("price", 0))
                    if price > 0:
                        return PriceQuote(
                            symbol="XAUUSD",
                            bid=price - float(data.get("spread", 0.3)) / 2,
                            ask=price + float(data.get("spread", 0.3)) / 2,
                            mid=price,
                            spread=float(data.get("spread", 0.3)),
                            timestamp=data.get("timestamp", ""),
                            provider=self.name,
                        )
                elif resp.status == 403:
                    log.error("[GoldAPI] Invalid API key.")
                    self._is_available = False
                elif resp.status == 429:
                    log.warning("[GoldAPI] Rate limit exceeded.")
                    self._is_available = False
        except Exception as e:
            log.error(f"[GoldAPI] Quote error: {e}")
        return None

    async def get_historical(
        self, symbol: str, timeframe: Timeframe, bars: int = 100
    ) -> Optional[pd.DataFrame]:
        """GoldAPI does not support historical data — returns None."""
        log.info("[GoldAPI] Historical data not supported by this provider.")
        return None


# ──────────────────────────────────────────────
# Data Feed Manager (Orchestrator with Failover)
# ──────────────────────────────────────────────

class DataFeedManager:
    """
    Manages multiple data providers with automatic failover.
    
    Priority order:
    1. TwelveData (primary — best for real-time + historical)
    2. Alpha Vantage (secondary — good historical)
    3. GoldAPI (tertiary — spot price only)
    
    Usage:
        config = DataFeedConfig(twelvedata_api_key="...", alphavantage_api_key="...")
        feed = DataFeedManager(config)
        await feed.initialize()
        
        quote = await feed.get_quote()
        df = await feed.get_ohlcv(timeframe=Timeframe.H1, bars=200)
        
        await feed.shutdown()
    """

    def __init__(self, config: DataFeedConfig):
        self.config = config
        self.providers: List[DataProvider] = []
        self._cache: Dict[str, Dict] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._initialized = False

        # Initialize providers based on available keys
        if config.twelvedata_api_key:
            self.providers.append(TwelveDataProvider(config))
        if config.alphavantage_api_key:
            self.providers.append(AlphaVantageProvider(config))
        if config.goldapi_api_key:
            self.providers.append(GoldAPIProvider(config))

        if not self.providers:
            log.warning("No API keys configured! Add at least one provider key.")

        log.info(f"DataFeedManager initialized | "
                 f"Providers: {[p.name for p in self.providers]} | "
                 f"Symbol: {config.symbol}")

    async def initialize(self):
        """Initialize all provider sessions."""
        for provider in self.providers:
            await provider.init_session()
        self._initialized = True
        log.info("All data feed sessions initialized.")

    async def shutdown(self):
        """Close all provider sessions."""
        for provider in self.providers:
            await provider.close_session()
        self._initialized = False
        log.info("All data feed sessions closed.")

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    async def get_quote(self, symbol: str = None) -> Optional[PriceQuote]:
        """
        Get real-time price quote with automatic failover.
        Tries each provider in priority order until one succeeds.
        """
        symbol = symbol or self.config.symbol

        # Check cache
        cache_key = f"quote_{symbol}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        # Try providers in order
        for provider in self.providers:
            if not provider.is_available:
                continue
            quote = await provider.get_quote(symbol)
            if quote:
                self._set_cache(cache_key, quote)
                return quote
            log.warning(f"[{provider.name}] Failed, trying next provider...")

        log.error("All providers failed to get quote.")
        return None

    async def get_ohlcv(
        self,
        timeframe: Timeframe = None,
        bars: int = 100,
        symbol: str = None,
    ) -> Optional[pd.DataFrame]:
        """
        Get historical OHLCV data with automatic failover.
        Returns DataFrame with columns: timestamp, open, high, low, close, volume
        """
        symbol = symbol or self.config.symbol
        timeframe = timeframe or self.config.default_timeframe

        # Check cache
        cache_key = f"ohlcv_{symbol}_{timeframe.value}_{bars}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        # Try providers in order
        for provider in self.providers:
            if not provider.is_available:
                continue
            df = await provider.get_historical(symbol, timeframe, bars)
            if df is not None and not df.empty:
                self._set_cache(cache_key, df)
                log.info(f"[{provider.name}] Got {len(df)} bars "
                         f"({timeframe.value}) for {symbol}")
                return df
            log.warning(f"[{provider.name}] No historical data, trying next...")

        log.error("All providers failed to get historical data.")
        return None

    async def get_latest_price(self, symbol: str = None) -> float:
        """Convenience method — returns just the mid price as float."""
        quote = await self.get_quote(symbol)
        return quote.price if quote else 0.0

    async def start_streaming(self, on_tick: Callable, symbol: str = None):
        """
        Start real-time price streaming via WebSocket (TwelveData only).
        Falls back to polling if WebSocket unavailable.
        """
        symbol = symbol or self.config.symbol

        # Try WebSocket first (TwelveData)
        for provider in self.providers:
            if isinstance(provider, TwelveDataProvider):
                log.info("Starting real-time WebSocket stream...")
                await provider.start_websocket(symbol, on_tick)
                return

        # Fallback: polling loop
        log.info("No WebSocket provider — falling back to polling mode.")
        while True:
            quote = await self.get_quote(symbol)
            if quote and on_tick:
                if asyncio.iscoroutinefunction(on_tick):
                    await on_tick(quote)
                else:
                    on_tick(quote)
            await asyncio.sleep(self.config.cache_ttl_seconds)

    async def stop_streaming(self):
        """Stop any active streaming."""
        for provider in self.providers:
            if isinstance(provider, TwelveDataProvider):
                await provider.stop_websocket()

    # ──────────────────────────────────────────
    # Cache Layer
    # ──────────────────────────────────────────

    def _get_cached(self, key: str):
        """Get cached value if not expired."""
        if key in self._cache:
            ts = self._cache_timestamps.get(key, 0)
            if time.time() - ts < self.config.cache_ttl_seconds:
                return self._cache[key]
            else:
                del self._cache[key]
                del self._cache_timestamps[key]
        return None

    def _set_cache(self, key: str, value):
        """Store value in cache."""
        self._cache[key] = value
        self._cache_timestamps[key] = time.time()

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()
        self._cache_timestamps.clear()

    # ──────────────────────────────────────────
    # Provider Health
    # ──────────────────────────────────────────

    def get_status(self) -> Dict:
        """Get status of all providers."""
        return {
            "providers": [
                {
                    "name": p.name,
                    "available": p.is_available,
                }
                for p in self.providers
            ],
            "cache_size": len(self._cache),
            "initialized": self._initialized,
        }


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

async def main():
    """Test data feed standalone."""
    import os

    config = DataFeedConfig(
        twelvedata_api_key=os.environ.get("TWELVEDATA_API_KEY", ""),
        alphavantage_api_key=os.environ.get("ALPHAVANTAGE_API_KEY", ""),
        goldapi_api_key=os.environ.get("GOLDAPI_API_KEY", ""),
        symbol="XAU/USD",
        default_timeframe=Timeframe.H1,
    )

    feed = DataFeedManager(config)
    await feed.initialize()

    # Get quote
    quote = await feed.get_quote()
    if quote:
        log.info(f"Current price: ${quote.price:.2f} (spread: {quote.spread:.2f}) "
                 f"via {quote.provider}")

    # Get historical
    df = await feed.get_ohlcv(timeframe=Timeframe.H1, bars=50)
    if df is not None:
        log.info(f"Historical data: {len(df)} bars")
        log.info(f"Latest close: ${df['close'].iloc[-1]:.2f}")

    # Status
    log.info(f"Provider status: {feed.get_status()}")

    await feed.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
