"""
Chastiefol — TradingView WebSocket Data Provider (Secondary/Fallback)
Connects to TradingView's public WebSocket for real-time XAUUSD price data.

Features:
- Real-time tick data (same as TradingView charts)
- OHLCV bar streaming (configurable timeframe)
- No API key required, no paid subscription
- Anonymous WebSocket connection
- Auto-reconnect on disconnect
- OHLCV bar accumulation from ticks

TradingView WebSocket Protocol:
- URL: wss://data.tradingview.com/socket.io/websocket
- Session-based with unique auth_token
- Messages are length-prefixed with ~ delimiter
- Uses chart sessions for data subscriptions

Symbol format: "OANDA:XAUUSD" or "FX:XAUUSD" or "FOREXCOM:XAUUSD"

Dependencies:
- websockets (pip install websockets)

NOTE: This is an UNOFFICIAL API (reverse-engineered WebSocket).
TradingView may change the protocol without notice.
Use cTrader FIX as PRIMARY, this as FALLBACK only.
"""

import json
import re
import string
import random
import logging
import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("DataFeed.TradingViewWS")


@dataclass
class TVWebSocketConfig:
    """TradingView WebSocket configuration."""
    ws_url: str = "wss://data.tradingview.com/socket.io/websocket"
    origin: str = "https://www.tradingview.com"
    symbol: str = "OANDA:XAUUSD"
    alt_symbols: List[str] = field(default_factory=lambda: [
        "FOREXCOM:XAUUSD", "FX:XAUUSD", "CAPITALCOM:GOLD",
    ])
    timeframe: str = "60"
    bars_to_load: int = 200
    reconnect_attempts: int = 5
    reconnect_delay: float = 3.0
    heartbeat_interval: float = 10.0
    tick_throttle_ms: int = 500


@dataclass
class TVTick:
    """Single price tick from TradingView."""
    symbol: str = ""
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    timestamp: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.price


@dataclass
class TVBar:
    """Single OHLCV bar from TradingView."""
    timestamp: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
            if self.timestamp else "",
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class TradingViewWSProvider:
    """
    Real-time data provider using TradingView's public WebSocket.
    No API key or paid subscription required.

    Usage:
        config = TVWebSocketConfig(symbol="OANDA:XAUUSD", timeframe="60")
        provider = TradingViewWSProvider(config)
        provider.on_tick = lambda tick: print(f"Price: {tick.price}")
        await provider.connect()
        await provider.subscribe()
        bars = provider.get_bars()
        price = provider.get_latest_price()
        await provider.disconnect()
    """

    def __init__(self, config: TVWebSocketConfig = None):
        self.config = config or TVWebSocketConfig()
        self.on_tick: Optional[Callable[[TVTick], Any]] = None
        self.on_bar: Optional[Callable[[TVBar], Any]] = None
        self.on_bars_loaded: Optional[Callable[[List[TVBar]], Any]] = None
        self.on_disconnect: Optional[Callable[[], Any]] = None

        self._ws = None
        self._connected = False
        self._chart_session = ""
        self._quote_session = ""
        self._running = False
        self._reader_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._latest_tick: Optional[TVTick] = None
        self._bars: List[TVBar] = []
        self._last_tick_callback_time = 0.0

        log.info(f"TradingViewWS initialized | Symbol: {self.config.symbol} | "
                 f"TF: {self.config.timeframe}")

    async def connect(self) -> bool:
        """Establish WebSocket connection to TradingView."""
        try:
            import websockets
        except ImportError:
            log.error("websockets package not installed. Run: pip install websockets")
            return False

        for attempt in range(1, self.config.reconnect_attempts + 1):
            try:
                import websockets
                self._ws = await websockets.connect(
                    self.config.ws_url,
                    origin=self.config.origin,
                    extra_headers={"Origin": self.config.origin},
                    ping_interval=None,
                    max_size=2**20,
                )
                self._connected = True
                self._running = True
                self._chart_session = self._gen_session("cs")
                self._quote_session = self._gen_session("qs")
                self._reader_task = asyncio.create_task(self._reader_loop())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                log.info(f"TradingView WebSocket connected (attempt {attempt})")
                await self._init_sessions()
                return True
            except Exception as e:
                log.warning(f"TV WS connection attempt {attempt} failed: {e}")
                if attempt < self.config.reconnect_attempts:
                    await asyncio.sleep(self.config.reconnect_delay * attempt)

        log.error("All TradingView WebSocket connection attempts failed.")
        return False

    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        log.info("TradingView WebSocket disconnected.")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def reconnect(self) -> bool:
        """Reconnect and resubscribe."""
        await self.disconnect()
        if await self.connect():
            await self.subscribe()
            return True
        return False

    async def subscribe(self, symbol: str = None):
        """Subscribe to real-time quote + chart bars."""
        symbol = symbol or self.config.symbol
        await self._send("quote_create_session", [self._quote_session])
        await self._send("quote_set_fields", [
            self._quote_session, "lp", "bid", "ask", "volume", "ch", "chp",
            "high_price", "low_price", "open_price",
        ])
        await self._send("quote_add_symbols", [self._quote_session, symbol])
        await self._send("chart_create_session", [self._chart_session])
        await self._send("resolve_symbol", [
            self._chart_session, "sds_sym_1", f"={{\"symbol\":\"{symbol}\"}}"
        ])
        await self._send("create_series", [
            self._chart_session, "sds_1", "s1", "sds_sym_1",
            self.config.timeframe, self.config.bars_to_load, ""
        ])
        log.info(f"Subscribed to {symbol} (quote + chart TF={self.config.timeframe})")

    def get_latest_price(self) -> float:
        """Get latest cached price."""
        return self._latest_tick.price if self._latest_tick else 0.0

    def get_latest_tick(self) -> Optional[TVTick]:
        return self._latest_tick

    def get_bars(self) -> List[TVBar]:
        return self._bars.copy()

    def get_bars_as_dataframe(self):
        """Convert cached bars to pandas DataFrame."""
        import pandas as pd
        if not self._bars:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        return pd.DataFrame([bar.to_dict() for bar in self._bars])

    def get_status(self) -> dict:
        return {
            "connected": self._connected,
            "symbol": self.config.symbol,
            "timeframe": self.config.timeframe,
            "latest_price": self.get_latest_price(),
            "bars_cached": len(self._bars),
            "last_tick_age": (
                round(time.time() - self._latest_tick.timestamp, 1)
                if self._latest_tick else None
            ),
        }

    # ── Internal Protocol ──

    async def _init_sessions(self):
        await self._send("set_auth_token", ["unauthorized_user_token"])

    async def _send(self, method: str, params: list):
        if not self._ws or not self._connected:
            return
        message = json.dumps({"m": method, "p": params})
        formatted = f"~m~{len(message)}~m~{message}"
        try:
            await self._ws.send(formatted)
        except Exception as e:
            log.error(f"TV WS send error: {e}")
            self._connected = False

    async def _reader_loop(self):
        while self._running and self._ws:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                self._process_raw_message(raw)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    log.warning(f"TV WS reader error: {e}")
                    self._connected = False
                    await asyncio.sleep(self.config.reconnect_delay)
                    await self.reconnect()
                break

    async def _heartbeat_loop(self):
        while self._running and self._connected:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                if self._ws and self._connected:
                    await self._ws.send("~m~2~m~~h~")
            except asyncio.CancelledError:
                break
            except Exception:
                break

    def _process_raw_message(self, raw: str):
        messages = re.findall(r'~m~\d+~m~(.+?)(?=~m~\d+~m~|$)', raw, re.DOTALL)
        for msg_str in messages:
            if msg_str.startswith("~h~"):
                continue
            try:
                data = json.loads(msg_str)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            method = data.get("m", "")
            params = data.get("p", [])
            if method == "qsd":
                self._handle_quote_data(params)
            elif method == "timescale_update":
                self._handle_bar_data(params)
            elif method == "du":
                self._handle_bar_update(params)
            elif method == "series_completed":
                if self.on_bars_loaded and self._bars:
                    self.on_bars_loaded(self._bars)

    def _handle_quote_data(self, params: list):
        if len(params) < 2:
            return
        data = params[1]
        if not isinstance(data, dict):
            return
        values = data.get("v", {})
        if not values:
            return
        tick = TVTick(
            symbol=data.get("n", self.config.symbol),
            price=float(values.get("lp", 0) or 0),
            bid=float(values.get("bid", 0) or 0),
            ask=float(values.get("ask", 0) or 0),
            volume=float(values.get("volume", 0) or 0),
            change=float(values.get("ch", 0) or 0),
            change_pct=float(values.get("chp", 0) or 0),
            timestamp=time.time(),
        )
        if tick.price <= 0:
            return
        self._latest_tick = tick
        now = time.time() * 1000
        if (self.on_tick and
                now - self._last_tick_callback_time > self.config.tick_throttle_ms):
            self._last_tick_callback_time = now
            self.on_tick(tick)

    def _handle_bar_data(self, params: list):
        if len(params) < 2:
            return
        data = params[1]
        if not isinstance(data, dict):
            return
        for key, series_data in data.items():
            if not isinstance(series_data, dict):
                continue
            bars_data = series_data.get("s", [])
            if not bars_data:
                continue
            self._bars = []
            for bar_item in bars_data:
                if isinstance(bar_item, dict):
                    v = bar_item.get("v", [])
                    if len(v) >= 6:
                        self._bars.append(TVBar(
                            timestamp=float(v[0]), open=float(v[1]),
                            high=float(v[2]), low=float(v[3]),
                            close=float(v[4]), volume=float(v[5]),
                        ))
            if self._bars:
                log.info(f"Loaded {len(self._bars)} historical bars from TradingView")

    def _handle_bar_update(self, params: list):
        if len(params) < 2:
            return
        data = params[1]
        if not isinstance(data, dict):
            return
        for key, series_data in data.items():
            if not isinstance(series_data, dict):
                continue
            for bar_item in series_data.get("s", []):
                if isinstance(bar_item, dict):
                    v = bar_item.get("v", [])
                    if len(v) >= 6:
                        bar = TVBar(
                            timestamp=float(v[0]), open=float(v[1]),
                            high=float(v[2]), low=float(v[3]),
                            close=float(v[4]), volume=float(v[5]),
                        )
                        if self._bars and self._bars[-1].timestamp == bar.timestamp:
                            self._bars[-1] = bar
                        else:
                            self._bars.append(bar)
                        if self.on_bar:
                            self.on_bar(bar)

    @staticmethod
    def _gen_session(prefix: str = "cs") -> str:
        chars = string.ascii_lowercase + string.digits
        return f"{prefix}_{''.join(random.choices(chars, k=12))}"
