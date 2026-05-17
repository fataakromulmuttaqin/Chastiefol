"""
Chastiefol — Binance WebSocket Real-Time Streaming
Direct connection to Binance WebSocket for live kline/ticker data.

WebSocket Endpoints:
- Spot: wss://stream.binance.com:9443/ws/<streamName>
- Combined: wss://stream.binance.com:9443/stream?streams=<s1>/<s2>/<s3>

Stream Names:
- Kline: <symbol>@kline_<interval>  (e.g. btcusdt@kline_1h)
- Ticker: <symbol>@ticker
- Mini Ticker: <symbol>@miniTicker
- All Mini Tickers: !miniTicker@arr
- Book Ticker: <symbol>@bookTicker
- Trade: <symbol>@trade

Kline Intervals: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M

Reference: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Set
from enum import Enum

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Binance.WebSocket")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

@dataclass
class BinanceWSConfig:
    """Binance WebSocket configuration."""
    # Base WebSocket URL
    base_url: str = "wss://stream.binance.com:9443"
    # Alternative endpoints (for latency or region)
    # wss://stream.binance.com:443
    # wss://data-stream.binance.vision (historical data streaming)
    #
    # Demo/Testnet endpoints:
    # Spot Testnet: wss://testnet.binance.vision
    # Spot Demo: wss://demo-stream.binance.com:9443
    # Futures Demo: wss://demo-fstream.binance.com
    # Futures Testnet: wss://fstream.binancefuture.com

    # Reconnection settings
    reconnect_attempts: int = 10
    reconnect_delay: float = 2.0
    reconnect_max_delay: float = 60.0

    # Ping/Pong keep-alive
    ping_interval: float = 180.0  # Binance closes after 24h, ping every 3min
    ping_timeout: float = 10.0

    # Maximum streams per connection (Binance limit: 1024)
    max_streams_per_connection: int = 200

    @classmethod
    def for_demo_mode(cls, demo_mode: str = "live") -> "BinanceWSConfig":
        """
        Create WebSocket config for a specific demo mode.
        
        Args:
            demo_mode: "live" | "testnet" | "demo" | "futures_demo" | "futures_testnet"
        """
        ws_urls = {
            "live": "wss://stream.binance.com:9443",
            "paper": "wss://stream.binance.com:9443",  # Paper still uses live data
            "testnet": "wss://testnet.binance.vision",
            "demo": "wss://demo-stream.binance.com:9443",
            "futures_demo": "wss://demo-fstream.binance.com",
            "futures_testnet": "wss://fstream.binancefuture.com",
        }
        return cls(base_url=ws_urls.get(demo_mode, ws_urls["live"]))


# ──────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────

@dataclass
class KlineUpdate:
    """Parsed kline/candlestick update from WebSocket."""
    symbol: str              # e.g. "BTCUSDT"
    interval: str            # e.g. "1h"
    open_time: int           # Open time in ms
    close_time: int          # Close time in ms
    open: float
    high: float
    low: float
    close: float
    volume: float            # Base asset volume
    quote_volume: float      # Quote asset volume
    trades: int              # Number of trades
    is_closed: bool          # Whether this candle is closed/final
    timestamp: str = ""

    @property
    def as_dict(self) -> dict:
        """Convert to candle dict compatible with analysis engine."""
        return {
            "timestamp": datetime.fromtimestamp(
                self.open_time / 1000, tz=timezone.utc
            ).isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class TickerUpdate:
    """Parsed 24hr ticker update from WebSocket."""
    symbol: str
    last_price: float
    bid_price: float
    ask_price: float
    high_24h: float
    low_24h: float
    volume_24h: float
    quote_volume_24h: float
    price_change_pct: float
    timestamp: str = ""


# ──────────────────────────────────────────────
# Binance WebSocket Client
# ──────────────────────────────────────────────

class BinanceWebSocket:
    """
    Async Binance WebSocket client for real-time market data.

    Supports:
    - Kline/Candlestick streaming (all intervals)
    - 24hr Ticker updates
    - Multiple symbols via combined streams
    - Auto-reconnection with exponential backoff
    - Callback-based event handling

    Usage:
        ws = BinanceWebSocket()

        # Register callbacks
        ws.on_kline = my_kline_handler
        ws.on_ticker = my_ticker_handler

        # Subscribe to streams
        ws.subscribe_kline("BTC/USDT", "1h")
        ws.subscribe_kline("ETH/USDT", "1h")
        ws.subscribe_ticker("BTC/USDT")

        # Start (blocking)
        await ws.start()

        # Or start in background
        task = asyncio.create_task(ws.start())
    """

    def __init__(self, config: BinanceWSConfig = None):
        self.config = config or BinanceWSConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._running = False
        self._reconnect_count = 0

        # Stream subscriptions
        self._kline_streams: Dict[str, str] = {}  # "btcusdt@kline_1h" → "BTC/USDT"
        self._ticker_streams: Set[str] = set()

        # Callbacks
        self.on_kline: Optional[Callable[[KlineUpdate], None]] = None
        self.on_ticker: Optional[Callable[[TickerUpdate], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # Stats
        self._messages_received = 0
        self._last_message_time = 0.0

    # ──────────────────────────────────────────
    # Subscription Management
    # ──────────────────────────────────────────

    def subscribe_kline(self, symbol: str, interval: str = "1h"):
        """
        Subscribe to kline stream for a symbol.
        
        Args:
            symbol: Trading pair in CCXT format (e.g. "BTC/USDT")
            interval: Kline interval (1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M)
        """
        stream_symbol = self._to_binance_symbol(symbol)
        stream_name = f"{stream_symbol}@kline_{interval}"
        self._kline_streams[stream_name] = symbol
        log.info(f"[WS] Subscribed to kline: {symbol} ({interval})")

    def subscribe_ticker(self, symbol: str):
        """Subscribe to 24hr ticker stream."""
        stream_symbol = self._to_binance_symbol(symbol)
        stream_name = f"{stream_symbol}@ticker"
        self._ticker_streams.add(stream_name)
        log.info(f"[WS] Subscribed to ticker: {symbol}")

    def subscribe_multiple_klines(self, symbols: List[str], interval: str = "1h"):
        """Subscribe to kline streams for multiple symbols."""
        for symbol in symbols:
            self.subscribe_kline(symbol, interval)

    def unsubscribe_kline(self, symbol: str, interval: str = "1h"):
        """Unsubscribe from a kline stream."""
        stream_symbol = self._to_binance_symbol(symbol)
        stream_name = f"{stream_symbol}@kline_{interval}"
        self._kline_streams.pop(stream_name, None)

    # ──────────────────────────────────────────
    # Connection Lifecycle
    # ──────────────────────────────────────────

    async def start(self):
        """Start the WebSocket connection and begin receiving data."""
        self._running = True
        self._session = aiohttp.ClientSession()

        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[WS] Connection error: {e}")
                if self.on_error:
                    if asyncio.iscoroutinefunction(self.on_error):
                        await self.on_error(str(e))
                    else:
                        self.on_error(str(e))

            if not self._running:
                break

            # Reconnect with exponential backoff
            self._reconnect_count += 1
            if self._reconnect_count > self.config.reconnect_attempts:
                log.error("[WS] Max reconnection attempts reached. Stopping.")
                break

            delay = min(
                self.config.reconnect_delay * (2 ** (self._reconnect_count - 1)),
                self.config.reconnect_max_delay,
            )
            log.info(f"[WS] Reconnecting in {delay:.1f}s "
                     f"(attempt {self._reconnect_count}/{self.config.reconnect_attempts})")
            await asyncio.sleep(delay)

        await self.stop()

    async def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        log.info(f"[WS] Stopped. Total messages received: {self._messages_received}")

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    # ──────────────────────────────────────────
    # Internal Connection Logic
    # ──────────────────────────────────────────

    async def _connect(self):
        """Establish WebSocket connection and start message loop."""
        url = self._build_url()
        if not url:
            log.warning("[WS] No streams subscribed — nothing to connect")
            await asyncio.sleep(5)
            return

        log.info(f"[WS] Connecting to Binance WebSocket...")
        log.info(f"[WS] Streams: {len(self._kline_streams) + len(self._ticker_streams)}")

        async with self._session.ws_connect(
            url,
            heartbeat=self.config.ping_interval,
            timeout=self.config.ping_timeout,
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0

            log.info("[WS] Connected to Binance WebSocket")
            if self.on_connect:
                if asyncio.iscoroutinefunction(self.on_connect):
                    await self.on_connect()
                else:
                    self.on_connect()

            # Message receive loop
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    log.error(f"[WS] WebSocket error: {ws.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    log.warning("[WS] WebSocket closed by server")
                    break

        # Disconnected
        if self.on_disconnect:
            if asyncio.iscoroutinefunction(self.on_disconnect):
                await self.on_disconnect()
            else:
                self.on_disconnect()

    def _build_url(self) -> str:
        """Build the combined streams URL."""
        all_streams = list(self._kline_streams.keys()) + list(self._ticker_streams)
        if not all_streams:
            return ""

        if len(all_streams) == 1:
            return f"{self.config.base_url}/ws/{all_streams[0]}"
        else:
            streams_str = "/".join(all_streams[:self.config.max_streams_per_connection])
            return f"{self.config.base_url}/stream?streams={streams_str}"

    # ──────────────────────────────────────────
    # Message Handling
    # ──────────────────────────────────────────

    async def _handle_message(self, raw: str):
        """Parse and route incoming WebSocket message."""
        self._messages_received += 1
        self._last_message_time = asyncio.get_event_loop().time()

        try:
            data = json.loads(raw)

            # Combined stream format: {"stream": "...", "data": {...}}
            if "stream" in data:
                stream = data["stream"]
                payload = data["data"]
            else:
                # Single stream format
                payload = data
                stream = ""

            event_type = payload.get("e", "")

            if event_type == "kline":
                await self._handle_kline(payload, stream)
            elif event_type == "24hrTicker":
                await self._handle_ticker(payload)

        except json.JSONDecodeError:
            log.warning(f"[WS] Invalid JSON received")
        except Exception as e:
            log.error(f"[WS] Message handling error: {e}")

    async def _handle_kline(self, data: dict, stream: str = ""):
        """Parse kline event and fire callback."""
        k = data.get("k", {})
        if not k:
            return

        # Resolve original CCXT-format symbol from stream name
        original_symbol = self._kline_streams.get(stream, data.get("s", ""))

        update = KlineUpdate(
            symbol=original_symbol or data.get("s", ""),
            interval=k.get("i", ""),
            open_time=k.get("t", 0),
            close_time=k.get("T", 0),
            open=float(k.get("o", 0)),
            high=float(k.get("h", 0)),
            low=float(k.get("l", 0)),
            close=float(k.get("c", 0)),
            volume=float(k.get("v", 0)),
            quote_volume=float(k.get("q", 0)),
            trades=int(k.get("n", 0)),
            is_closed=k.get("x", False),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if self.on_kline:
            if asyncio.iscoroutinefunction(self.on_kline):
                await self.on_kline(update)
            else:
                self.on_kline(update)

    async def _handle_ticker(self, data: dict):
        """Parse 24hr ticker event and fire callback."""
        update = TickerUpdate(
            symbol=data.get("s", ""),
            last_price=float(data.get("c", 0)),
            bid_price=float(data.get("b", 0)),
            ask_price=float(data.get("a", 0)),
            high_24h=float(data.get("h", 0)),
            low_24h=float(data.get("l", 0)),
            volume_24h=float(data.get("v", 0)),
            quote_volume_24h=float(data.get("q", 0)),
            price_change_pct=float(data.get("P", 0)),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if self.on_ticker:
            if asyncio.iscoroutinefunction(self.on_ticker):
                await self.on_ticker(update)
            else:
                self.on_ticker(update)

    # ──────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────

    @staticmethod
    def _to_binance_symbol(symbol: str) -> str:
        """Convert CCXT symbol format to Binance stream format.
        BTC/USDT → btcusdt
        """
        return symbol.replace("/", "").lower()

    @staticmethod
    def _from_binance_symbol(binance_symbol: str) -> str:
        """Convert Binance symbol to CCXT format (best effort).
        BTCUSDT → BTC/USDT
        """
        # Common quote currencies (ordered by length for greedy match)
        quotes = ["USDT", "BUSD", "BTC", "ETH", "BNB", "USD"]
        upper = binance_symbol.upper()
        for quote in quotes:
            if upper.endswith(quote):
                base = upper[:-len(quote)]
                return f"{base}/{quote}"
        return binance_symbol

    def get_stats(self) -> Dict:
        """Get WebSocket connection stats."""
        return {
            "connected": self.is_connected,
            "messages_received": self._messages_received,
            "kline_streams": len(self._kline_streams),
            "ticker_streams": len(self._ticker_streams),
            "reconnect_count": self._reconnect_count,
        }
