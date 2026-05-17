"""
Chastiefol — Real-Time Data Feed (XAUUSD)
Fetches live & historical XAUUSD prices WITHOUT external API keys.

Primary Provider: TradingView WebSocket (FREE, no key required)
  - Real-time tick data (same as TradingView charts)
  - Historical OHLCV bars (200 bars loaded on subscribe)
  - Auto-reconnect with failover

Secondary Provider: CCXT/Binance (for PAXG/USDT as gold proxy)
  - Fallback if TradingView WS is unavailable
  - Uses same CCXT infrastructure as crypto module

REMOVED (no longer needed):
  - TwelveData (rate limited, requires paid API key)
  - AlphaVantage (25 req/day limit, stale data)
  - GoldAPI (spot price only, NO historical candles)

The scanner REQUIRES historical OHLCV data to compute indicators (EMA, RSI, PSAR, etc).
TradingView WS provides this out of the box — 200 H1 bars on subscribe.
"""

import logging
import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable
from enum import Enum

import pandas as pd

from .tradingview_ws import (
    TradingViewWSProvider,
    TVWebSocketConfig,
    TVTick,
    TVBar,
)

from Common.health import HealthStatus, default_registry

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


# TradingView timeframe mapping
TV_TIMEFRAME_MAP = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.D1: "1D",
    Timeframe.W1: "1W",
}


@dataclass
class DataFeedConfig:
    """Configuration for data feed — NO external API keys needed."""
    # Symbol
    symbol: str = "OANDA:XAUUSD"
    symbol_alt: List[str] = field(default_factory=lambda: [
        "FOREXCOM:XAUUSD", "FX:XAUUSD", "CAPITALCOM:GOLD",
    ])

    # General
    default_timeframe: Timeframe = Timeframe.H1
    bars_to_load: int = 200
    cache_ttl_seconds: int = 30

    # TradingView WebSocket (primary — FREE)
    tv_ws_url: str = "wss://data.tradingview.com/socket.io/websocket"
    tv_reconnect_attempts: int = 5
    tv_reconnect_delay: float = 3.0

    # CCXT/Binance fallback (for PAXG/USDT gold proxy)
    binance_api_key: str = ""
    binance_secret: str = ""
    use_binance_fallback: bool = True
    binance_gold_symbol: str = "PAXG/USDT"


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
        return self.mid if self.mid > 0 else (self.bid + self.ask) / 2 if self.bid > 0 else 0.0


# ──────────────────────────────────────────────
# Data Feed Manager (TradingView WS + CCXT Fallback)
# ──────────────────────────────────────────────

class DataFeedManager:
    """
    XAUUSD data feed using TradingView WebSocket (FREE, no API key).

    Provides:
    - Real-time price quotes (tick streaming)
    - Historical OHLCV bars (200 bars loaded on subscribe)
    - Auto-reconnect + failover to CCXT/Binance

    The TradingView WebSocket loads historical bars immediately on subscribe,
    solving the "no historical candles" problem that broke the scanner.

    Usage:
        config = DataFeedConfig()  # No API keys needed!
        feed = DataFeedManager(config)
        await feed.initialize()

        # Get historical bars for analysis
        df = await feed.get_ohlcv(timeframe=Timeframe.H1, bars=200)

        # Get current price
        quote = await feed.get_quote()

        await feed.shutdown()
    """

    def __init__(self, config: DataFeedConfig = None):
        self.config = config or DataFeedConfig()

        # TradingView WebSocket provider (primary)
        self._tv_provider: Optional[TradingViewWSProvider] = None
        self._tv_connected = False

        # CCXT fallback (secondary)
        self._ccxt_provider = None

        # Cache
        self._quote_cache: Optional[PriceQuote] = None
        self._quote_cache_time: float = 0.0
        self._ohlcv_cache: Dict[str, pd.DataFrame] = {}
        self._ohlcv_cache_times: Dict[str, float] = {}

        # State
        self._initialized = False
        self._on_tick_callbacks: List[Callable] = []

        # Health reporting — surfaced via /health so operators can see
        # whether the live feed is dead and we're trading on stale data.
        self._health = default_registry()
        self._health.register("datafeed")

        log.info(f"DataFeedManager initialized | "
                 f"Symbol: {self.config.symbol} | "
                 f"Provider: TradingView WS (FREE) | "
                 f"Bars: {self.config.bars_to_load}")

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    async def initialize(self):
        """Initialize data feed — connect to TradingView WebSocket."""
        log.info("Initializing data feed (TradingView WebSocket)...")

        # 1. Start TradingView WebSocket
        tv_config = TVWebSocketConfig(
            ws_url=self.config.tv_ws_url,
            symbol=self.config.symbol,
            alt_symbols=self.config.symbol_alt,
            timeframe=TV_TIMEFRAME_MAP.get(self.config.default_timeframe, "60"),
            bars_to_load=self.config.bars_to_load,
            reconnect_attempts=self.config.tv_reconnect_attempts,
            reconnect_delay=self.config.tv_reconnect_delay,
        )
        self._tv_provider = TradingViewWSProvider(tv_config)
        self._tv_provider.on_tick = self._on_tv_tick

        connected = await self._tv_provider.connect()
        if connected:
            await self._tv_provider.subscribe()
            self._tv_connected = True
            log.info("  ✓ TradingView WebSocket connected (FREE, no API key)")

            # Wait for bars to load
            await asyncio.sleep(3)
            bars = self._tv_provider.get_bars()
            if bars:
                log.info(f"  ✓ Loaded {len(bars)} historical bars from TradingView")
            else:
                log.warning("  ⚠ No historical bars yet (may still be loading)")
        else:
            log.warning("  ✗ TradingView WebSocket failed — will use CCXT fallback")

        # 2. Initialize CCXT fallback if configured
        if self.config.use_binance_fallback:
            try:
                from CryptoDataFeed.providers import CCXTProvider, CryptoTimeframe
                self._ccxt_provider = CCXTProvider(
                    exchange_id="binance",
                    api_key=self.config.binance_api_key,
                    secret=self.config.binance_secret,
                )
                await self._ccxt_provider.initialize()
                if self._ccxt_provider.is_available:
                    log.info("  ✓ CCXT/Binance fallback ready (PAXG/USDT)")
            except Exception as e:
                log.info(f"  ⚠ CCXT fallback not available: {e}")
                self._ccxt_provider = None

        self._initialized = True
        log.info("Data feed ready.")

    async def shutdown(self):
        """Disconnect all providers."""
        if self._tv_provider:
            await self._tv_provider.disconnect()
        if self._ccxt_provider:
            await self._ccxt_provider.close()
        self._initialized = False
        log.info("Data feed shut down.")

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    async def get_quote(self, symbol: str = None) -> Optional[PriceQuote]:
        """
        Get real-time XAUUSD price quote.
        Source: TradingView WebSocket tick data (live, no API key).
        """
        # Check cache
        if self._quote_cache and (time.time() - self._quote_cache_time) < 5:
            return self._quote_cache

        # TradingView WS (primary)
        if self._tv_provider and self._tv_connected:
            tick = self._tv_provider.get_latest_tick()
            if tick and tick.price > 0:
                quote = PriceQuote(
                    symbol="XAUUSD",
                    bid=tick.bid if tick.bid > 0 else tick.price - 0.15,
                    ask=tick.ask if tick.ask > 0 else tick.price + 0.15,
                    mid=tick.price,
                    spread=tick.ask - tick.bid if tick.bid > 0 else 0.30,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    provider="TradingView:WS",
                )
                self._quote_cache = quote
                self._quote_cache_time = time.time()
                self._health.report(
                    "datafeed", HealthStatus.HEALTHY, detail="TradingView WS live",
                )
                return quote

        # CCXT fallback (PAXG/USDT as gold proxy)
        if self._ccxt_provider and self._ccxt_provider.is_available:
            try:
                ccxt_quote = await self._ccxt_provider.get_quote(self.config.binance_gold_symbol)
                if ccxt_quote and ccxt_quote.price > 0:
                    quote = PriceQuote(
                        symbol="XAUUSD",
                        bid=ccxt_quote.bid,
                        ask=ccxt_quote.ask,
                        mid=ccxt_quote.price,
                        spread=ccxt_quote.spread,
                        timestamp=ccxt_quote.timestamp,
                        provider="CCXT:Binance(PAXG)",
                    )
                    self._quote_cache = quote
                    self._quote_cache_time = time.time()
                    # Live, but on the proxy — that's a degraded mode.
                    self._health.report(
                        "datafeed",
                        HealthStatus.DEGRADED,
                        detail="primary down, using CCXT PAXG/USDT proxy",
                    )
                    return quote
            except Exception as e:
                # CCXT fallback failed — log so operators can spot rate-limits
                # or auth issues. We still fall through to stale cache below
                # so the trading loop doesn't crash, but the failure must be
                # visible (silently returning stale price masks broken feeds).
                log.warning(
                    "[DataFeed] CCXT fallback quote failed (%s): %s",
                    type(e).__name__,
                    e,
                )

        # Final fallback: stale cache. Warn so it's clear no fresh source worked.
        if self._quote_cache is not None:
            age = time.time() - self._quote_cache_time
            log.warning(
                "[DataFeed] All providers failed — returning stale quote (age=%.1fs)",
                age,
            )
            self._health.report(
                "datafeed",
                HealthStatus.UNHEALTHY,
                detail=f"all providers down; serving stale cache (age={age:.1f}s)",
                stale_age_seconds=round(age, 1),
            )
        else:
            self._health.report(
                "datafeed",
                HealthStatus.UNHEALTHY,
                detail="all providers down; no cached quote",
            )
        return self._quote_cache

    async def get_ohlcv(
        self,
        timeframe: Timeframe = None,
        bars: int = 200,
        symbol: str = None,
    ) -> Optional[pd.DataFrame]:
        """
        Get historical OHLCV data for XAUUSD.

        Primary: TradingView WebSocket (loads bars on subscribe — FREE).
        Fallback: CCXT/Binance PAXG/USDT.

        Returns DataFrame with columns: timestamp, open, high, low, close, volume
        """
        timeframe = timeframe or self.config.default_timeframe

        # Check cache
        cache_key = f"ohlcv_{timeframe.value}_{bars}"
        cached_df = self._get_ohlcv_cache(cache_key)
        if cached_df is not None:
            return cached_df

        # TradingView WS (primary) — already has bars loaded
        if self._tv_provider and self._tv_connected:
            tv_bars = self._tv_provider.get_bars()
            if tv_bars and len(tv_bars) >= 50:
                df = self._tv_provider.get_bars_as_dataframe()
                if df is not None and not df.empty:
                    # Ensure numeric columns
                    for col in ["open", "high", "low", "close", "volume"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                    df = df.tail(bars)
                    self._set_ohlcv_cache(cache_key, df)
                    return df

        # CCXT fallback (PAXG/USDT)
        if self._ccxt_provider and self._ccxt_provider.is_available:
            try:
                from CryptoDataFeed.providers import CryptoTimeframe
                tf_map = {
                    Timeframe.M1: CryptoTimeframe.M1,
                    Timeframe.M5: CryptoTimeframe.M5,
                    Timeframe.M15: CryptoTimeframe.M15,
                    Timeframe.M30: CryptoTimeframe.M30,
                    Timeframe.H1: CryptoTimeframe.H1,
                    Timeframe.H4: CryptoTimeframe.H4,
                    Timeframe.D1: CryptoTimeframe.D1,
                    Timeframe.W1: CryptoTimeframe.W1,
                }
                ccxt_tf = tf_map.get(timeframe)
                if ccxt_tf:
                    df = await self._ccxt_provider.get_historical(
                        self.config.binance_gold_symbol, ccxt_tf, bars
                    )
                    if df is not None and not df.empty:
                        self._set_ohlcv_cache(cache_key, df)
                        log.info(f"[CCXT] Got {len(df)} bars for PAXG/USDT (gold proxy)")
                        return df
            except Exception as e:
                log.warning(f"CCXT OHLCV fallback failed: {e}")

        log.warning("No OHLCV data available from any provider")
        return None

    async def get_latest_price(self, symbol: str = None) -> float:
        """Convenience — returns just the price as float."""
        quote = await self.get_quote(symbol)
        return quote.price if quote else 0.0

    def register_tick_callback(self, callback: Callable):
        """Register a callback for real-time tick updates."""
        self._on_tick_callbacks.append(callback)

    # ──────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────

    def _on_tv_tick(self, tick: TVTick):
        """Handle incoming TradingView tick."""
        # Update quote cache
        self._quote_cache = PriceQuote(
            symbol="XAUUSD",
            bid=tick.bid if tick.bid > 0 else tick.price - 0.15,
            ask=tick.ask if tick.ask > 0 else tick.price + 0.15,
            mid=tick.price,
            spread=tick.ask - tick.bid if tick.bid > 0 else 0.30,
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider="TradingView:WS",
        )
        self._quote_cache_time = time.time()

        # Fire callbacks
        for cb in self._on_tick_callbacks:
            try:
                cb(tick)
            except Exception as e:
                # Don't let one misbehaving callback break the tick loop, but
                # surface it so silent regressions are obvious in logs.
                log.exception(
                    "[DataFeed] Tick callback %r raised %s: %s",
                    getattr(cb, "__qualname__", cb),
                    type(e).__name__,
                    e,
                )

    def _get_ohlcv_cache(self, key: str) -> Optional[pd.DataFrame]:
        """Get cached OHLCV if not expired."""
        if key in self._ohlcv_cache:
            ts = self._ohlcv_cache_times.get(key, 0)
            if time.time() - ts < self.config.cache_ttl_seconds:
                return self._ohlcv_cache[key]
        return None

    def _set_ohlcv_cache(self, key: str, df: pd.DataFrame):
        """Cache OHLCV data."""
        self._ohlcv_cache[key] = df
        self._ohlcv_cache_times[key] = time.time()

    # ──────────────────────────────────────────
    # Status
    # ──────────────────────────────────────────

    def get_status(self) -> Dict:
        """Get data feed status."""
        tv_status = self._tv_provider.get_status() if self._tv_provider else {}
        return {
            "initialized": self._initialized,
            "tradingview_ws": {
                "connected": self._tv_connected,
                "bars_cached": tv_status.get("bars_cached", 0),
                "latest_price": tv_status.get("latest_price", 0),
            },
            "ccxt_fallback": {
                "available": self._ccxt_provider.is_available if self._ccxt_provider else False,
            },
            "cache_size": len(self._ohlcv_cache),
            "provider": "TradingView WS (FREE)" if self._tv_connected else "CCXT/Binance",
        }
