"""
Chastiefol — Binance Connector (Order Execution via CCXT)
Full trading connector for Binance spot market using the CCXT library.

Features:
- Market, Limit, Stop-Limit order execution
- Position management (open/close/modify)
- Account balance & portfolio queries
- Order status tracking & history
- Paper trading mode
- Automatic retry with exponential backoff

CCXT Docs: https://docs.ccxt.com
Binance API: https://developers.binance.com/docs/binance-spot-api-docs

Usage:
    config = BinanceConfig(api_key="...", secret="...")
    connector = BinanceConnector(config)
    await connector.connect()

    # Execute market order
    result = await connector.market_order("BTC/USDT", "buy", amount=0.001)

    # Get account balance
    balance = await connector.get_balance()

    await connector.disconnect()
"""

import logging
import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Binance.Connector")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LIMIT = "stop_limit"
    STOP_MARKET = "stop_market"


class BinanceDemoMode(str, Enum):
    """Binance demo/testnet mode selection."""
    LIVE = "live"               # Real money trading
    PAPER = "paper"             # Local simulation (no API calls)
    TESTNET = "testnet"         # Binance Spot Testnet (testnet.binance.vision)
    DEMO = "demo"              # Binance Demo Trading (api-gcp.binance.com with demo keys)
    FUTURES_DEMO = "futures_demo"  # Binance Futures Demo (demo-fapi.binance.com)
    FUTURES_TESTNET = "futures_testnet"  # Binance Futures Testnet (testnet.binancefuture.com)


# Endpoint configuration for each demo mode
BINANCE_ENDPOINTS = {
    BinanceDemoMode.LIVE: {
        "rest": "https://api.binance.com",
        "ws": "wss://stream.binance.com:9443",
        "ws_api": "wss://ws-api.binance.com:443/ws-api/v3",
    },
    BinanceDemoMode.TESTNET: {
        "rest": "https://testnet.binance.vision",
        "ws": "wss://testnet.binance.vision",
        "ws_api": "wss://ws-api.testnet.binance.vision/ws-api/v3",
    },
    BinanceDemoMode.DEMO: {
        "rest": "https://api-gcp.binance.com",
        "ws": "wss://demo-stream.binance.com:9443",
        "ws_api": "wss://demo-ws-api.binance.com/ws-api/v3",
    },
    BinanceDemoMode.FUTURES_DEMO: {
        "rest": "https://demo-fapi.binance.com",
        "ws": "wss://demo-fstream.binance.com",
        "ws_api": "",
    },
    BinanceDemoMode.FUTURES_TESTNET: {
        "rest": "https://testnet.binancefuture.com",
        "ws": "wss://fstream.binancefuture.com",
        "ws_api": "",
    },
}


@dataclass
class BinanceConfig:
    """Binance connector configuration."""
    api_key: str = ""
    secret: str = ""

    # Exchange settings
    sandbox: bool = False               # Use Binance testnet (legacy flag, use demo_mode instead)
    demo_mode: str = "paper"            # "live" | "paper" | "testnet" | "demo" | "futures_demo" | "futures_testnet"
    default_type: str = "spot"          # "spot" | "future" | "margin"
    recv_window: int = 5000             # Binance recvWindow (ms)

    # Execution settings
    paper_mode: bool = True             # Simulate orders without real execution
    max_retries: int = 3
    retry_delay: float = 1.0

    # Risk guards
    max_order_value_usd: float = 10000.0   # Max single order value
    max_daily_orders: int = 50              # Max orders per day
    confirm_large_orders: bool = True       # Require confirmation for large orders
    large_order_threshold_usd: float = 5000.0

    @property
    def effective_demo_mode(self) -> BinanceDemoMode:
        """Get the effective demo mode enum value."""
        if self.paper_mode and self.demo_mode == "paper":
            return BinanceDemoMode.PAPER
        try:
            return BinanceDemoMode(self.demo_mode)
        except ValueError:
            return BinanceDemoMode.PAPER

    @property
    def endpoints(self) -> Dict[str, str]:
        """Get the correct API endpoints for current demo mode."""
        mode = self.effective_demo_mode
        if mode == BinanceDemoMode.PAPER:
            # Paper mode doesn't need real endpoints but use live for market data
            return BINANCE_ENDPOINTS[BinanceDemoMode.LIVE]
        return BINANCE_ENDPOINTS.get(mode, BINANCE_ENDPOINTS[BinanceDemoMode.LIVE])

    @property
    def is_demo_or_testnet(self) -> bool:
        """Check if running in any non-live mode."""
        return self.effective_demo_mode != BinanceDemoMode.LIVE

    @property
    def is_futures(self) -> bool:
        """Check if running in futures mode."""
        return self.effective_demo_mode in (
            BinanceDemoMode.FUTURES_DEMO,
            BinanceDemoMode.FUTURES_TESTNET,
        ) or self.default_type == "future"


@dataclass
class OrderResult:
    """Standardized order execution result."""
    success: bool = False
    order_id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    amount: float = 0.0            # Requested amount
    filled: float = 0.0            # Actually filled
    remaining: float = 0.0
    price: float = 0.0             # Requested price (limit)
    average_price: float = 0.0     # Average fill price
    cost: float = 0.0              # Total cost (amount × price)
    fee: float = 0.0               # Trading fee
    fee_currency: str = ""
    status: str = ""               # "open", "closed", "canceled"
    timestamp: str = ""
    error_message: str = ""
    raw_response: Dict = field(default_factory=dict)


@dataclass
class AccountBalance:
    """Account balance information."""
    total_usd: float = 0.0
    free_usd: float = 0.0
    used_usd: float = 0.0
    assets: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # assets = {"BTC": {"free": 0.5, "used": 0.1, "total": 0.6}, ...}
    timestamp: str = ""


@dataclass
class OpenPosition:
    """Represents an open position (bought asset)."""
    symbol: str = ""
    side: str = "buy"
    amount: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    timestamp: str = ""



# ──────────────────────────────────────────────
# Binance Connector
# ──────────────────────────────────────────────

class BinanceConnector:
    """
    Production-ready Binance trading connector via CCXT.

    Supports:
    - Spot trading (all USDT pairs)
    - Market & Limit orders
    - OCO (One-Cancels-Other) for SL/TP
    - Account balance queries
    - Order history & status
    - Paper trading mode
    - Automatic rate limiting (handled by CCXT)

    Architecture:
    ┌──────────────────────────────────────────────┐
    │           BinanceConnector                    │
    ├──────────────────────────────────────────────┤
    │  CCXT async_support.binance                  │
    │    ├─ Market Orders                          │
    │    ├─ Limit Orders                           │
    │    ├─ Stop-Limit Orders (SL/TP)              │
    │    ├─ OCO Orders                             │
    │    ├─ Account & Balance                      │
    │    └─ Order Management                       │
    ├──────────────────────────────────────────────┤
    │  Paper Trading Engine (simulation)           │
    │  Risk Guards (value limits, daily caps)      │
    └──────────────────────────────────────────────┘
    """

    def __init__(self, config: BinanceConfig = None):
        self.config = config or BinanceConfig()
        self._exchange = None
        self._connected = False
        self._markets_loaded = False

        # Paper trading state
        self._paper_balance: Dict[str, float] = {"USDT": 10000.0}
        self._paper_orders: List[Dict] = []
        self._paper_order_id = 0

        # Daily order tracking
        self._daily_orders = 0
        self._daily_reset_date = ""

        # Trade history
        self._order_history: List[OrderResult] = []

        log.info(f"BinanceConnector initialized | "
                 f"Mode: {'PAPER' if self.config.paper_mode else self.config.demo_mode.upper()} | "
                 f"Type: {self.config.default_type} | "
                 f"Demo: {self.config.effective_demo_mode.value}")

    # ──────────────────────────────────────────
    # Connection Lifecycle
    # ──────────────────────────────────────────

    async def connect(self) -> bool:
        """Initialize CCXT exchange and verify connection."""
        try:
            import ccxt.async_support as ccxt_async

            demo_mode = self.config.effective_demo_mode
            endpoints = self.config.endpoints

            exchange_config = {
                "apiKey": self.config.api_key,
                "secret": self.config.secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": self.config.default_type,
                    "recvWindow": self.config.recv_window,
                    "adjustForTimeDifference": True,
                },
            }

            # Select the correct CCXT exchange class based on mode
            if self.config.is_futures:
                self._exchange = ccxt_async.binance(exchange_config)
                self._exchange.options["defaultType"] = "future"
            else:
                self._exchange = ccxt_async.binance(exchange_config)

            # Configure endpoints based on demo mode
            if demo_mode == BinanceDemoMode.TESTNET:
                # Use CCXT built-in sandbox mode for spot testnet
                self._exchange.set_sandbox_mode(True)
                log.info("[Binance] Spot TESTNET mode enabled (testnet.binance.vision)")

            elif demo_mode == BinanceDemoMode.DEMO:
                # Binance Demo Trading — override URLs manually
                self._exchange.urls["api"]["public"] = endpoints["rest"] + "/api/v3"
                self._exchange.urls["api"]["private"] = endpoints["rest"] + "/api/v3"
                self._exchange.urls["api"]["sapi"] = endpoints["rest"] + "/sapi/v1"
                log.info("[Binance] DEMO TRADING mode enabled (api-gcp.binance.com)")
                log.info("    ⚠ Use API keys from Binance Demo Trading page")

            elif demo_mode == BinanceDemoMode.FUTURES_DEMO:
                # Binance Futures Demo
                self._exchange.urls["api"]["fapiPublic"] = endpoints["rest"] + "/fapi/v1"
                self._exchange.urls["api"]["fapiPrivate"] = endpoints["rest"] + "/fapi/v1"
                self._exchange.urls["api"]["fapiPrivateV2"] = endpoints["rest"] + "/fapi/v2"
                self._exchange.options["defaultType"] = "future"
                log.info("[Binance] FUTURES DEMO mode enabled (demo-fapi.binance.com)")
                log.info("    ⚠ Use API keys from Binance Demo Trading page")

            elif demo_mode == BinanceDemoMode.FUTURES_TESTNET:
                # Binance Futures Testnet — use CCXT sandbox
                self._exchange.set_sandbox_mode(True)
                self._exchange.options["defaultType"] = "future"
                log.info("[Binance] FUTURES TESTNET mode enabled (testnet.binancefuture.com)")

            elif demo_mode == BinanceDemoMode.LIVE:
                log.info("[Binance] LIVE mode — real money trading!")

            elif demo_mode == BinanceDemoMode.PAPER:
                log.info("[Binance] PAPER mode — local simulation (no API calls)")

            # Legacy sandbox flag support
            if self.config.sandbox and demo_mode == BinanceDemoMode.LIVE:
                self._exchange.set_sandbox_mode(True)
                log.info("[Binance] Legacy sandbox flag enabled")

            # Load markets (skip for paper-only mode without API keys)
            if demo_mode != BinanceDemoMode.PAPER or self.config.api_key:
                await self._exchange.load_markets()
                self._markets_loaded = True
                market_count = len(self._exchange.markets)
                log.info(f"[Binance] Connected | Markets: {market_count} | "
                         f"Mode: {demo_mode.value.upper()}")
            else:
                self._markets_loaded = False
                log.info("[Binance] Paper mode — markets not loaded (no API key)")

            self._connected = True

            # Verify API key (if not paper mode and key provided)
            if not self.config.paper_mode and self.config.api_key:
                try:
                    balance = await self._exchange.fetch_balance()
                    usdt_free = float(balance.get("USDT", {}).get("free", 0))
                    log.info(f"[Binance] API verified | USDT balance: ${usdt_free:,.2f} "
                             f"({'DEMO' if self.config.is_demo_or_testnet else 'LIVE'})")
                except Exception as e:
                    log.warning(f"[Binance] API key verification failed: {e}")
                    if not self.config.is_demo_or_testnet:
                        log.warning("[Binance] Falling back to paper mode")
                        self.config.paper_mode = True

            return True

        except ImportError:
            log.error("[Binance] CCXT not installed. Run: pip install ccxt")
            return False
        except Exception as e:
            log.error(f"[Binance] Connection failed: {e}")
            return False

    async def disconnect(self):
        """Close the exchange connection."""
        if self._exchange:
            await self._exchange.close()
            self._exchange = None
        self._connected = False
        log.info("[Binance] Disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._exchange is not None

    # ──────────────────────────────────────────
    # Order Execution
    # ──────────────────────────────────────────

    async def market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        quote_amount: float = 0.0,
        comment: str = "",
    ) -> OrderResult:
        """
        Execute a market order.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT")
            side: "buy" or "sell"
            amount: Base asset amount (e.g. 0.001 BTC)
            quote_amount: If set, buy/sell this USD value instead of base amount
            comment: Order comment/tag

        Returns:
            OrderResult with execution details
        """
        # Daily order limit check
        if not self._check_daily_limit():
            return OrderResult(
                success=False,
                error_message=f"Daily order limit ({self.config.max_daily_orders}) reached"
            )

        # Value guard
        price_estimate = await self._get_price_estimate(symbol)
        order_value = (amount * price_estimate) if amount > 0 else quote_amount
        if order_value > self.config.max_order_value_usd:
            return OrderResult(
                success=False,
                error_message=f"Order value ${order_value:,.2f} exceeds max ${self.config.max_order_value_usd:,.2f}"
            )

        # Paper mode
        if self.config.paper_mode:
            return self._paper_market_order(symbol, side, amount, price_estimate)

        # Live execution
        return await self._execute_with_retry(
            self._do_market_order, symbol, side, amount, quote_amount
        )

    async def limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        comment: str = "",
    ) -> OrderResult:
        """
        Execute a limit order.

        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            amount: Base asset amount
            price: Limit price

        Returns:
            OrderResult
        """
        if not self._check_daily_limit():
            return OrderResult(
                success=False,
                error_message="Daily order limit reached"
            )

        if self.config.paper_mode:
            return self._paper_limit_order(symbol, side, amount, price)

        return await self._execute_with_retry(
            self._do_limit_order, symbol, side, amount, price
        )

    async def stop_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        stop_price: float,
        comment: str = "",
    ) -> OrderResult:
        """
        Execute a stop-limit order (for stop-loss).

        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            amount: Base asset amount
            price: Limit price (execution price)
            stop_price: Trigger price (when to activate)
        """
        if self.config.paper_mode:
            return self._paper_limit_order(symbol, side, amount, stop_price)

        return await self._execute_with_retry(
            self._do_stop_limit_order, symbol, side, amount, price, stop_price
        )

    async def oco_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        take_profit_price: float,
        stop_loss_price: float,
        stop_limit_price: float = 0.0,
    ) -> OrderResult:
        """
        Execute an OCO (One-Cancels-Other) order for SL + TP.
        When one side fills, the other is automatically canceled.

        Args:
            symbol: Trading pair
            side: "sell" for closing a long, "buy" for closing a short
            amount: Amount to close
            take_profit_price: TP limit price
            stop_loss_price: SL trigger price
            stop_limit_price: SL execution price (slightly below trigger)
        """
        if self.config.paper_mode:
            return self._paper_limit_order(symbol, side, amount, take_profit_price)

        if not stop_limit_price:
            # Default: 0.1% below stop for sell, 0.1% above for buy
            if side == "sell":
                stop_limit_price = stop_loss_price * 0.999
            else:
                stop_limit_price = stop_loss_price * 1.001

        try:
            params = {
                "stopLimitPrice": self._exchange.price_to_precision(symbol, stop_limit_price),
                "stopLimitTimeInForce": "GTC",
            }

            order = await self._exchange.create_order(
                symbol=symbol,
                type="oco",
                side=side,
                amount=amount,
                price=take_profit_price,
                params={
                    **params,
                    "stopPrice": stop_loss_price,
                },
            )

            result = self._parse_order_response(order)
            self._record_order(result)
            return result

        except Exception as e:
            log.error(f"[Binance] OCO order failed: {e}")
            return OrderResult(success=False, error_message=str(e))

    # ──────────────────────────────────────────
    # Position Management
    # ──────────────────────────────────────────

    async def open_position(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        comment: str = "Chastiefol",
    ) -> OrderResult:
        """
        Open a position with optional SL/TP.
        Executes market entry + OCO for SL/TP management.

        Args:
            symbol: Trading pair
            side: "buy" (long) or "sell" (short/sell existing)
            amount: Base asset amount
            stop_loss: Stop loss price
            take_profit: Take profit price

        Returns:
            OrderResult from the entry order
        """
        # Execute entry
        entry_result = await self.market_order(symbol, side, amount, comment=comment)
        if not entry_result.success:
            return entry_result

        # Set SL/TP via OCO if both provided
        if stop_loss > 0 and take_profit > 0:
            close_side = "sell" if side == "buy" else "buy"
            filled_amount = entry_result.filled if entry_result.filled > 0 else amount

            oco_result = await self.oco_order(
                symbol=symbol,
                side=close_side,
                amount=filled_amount,
                take_profit_price=take_profit,
                stop_loss_price=stop_loss,
            )
            if not oco_result.success:
                log.warning(f"[Binance] OCO failed ({oco_result.error_message}) — trying fallback SL+TP split")
                # Fallback: place SL and TP as separate orders
                sl_success = False
                tp_success = False

                # Stop-loss: stop-limit order
                sl_price = stop_loss * (0.999 if close_side == "sell" else 1.001)
                sl_result = await self.stop_limit_order(
                    symbol=symbol,
                    side=close_side,
                    amount=filled_amount,
                    price=sl_price,
                    stop_price=stop_loss,
                )
                if sl_result.success:
                    log.info(f"[Binance] SL placed @ {stop_loss:.6f}")
                    sl_success = True
                else:
                    log.warning(f"[Binance] SL fallback failed: {sl_result.error_message}")

                # Take-profit: limit order
                tp_result = await self.limit_order(
                    symbol=symbol,
                    side=close_side,
                    amount=filled_amount,
                    price=take_profit,
                )
                if tp_result.success:
                    log.info(f"[Binance] TP placed @ {take_profit:.6f}")
                    tp_success = True
                else:
                    log.warning(f"[Binance] TP fallback failed: {tp_result.error_message}")

                if not sl_success and not tp_success:
                    entry_result.error_message = f"SL/TP failed: {sl_result.error_message}"
                elif not sl_success:
                    entry_result.error_message = f"SL failed: {sl_result.error_message}"
                elif not tp_success:
                    entry_result.error_message = f"TP failed: {tp_result.error_message}"
                else:
                    entry_result.error_message = f"SL/TP set via fallback (OCO unavailable)"
            else:
                log.info(f"[Binance] SL+TP OCO placed: SL={stop_loss:.6f} TP={take_profit:.6f}")

        elif stop_loss > 0:
            # Only SL
            close_side = "sell" if side == "buy" else "buy"
            filled_amount = entry_result.filled if entry_result.filled > 0 else amount
            sl_result = await self.stop_limit_order(
                symbol=symbol,
                side=close_side,
                amount=filled_amount,
                price=stop_loss * (0.999 if close_side == "sell" else 1.001),
                stop_price=stop_loss,
            )
            if sl_result.success:
                log.info(f"[Binance] SL placed @ {stop_loss:.6f}")
            else:
                log.warning(f"[Binance] SL order failed: {sl_result.error_message}")

        return entry_result

    async def close_position(
        self,
        symbol: str,
        amount: float = 0.0,
        comment: str = "",
    ) -> OrderResult:
        """
        Close a position by selling the held asset.
        If amount=0, sells the entire free balance of the base asset.
        """
        if amount <= 0:
            # Get full balance of base asset
            base = symbol.split("/")[0]
            balance = await self.get_balance()
            amount = balance.assets.get(base, {}).get("free", 0)
            if amount <= 0:
                return OrderResult(
                    success=False,
                    error_message=f"No {base} balance to close"
                )

        return await self.market_order(symbol, "sell", amount, comment=comment)

    # ──────────────────────────────────────────
    # Account & Balance
    # ──────────────────────────────────────────

    async def get_balance(self) -> AccountBalance:
        """Get account balance across all assets."""
        if self.config.paper_mode:
            return self._paper_get_balance()

        if not self._exchange:
            return AccountBalance()

        try:
            balance = await self._exchange.fetch_balance()
            assets = {}
            total_usd = 0.0
            free_usd = 0.0
            used_usd = 0.0

            for currency, amounts in balance.get("total", {}).items():
                if amounts and float(amounts) > 0:
                    assets[currency] = {
                        "free": float(balance.get("free", {}).get(currency, 0) or 0),
                        "used": float(balance.get("used", {}).get(currency, 0) or 0),
                        "total": float(amounts),
                    }

            # USDT as USD proxy
            usdt = assets.get("USDT", {})
            total_usd = usdt.get("total", 0)
            free_usd = usdt.get("free", 0)
            used_usd = usdt.get("used", 0)

            return AccountBalance(
                total_usd=total_usd,
                free_usd=free_usd,
                used_usd=used_usd,
                assets=assets,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            log.error(f"[Binance] Balance fetch error: {e}")
            return AccountBalance()

    async def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """Get all open orders (optionally filtered by symbol)."""
        if self.config.paper_mode:
            return [o for o in self._paper_orders if o.get("status") == "open"]

        if not self._exchange:
            return []

        try:
            return await self._exchange.fetch_open_orders(symbol)
        except Exception as e:
            log.error(f"[Binance] Open orders fetch error: {e}")
            return []

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order."""
        if self.config.paper_mode:
            for order in self._paper_orders:
                if order.get("id") == order_id:
                    order["status"] = "canceled"
                    return True
            return False

        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            log.error(f"[Binance] Cancel order error: {e}")
            return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all open orders for a symbol. Returns count canceled."""
        if self.config.paper_mode:
            count = 0
            for order in self._paper_orders:
                if order.get("symbol") == symbol and order.get("status") == "open":
                    order["status"] = "canceled"
                    count += 1
            return count

        try:
            orders = await self._exchange.fetch_open_orders(symbol)
            for order in orders:
                await self._exchange.cancel_order(order["id"], symbol)
            return len(orders)
        except Exception as e:
            log.error(f"[Binance] Cancel all orders error: {e}")
            return 0

    async def get_order_status(self, order_id: str, symbol: str) -> Optional[Dict]:
        """Get status of a specific order."""
        if self.config.paper_mode:
            for order in self._paper_orders:
                if order.get("id") == order_id:
                    return order
            return None

        try:
            return await self._exchange.fetch_order(order_id, symbol)
        except Exception as e:
            log.error(f"[Binance] Order status error: {e}")
            return None

    async def get_my_trades(self, symbol: str, limit: int = 50) -> List[Dict]:
        """Get recent trades for a symbol."""
        if self.config.paper_mode:
            return [o for o in self._paper_orders
                    if o.get("symbol") == symbol and o.get("status") == "closed"]

        try:
            return await self._exchange.fetch_my_trades(symbol, limit=limit)
        except Exception as e:
            log.error(f"[Binance] Trades fetch error: {e}")
            return []

    # ──────────────────────────────────────────
    # Market Info
    # ──────────────────────────────────────────

    async def get_market_info(self, symbol: str) -> Optional[Dict]:
        """Get market info (min order size, precision, etc.)."""
        if not self._exchange or not self._markets_loaded:
            return None
        return self._exchange.markets.get(symbol)

    async def get_min_order_amount(self, symbol: str) -> float:
        """Get minimum order amount for a symbol."""
        market = await self.get_market_info(symbol)
        if market:
            limits = market.get("limits", {}).get("amount", {})
            return float(limits.get("min", 0) or 0)
        return 0.0

    async def get_trading_fees(self, symbol: str) -> Dict[str, float]:
        """Get maker/taker fees for a symbol."""
        if self.config.paper_mode:
            return {"maker": 0.001, "taker": 0.001}  # 0.1% default

        try:
            fees = await self._exchange.fetch_trading_fee(symbol)
            return {
                "maker": float(fees.get("maker", 0.001)),
                "taker": float(fees.get("taker", 0.001)),
            }
        except Exception:
            return {"maker": 0.001, "taker": 0.001}



    # ──────────────────────────────────────────
    # Internal: Live Execution
    # ──────────────────────────────────────────

    async def _do_market_order(
        self, symbol: str, side: str, amount: float, quote_amount: float = 0.0
    ) -> OrderResult:
        """Execute a live market order via CCXT."""
        try:
            params = {}
            if quote_amount > 0 and side == "buy":
                # Buy with quote amount (e.g. spend $100 of USDT)
                params["quoteOrderQty"] = quote_amount
                amount = None

            order = await self._exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
                params=params,
            )

            result = self._parse_order_response(order)
            self._record_order(result)
            log.info(f"[Binance] Market {side.upper()} {symbol} | "
                     f"Filled: {result.filled} @ ${result.average_price:,.2f} | "
                     f"Cost: ${result.cost:,.2f}")
            return result

        except Exception as e:
            log.error(f"[Binance] Market order failed: {e}")
            return OrderResult(success=False, error_message=str(e))

    async def _do_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> OrderResult:
        """Execute a live limit order via CCXT."""
        try:
            order = await self._exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=amount,
                price=price,
            )

            result = self._parse_order_response(order)
            self._record_order(result)
            log.info(f"[Binance] Limit {side.upper()} {symbol} | "
                     f"Amount: {amount} @ ${price:,.2f}")
            return result

        except Exception as e:
            log.error(f"[Binance] Limit order failed: {e}")
            return OrderResult(success=False, error_message=str(e))

    async def _do_stop_limit_order(
        self, symbol: str, side: str, amount: float,
        price: float, stop_price: float
    ) -> OrderResult:
        """Execute a stop-limit order via CCXT."""
        try:
            params = {"stopPrice": stop_price}
            order = await self._exchange.create_order(
                symbol=symbol,
                type="STOP_LOSS_LIMIT",
                side=side,
                amount=amount,
                price=price,
                params=params,
            )

            result = self._parse_order_response(order)
            self._record_order(result)
            log.info(f"[Binance] Stop-Limit {side.upper()} {symbol} | "
                     f"Stop: ${stop_price:,.2f} Limit: ${price:,.2f}")
            return result

        except Exception as e:
            log.error(f"[Binance] Stop-limit order failed: {e}")
            return OrderResult(success=False, error_message=str(e))

    async def _execute_with_retry(self, func, *args) -> OrderResult:
        """Execute an order function with retry logic."""
        for attempt in range(1, self.config.max_retries + 1):
            result = await func(*args)
            if result.success:
                return result

            # Don't retry certain errors
            if any(err in result.error_message.lower() for err in [
                "insufficient", "balance", "invalid", "min notional"
            ]):
                return result

            if attempt < self.config.max_retries:
                delay = self.config.retry_delay * (2 ** (attempt - 1))
                log.warning(f"[Binance] Retry {attempt}/{self.config.max_retries} "
                           f"in {delay:.1f}s — {result.error_message}")
                await asyncio.sleep(delay)

        return result

    # ──────────────────────────────────────────
    # Internal: Paper Trading
    # ──────────────────────────────────────────

    def _paper_market_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> OrderResult:
        """Simulate a market order in paper mode."""
        self._paper_order_id += 1
        order_id = f"PAPER_{self._paper_order_id}"

        base, quote = symbol.split("/")
        cost = amount * price
        fee = cost * 0.001  # 0.1% fee simulation

        if side == "buy":
            # Check USDT balance
            usdt_balance = self._paper_balance.get(quote, 0)
            if cost + fee > usdt_balance:
                return OrderResult(
                    success=False,
                    error_message=f"Insufficient {quote} balance: "
                                  f"need ${cost + fee:,.2f}, have ${usdt_balance:,.2f}"
                )
            self._paper_balance[quote] = usdt_balance - cost - fee
            self._paper_balance[base] = self._paper_balance.get(base, 0) + amount
        else:
            # Check base balance
            base_balance = self._paper_balance.get(base, 0)
            if amount > base_balance:
                return OrderResult(
                    success=False,
                    error_message=f"Insufficient {base} balance: "
                                  f"need {amount}, have {base_balance}"
                )
            self._paper_balance[base] = base_balance - amount
            self._paper_balance[quote] = self._paper_balance.get(quote, 0) + cost - fee

        result = OrderResult(
            success=True,
            order_id=order_id,
            client_order_id=order_id,
            symbol=symbol,
            side=side,
            order_type="market",
            amount=amount,
            filled=amount,
            remaining=0.0,
            price=price,
            average_price=price,
            cost=cost,
            fee=fee,
            fee_currency=quote,
            status="closed",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self._paper_orders.append(result.__dict__)
        self._record_order(result)
        self._daily_orders += 1

        log.info(f"[PAPER] {side.upper()} {amount} {base} @ ${price:,.2f} | "
                 f"Cost: ${cost:,.2f} | Fee: ${fee:,.2f} | "
                 f"Balance: ${self._paper_balance.get(quote, 0):,.2f} {quote}")

        return result

    def _paper_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> OrderResult:
        """Simulate a limit order (immediately filled in paper mode)."""
        # In paper mode, limit orders fill immediately at requested price
        return self._paper_market_order(symbol, side, amount, price)

    def _paper_get_balance(self) -> AccountBalance:
        """Get paper trading balance."""
        assets = {}
        for currency, amount in self._paper_balance.items():
            if amount > 0:
                assets[currency] = {
                    "free": amount,
                    "used": 0.0,
                    "total": amount,
                }

        usdt = self._paper_balance.get("USDT", 0)
        return AccountBalance(
            total_usd=usdt,
            free_usd=usdt,
            used_usd=0.0,
            assets=assets,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ──────────────────────────────────────────
    # Internal: Helpers
    # ──────────────────────────────────────────

    def _parse_order_response(self, order: Dict) -> OrderResult:
        """Parse CCXT order response into OrderResult."""
        return OrderResult(
            success=order.get("status") != "rejected",
            order_id=str(order.get("id", "")),
            client_order_id=str(order.get("clientOrderId", "")),
            symbol=order.get("symbol", ""),
            side=order.get("side", ""),
            order_type=order.get("type", ""),
            amount=float(order.get("amount", 0) or 0),
            filled=float(order.get("filled", 0) or 0),
            remaining=float(order.get("remaining", 0) or 0),
            price=float(order.get("price", 0) or 0),
            average_price=float(order.get("average", 0) or order.get("price", 0) or 0),
            cost=float(order.get("cost", 0) or 0),
            fee=float(order.get("fee", {}).get("cost", 0) or 0) if order.get("fee") else 0.0,
            fee_currency=order.get("fee", {}).get("currency", "") if order.get("fee") else "",
            status=order.get("status", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
            raw_response=order,
        )

    def _record_order(self, result: OrderResult):
        """Record order in history."""
        self._order_history.append(result)
        # Keep only last 1000
        if len(self._order_history) > 1000:
            self._order_history = self._order_history[-500:]

    def _check_daily_limit(self) -> bool:
        """Check if daily order limit is reached."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_orders = 0
            self._daily_reset_date = today
        return self._daily_orders < self.config.max_daily_orders

    async def _get_price_estimate(self, symbol: str) -> float:
        """Get current price estimate for risk guards."""
        if not self._exchange:
            return 0.0
        try:
            ticker = await self._exchange.fetch_ticker(symbol)
            return float(ticker.get("last", 0) or 0)
        except Exception:
            return 0.0

    # ──────────────────────────────────────────
    # Status & History
    # ──────────────────────────────────────────

    def get_order_history(self, limit: int = 50) -> List[OrderResult]:
        """Get recent order history."""
        return self._order_history[-limit:]

    def get_stats(self) -> Dict:
        """Get connector statistics."""
        return {
            "connected": self._connected,
            "paper_mode": self.config.paper_mode,
            "sandbox": self.config.sandbox,
            "daily_orders": self._daily_orders,
            "total_orders": len(self._order_history),
            "paper_balance": self._paper_balance if self.config.paper_mode else {},
        }


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

async def main():
    """Test Binance connector in paper mode."""
    config = BinanceConfig(
        paper_mode=True,
        max_order_value_usd=50000,
    )

    connector = BinanceConnector(config)
    await connector.connect()

    if connector.is_connected:
        # Paper trade
        result = await connector.market_order("BTC/USDT", "buy", 0.01)
        log.info(f"Order result: {result.success} | Filled: {result.filled}")

        # Balance
        balance = await connector.get_balance()
        log.info(f"Balance: ${balance.total_usd:,.2f}")

        # Stats
        log.info(f"Stats: {connector.get_stats()}")

    await connector.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
