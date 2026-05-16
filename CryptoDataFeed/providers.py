"""
Chastiefol — Crypto Data Providers
Individual provider implementations for crypto market data.

Providers:
1. CCXTProvider — Primary (Binance + multi-exchange via CCXT library)
2. CoinGeckoProvider — Free public API, no key required
3. CoinMarketCapProvider — Requires API key, market cap data
4. CoinStatsProvider — Portfolio & coin data API
"""

import logging
import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
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
log = logging.getLogger("CryptoDataFeed.Providers")


# ──────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────

class CryptoTimeframe(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"
    MO1 = "1M"


@dataclass
class CryptoPriceQuote:
    """Real-time crypto price quote."""
    symbol: str = ""          # e.g. "BTC/USDT"
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    last: float = 0.0
    spread: float = 0.0
    volume_24h: float = 0.0
    change_24h_pct: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    timestamp: str = ""
    provider: str = ""

    @property
    def price(self) -> float:
        return self.last if self.last > 0 else self.mid


# ──────────────────────────────────────────────
# Abstract Provider Base
# ──────────────────────────────────────────────

class CryptoDataProvider(ABC):
    """Abstract base class for crypto data providers."""

    def __init__(self, name: str):
        self.name = name
        self._session: Optional[aiohttp.ClientSession] = None
        self._is_available = True
        self._last_request_time = 0.0
        self._rate_limit_delay = 0.5  # seconds between requests

    async def init_session(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _rate_limit(self):
        """Simple rate limiter."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[CryptoPriceQuote]:
        pass

    @abstractmethod
    async def get_historical(
        self, symbol: str, timeframe: CryptoTimeframe, bars: int
    ) -> Optional[pd.DataFrame]:
        pass

    @property
    def is_available(self) -> bool:
        return self._is_available


# ──────────────────────────────────────────────
# CCXT Provider (Primary)
# ──────────────────────────────────────────────

class CCXTProvider(CryptoDataProvider):
    """
    CCXT-based data provider.
    Uses the CCXT library for exchange-agnostic data access.
    Primary exchange: Binance, with failover to others.

    Docs: https://docs.ccxt.com
    GitHub: https://github.com/ccxt/ccxt

    Features:
    - Unified API across 100+ exchanges
    - OHLCV candlestick data
    - Real-time ticker/quote
    - Order book depth
    - All Binance spot pairs supported
    """

    # CCXT timeframe mapping
    TIMEFRAME_MAP = {
        CryptoTimeframe.M1: "1m",
        CryptoTimeframe.M3: "3m",
        CryptoTimeframe.M5: "5m",
        CryptoTimeframe.M15: "15m",
        CryptoTimeframe.M30: "30m",
        CryptoTimeframe.H1: "1h",
        CryptoTimeframe.H2: "2h",
        CryptoTimeframe.H4: "4h",
        CryptoTimeframe.H6: "6h",
        CryptoTimeframe.H8: "8h",
        CryptoTimeframe.H12: "12h",
        CryptoTimeframe.D1: "1d",
        CryptoTimeframe.D3: "3d",
        CryptoTimeframe.W1: "1w",
        CryptoTimeframe.MO1: "1M",
    }

    def __init__(self, exchange_id: str = "binance",
                 api_key: str = "", secret: str = "",
                 sandbox: bool = False):
        super().__init__(f"CCXT:{exchange_id}")
        self.exchange_id = exchange_id
        self._api_key = api_key
        self._secret = secret
        self._sandbox = sandbox
        self._exchange = None
        self._markets_loaded = False

    async def initialize(self):
        """Initialize the CCXT exchange instance."""
        try:
            import ccxt.async_support as ccxt_async

            exchange_class = getattr(ccxt_async, self.exchange_id)
            config = {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                },
            }
            if self._api_key:
                config["apiKey"] = self._api_key
            if self._secret:
                config["secret"] = self._secret

            self._exchange = exchange_class(config)

            if self._sandbox:
                self._exchange.set_sandbox_mode(True)

            # Load markets
            await self._exchange.load_markets()
            self._markets_loaded = True
            self._is_available = True

            log.info(f"[{self.name}] Initialized | "
                     f"Markets: {len(self._exchange.markets)} | "
                     f"Sandbox: {self._sandbox}")

        except ImportError:
            log.error(f"[{self.name}] CCXT library not installed. "
                      f"Run: pip install ccxt")
            self._is_available = False
        except Exception as e:
            log.error(f"[{self.name}] Initialization failed: {e}")
            self._is_available = False

    async def close(self):
        """Close the CCXT exchange connection."""
        if self._exchange:
            await self._exchange.close()
            self._exchange = None

    async def get_quote(self, symbol: str) -> Optional[CryptoPriceQuote]:
        """Get real-time ticker from exchange via CCXT."""
        if not self._exchange or not self._is_available:
            return None

        try:
            ticker = await self._exchange.fetch_ticker(symbol)
            return CryptoPriceQuote(
                symbol=symbol,
                bid=float(ticker.get("bid", 0) or 0),
                ask=float(ticker.get("ask", 0) or 0),
                mid=(float(ticker.get("bid", 0) or 0) + float(ticker.get("ask", 0) or 0)) / 2,
                last=float(ticker.get("last", 0) or 0),
                spread=float(ticker.get("ask", 0) or 0) - float(ticker.get("bid", 0) or 0),
                volume_24h=float(ticker.get("quoteVolume", 0) or 0),
                change_24h_pct=float(ticker.get("percentage", 0) or 0),
                high_24h=float(ticker.get("high", 0) or 0),
                low_24h=float(ticker.get("low", 0) or 0),
                timestamp=datetime.now(timezone.utc).isoformat(),
                provider=self.name,
            )
        except Exception as e:
            log.error(f"[{self.name}] Quote error for {symbol}: {e}")
            return None

    async def get_historical(
        self, symbol: str, timeframe: CryptoTimeframe, bars: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV historical data via CCXT.
        Uses Binance api.binance.com/api/v3/klines under the hood.
        """
        if not self._exchange or not self._is_available:
            return None

        ccxt_tf = self.TIMEFRAME_MAP.get(timeframe, "1h")

        try:
            ohlcv = await self._exchange.fetch_ohlcv(
                symbol, timeframe=ccxt_tf, limit=bars
            )

            if not ohlcv:
                return None

            df = pd.DataFrame(ohlcv, columns=[
                "timestamp", "open", "high", "low", "close", "volume"
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.astype({
                "open": float, "high": float, "low": float,
                "close": float, "volume": float,
            })

            return df

        except Exception as e:
            log.error(f"[{self.name}] Historical error for {symbol} ({ccxt_tf}): {e}")
            return None

    async def get_all_symbols(self) -> List[str]:
        """Get all available trading symbols from the exchange."""
        if not self._exchange or not self._markets_loaded:
            return []
        return list(self._exchange.markets.keys())

    async def get_usdt_pairs(self) -> List[str]:
        """Get all USDT trading pairs (most liquid on Binance)."""
        if not self._exchange or not self._markets_loaded:
            return []
        return [
            symbol for symbol, market in self._exchange.markets.items()
            if market.get("quote") == "USDT"
            and market.get("active", True)
            and market.get("spot", True)
        ]

    async def get_order_book(self, symbol: str, limit: int = 20) -> Optional[Dict]:
        """Get order book depth."""
        if not self._exchange:
            return None
        try:
            return await self._exchange.fetch_order_book(symbol, limit=limit)
        except Exception as e:
            log.error(f"[{self.name}] Order book error: {e}")
            return None

    async def get_multiple_tickers(self, symbols: List[str] = None) -> Dict[str, CryptoPriceQuote]:
        """Fetch tickers for multiple symbols at once."""
        if not self._exchange:
            return {}
        try:
            tickers = await self._exchange.fetch_tickers(symbols)
            result = {}
            for sym, ticker in tickers.items():
                result[sym] = CryptoPriceQuote(
                    symbol=sym,
                    bid=float(ticker.get("bid", 0) or 0),
                    ask=float(ticker.get("ask", 0) or 0),
                    mid=(float(ticker.get("bid", 0) or 0) + float(ticker.get("ask", 0) or 0)) / 2,
                    last=float(ticker.get("last", 0) or 0),
                    spread=float(ticker.get("ask", 0) or 0) - float(ticker.get("bid", 0) or 0),
                    volume_24h=float(ticker.get("quoteVolume", 0) or 0),
                    change_24h_pct=float(ticker.get("percentage", 0) or 0),
                    high_24h=float(ticker.get("high", 0) or 0),
                    low_24h=float(ticker.get("low", 0) or 0),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    provider=self.name,
                )
            return result
        except Exception as e:
            log.error(f"[{self.name}] Multiple tickers error: {e}")
            return {}


# ──────────────────────────────────────────────
# CoinGecko Provider (Secondary — Free, no API key)
# ──────────────────────────────────────────────

class CoinGeckoProvider(CryptoDataProvider):
    """
    CoinGecko API provider.
    Docs: https://www.coingecko.com/en/api/documentation
    Free tier: 10-30 calls/min (no API key needed)

    Good for:
    - Market cap data
    - Historical OHLC (limited granularity)
    - Coin metadata & rankings
    - 24h price changes
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    # Map common symbols to CoinGecko IDs
    SYMBOL_TO_ID = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
        "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
        "ATOM": "cosmos", "LTC": "litecoin", "ETC": "ethereum-classic",
        "FIL": "filecoin", "APT": "aptos", "ARB": "arbitrum",
        "OP": "optimism", "NEAR": "near", "SHIB": "shiba-inu",
        "TRX": "tron", "PEPE": "pepe", "SUI": "sui",
        "INJ": "injective-protocol", "FET": "fetch-ai",
        "RENDER": "render-token", "IMX": "immutable-x",
        "SEI": "sei-network", "TIA": "celestia", "JUP": "jupiter-exchange-solana",
        "WIF": "dogwifcoin", "AAVE": "aave", "MKR": "maker",
        "RUNE": "thorchain", "FTM": "fantom", "GRT": "the-graph",
        "SAND": "the-sandbox", "MANA": "decentraland", "AXS": "axie-infinity",
        "CRV": "curve-dao-token", "LDO": "lido-dao", "SNX": "havven",
        "ALGO": "algorand", "XLM": "stellar", "VET": "vechain",
        "HBAR": "hedera-hashgraph", "ICP": "internet-computer",
        "EGLD": "elrond-erd-2", "THETA": "theta-token",
    }

    def __init__(self, api_key: str = ""):
        super().__init__("CoinGecko")
        self._api_key = api_key  # Pro API key (optional)
        self._rate_limit_delay = 2.0  # 30 calls/min = ~2s between

    def _get_coin_id(self, symbol: str) -> str:
        """Convert symbol (BTC) to CoinGecko coin ID (bitcoin)."""
        # Extract base from pair like "BTC/USDT" → "BTC"
        base = symbol.split("/")[0] if "/" in symbol else symbol
        base = base.upper()
        return self.SYMBOL_TO_ID.get(base, base.lower())

    async def get_quote(self, symbol: str) -> Optional[CryptoPriceQuote]:
        """Get price via CoinGecko simple/price endpoint."""
        await self.init_session()
        await self._rate_limit()

        coin_id = self._get_coin_id(symbol)
        url = f"{self.BASE_URL}/simple/price"
        params = {
            "ids": coin_id,
            "vs_currencies": "usd",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
        if self._api_key:
            params["x_cg_pro_api_key"] = self._api_key

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if coin_id in data:
                        coin_data = data[coin_id]
                        price = float(coin_data.get("usd", 0))
                        return CryptoPriceQuote(
                            symbol=symbol,
                            last=price,
                            mid=price,
                            bid=price * 0.9999,  # Approximate
                            ask=price * 1.0001,
                            spread=price * 0.0002,
                            volume_24h=float(coin_data.get("usd_24h_vol", 0)),
                            change_24h_pct=float(coin_data.get("usd_24h_change", 0)),
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            provider=self.name,
                        )
                elif resp.status == 429:
                    log.warning("[CoinGecko] Rate limit hit")
                    self._is_available = False
                    await asyncio.sleep(60)
                    self._is_available = True
        except Exception as e:
            log.error(f"[CoinGecko] Quote error: {e}")
        return None

    async def get_historical(
        self, symbol: str, timeframe: CryptoTimeframe, bars: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        Get historical OHLC from CoinGecko.
        Note: CoinGecko OHLC has limited granularity (1d, 4h for 1-30 days).
        """
        await self.init_session()
        await self._rate_limit()

        coin_id = self._get_coin_id(symbol)

        # Calculate days needed based on timeframe
        tf_to_days = {
            CryptoTimeframe.M5: max(1, bars // 288),
            CryptoTimeframe.M15: max(1, bars // 96),
            CryptoTimeframe.H1: max(1, bars // 24),
            CryptoTimeframe.H4: max(2, bars // 6),
            CryptoTimeframe.D1: bars,
            CryptoTimeframe.W1: bars * 7,
        }
        days = tf_to_days.get(timeframe, bars)

        url = f"{self.BASE_URL}/coins/{coin_id}/ohlc"
        params = {"vs_currency": "usd", "days": str(min(days, 365))}
        if self._api_key:
            params["x_cg_pro_api_key"] = self._api_key

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        df = pd.DataFrame(data, columns=[
                            "timestamp", "open", "high", "low", "close"
                        ])
                        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                        df["volume"] = 0.0  # CoinGecko OHLC doesn't include volume
                        return df.tail(bars)
                elif resp.status == 429:
                    log.warning("[CoinGecko] Rate limit on historical")
                    self._is_available = False
        except Exception as e:
            log.error(f"[CoinGecko] Historical error: {e}")
        return None

    async def get_market_data(self, limit: int = 100) -> Optional[List[Dict]]:
        """Get top coins by market cap."""
        await self.init_session()
        await self._rate_limit()

        url = f"{self.BASE_URL}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": str(limit),
            "page": "1",
            "sparkline": "false",
        }

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            log.error(f"[CoinGecko] Market data error: {e}")
        return None


# ──────────────────────────────────────────────
# CoinMarketCap Provider (Tertiary)
# ──────────────────────────────────────────────

class CoinMarketCapProvider(CryptoDataProvider):
    """
    CoinMarketCap API provider.
    Docs: https://coinmarketcap.com/api/documentation/v1/
    Free tier: 10,000 calls/month, 333/day

    Good for:
    - Market cap & rankings
    - Latest quotes with market metrics
    - Historical data (paid plans)
    - Coin metadata
    """

    BASE_URL = "https://pro-api.coinmarketcap.com/v1"

    # Map symbols for CMC (uses symbol directly)
    def __init__(self, api_key: str = ""):
        super().__init__("CoinMarketCap")
        self._api_key = api_key
        self._rate_limit_delay = 3.0  # ~333 calls/day

        if not api_key:
            log.warning("[CoinMarketCap] No API key — provider disabled")
            self._is_available = False

    def _headers(self) -> Dict:
        return {
            "X-CMC_PRO_API_KEY": self._api_key,
            "Accept": "application/json",
        }

    def _extract_symbol(self, pair: str) -> str:
        """Extract base symbol from pair: BTC/USDT → BTC"""
        return pair.split("/")[0] if "/" in pair else pair

    async def get_quote(self, symbol: str) -> Optional[CryptoPriceQuote]:
        """Get latest quote from CoinMarketCap."""
        if not self._is_available:
            return None

        await self.init_session()
        await self._rate_limit()

        base_symbol = self._extract_symbol(symbol)
        url = f"{self.BASE_URL}/cryptocurrency/quotes/latest"
        params = {"symbol": base_symbol, "convert": "USD"}

        try:
            async with self._session.get(url, params=params, headers=self._headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    coin_data = data.get("data", {}).get(base_symbol, {})
                    if coin_data:
                        quote = coin_data.get("quote", {}).get("USD", {})
                        price = float(quote.get("price", 0))
                        return CryptoPriceQuote(
                            symbol=symbol,
                            last=price,
                            mid=price,
                            bid=price * 0.9999,
                            ask=price * 1.0001,
                            spread=price * 0.0002,
                            volume_24h=float(quote.get("volume_24h", 0)),
                            change_24h_pct=float(quote.get("percent_change_24h", 0)),
                            high_24h=0.0,  # Not provided in basic endpoint
                            low_24h=0.0,
                            timestamp=quote.get("last_updated", ""),
                            provider=self.name,
                        )
                elif resp.status == 429:
                    log.warning("[CoinMarketCap] Rate limit hit")
                    self._is_available = False
                elif resp.status == 401:
                    log.error("[CoinMarketCap] Invalid API key")
                    self._is_available = False
        except Exception as e:
            log.error(f"[CoinMarketCap] Quote error: {e}")
        return None

    async def get_historical(
        self, symbol: str, timeframe: CryptoTimeframe, bars: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        CoinMarketCap historical OHLCV requires paid plan.
        Returns None on free tier — will failover to CCXT/CoinGecko.
        """
        log.info("[CoinMarketCap] Historical OHLCV requires paid plan — skipping")
        return None

    async def get_global_metrics(self) -> Optional[Dict]:
        """Get global crypto market metrics."""
        if not self._is_available:
            return None

        await self.init_session()
        await self._rate_limit()

        url = f"{self.BASE_URL}/global-metrics/quotes/latest"
        try:
            async with self._session.get(url, headers=self._headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {})
        except Exception as e:
            log.error(f"[CoinMarketCap] Global metrics error: {e}")
        return None


# ──────────────────────────────────────────────
# CoinStats Provider (Quaternary)
# ──────────────────────────────────────────────

class CoinStatsProvider(CryptoDataProvider):
    """
    CoinStats API provider.
    Docs: https://coinstats.app/api-docs
    Free tier: Limited calls

    Good for:
    - Coin prices & market data
    - Portfolio tracking data
    - News aggregation
    """

    BASE_URL = "https://openapiv1.coinstats.app"

    # CoinStats uses its own coin IDs
    SYMBOL_TO_ID = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binance-coin",
        "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche",
        "MATIC": "polygon", "LINK": "chainlink", "UNI": "uniswap",
        "ATOM": "cosmos", "LTC": "litecoin", "ETC": "ethereum-classic",
    }

    def __init__(self, api_key: str = ""):
        super().__init__("CoinStats")
        self._api_key = api_key
        self._rate_limit_delay = 2.0

        if not api_key:
            log.warning("[CoinStats] No API key — provider disabled")
            self._is_available = False

    def _headers(self) -> Dict:
        return {
            "X-API-KEY": self._api_key,
            "Accept": "application/json",
        }

    def _get_coin_id(self, symbol: str) -> str:
        base = symbol.split("/")[0] if "/" in symbol else symbol
        return self.SYMBOL_TO_ID.get(base.upper(), base.lower())

    async def get_quote(self, symbol: str) -> Optional[CryptoPriceQuote]:
        """Get coin price from CoinStats."""
        if not self._is_available:
            return None

        await self.init_session()
        await self._rate_limit()

        coin_id = self._get_coin_id(symbol)
        url = f"{self.BASE_URL}/coins/{coin_id}"

        try:
            async with self._session.get(url, headers=self._headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data.get("price", 0))
                    if price > 0:
                        return CryptoPriceQuote(
                            symbol=symbol,
                            last=price,
                            mid=price,
                            bid=price * 0.9999,
                            ask=price * 1.0001,
                            spread=price * 0.0002,
                            volume_24h=float(data.get("volume", 0)),
                            change_24h_pct=float(data.get("priceChange1d", 0)),
                            high_24h=0.0,
                            low_24h=0.0,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            provider=self.name,
                        )
                elif resp.status == 429:
                    log.warning("[CoinStats] Rate limit hit")
                    self._is_available = False
        except Exception as e:
            log.error(f"[CoinStats] Quote error: {e}")
        return None

    async def get_historical(
        self, symbol: str, timeframe: CryptoTimeframe, bars: int = 200
    ) -> Optional[pd.DataFrame]:
        """CoinStats has limited historical support — defer to CCXT."""
        log.info("[CoinStats] Historical data limited — deferring to primary provider")
        return None

    async def get_coins_list(self, limit: int = 100) -> Optional[List[Dict]]:
        """Get list of coins with basic data."""
        if not self._is_available:
            return None

        await self.init_session()
        await self._rate_limit()

        url = f"{self.BASE_URL}/coins"
        params = {"limit": str(limit), "currency": "USD"}

        try:
            async with self._session.get(url, params=params, headers=self._headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", [])
        except Exception as e:
            log.error(f"[CoinStats] Coins list error: {e}")
        return None
