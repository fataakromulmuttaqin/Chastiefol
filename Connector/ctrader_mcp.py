"""
Chastiefol — cTrader Remote MCP Server Connector
Connects to cTrader via the official Remote MCP Server (REST/JSON-RPC).
URL: https://mcp.ctrader.com/trading/mcp

Capabilities:
- Account info & positions
- Market/Limit order execution
- Position management (close, modify SL/TP)
- Symbol info & pricing
"""

import json
import logging
import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Any
from enum import Enum

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cTrader.MCP")


# ──────────────────────────────────────────────
# Enums & Models
# ──────────────────────────────────────────────

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PENDING = "PENDING"


@dataclass
class MCPConfig:
    """Configuration for cTrader Remote MCP Server."""
    url: str = "https://mcp.ctrader.com/trading/mcp"
    access_token: str = ""
    account_id: str = ""
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0


@dataclass
class AccountInfo:
    account_id: str = ""
    balance: float = 0.0
    equity: float = 0.0
    margin_used: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    unrealized_pnl: float = 0.0
    currency: str = "USD"
    leverage: int = 100
    is_live: bool = False


@dataclass
class Position:
    position_id: str = ""
    symbol: str = ""
    side: str = ""
    volume: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    open_time: str = ""
    comment: str = ""


@dataclass
class OrderResult:
    success: bool = False
    order_id: str = ""
    position_id: str = ""
    execution_price: float = 0.0
    filled_volume: float = 0.0
    error_code: str = ""
    error_message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ──────────────────────────────────────────────
# MCP Connector
# ──────────────────────────────────────────────

class CTraderMCPConnector:
    """
    Connector to cTrader via Remote MCP Server (JSON-RPC style REST API).
    
    Usage:
        config = MCPConfig(access_token="Bearer ...", account_id="5820056")
        connector = CTraderMCPConnector(config)
        await connector.connect()
        info = await connector.get_account_info()
        result = await connector.send_market_order("XAUUSD", OrderSide.BUY, 0.01, sl=2340.0, tp=2380.0)
        await connector.disconnect()
    """

    def __init__(self, config: MCPConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._connected = False
        self._request_id = 0
        self._last_heartbeat = 0.0

    # ──────────────────────────────────────────
    # Connection Lifecycle
    # ──────────────────────────────────────────

    async def connect(self) -> bool:
        """Establish connection to cTrader MCP server."""
        try:
            headers = {
                "Authorization": self.config.access_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout),
            )
            # Verify connection with account info request
            info = await self.get_account_info()
            if info and info.account_id:
                self._connected = True
                log.info(f"Connected to cTrader MCP | Account: {info.account_id} | "
                         f"Balance: ${info.balance:.2f} | "
                         f"{'LIVE' if info.is_live else 'DEMO'}")
                return True
            else:
                log.error("Connection verification failed — no account info returned.")
                return False
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False

    async def disconnect(self):
        """Close the connection."""
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        log.info("Disconnected from cTrader MCP.")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._session is not None

    async def reconnect(self) -> bool:
        """Reconnect with exponential backoff."""
        for attempt in range(1, self.config.max_retries + 1):
            log.info(f"Reconnection attempt {attempt}/{self.config.max_retries}...")
            await self.disconnect()
            await asyncio.sleep(self.config.retry_delay * (2 ** (attempt - 1)))
            if await self.connect():
                return True
        log.error("All reconnection attempts failed.")
        return False

    # ──────────────────────────────────────────
    # MCP API Calls
    # ──────────────────────────────────────────

    async def _request(self, method: str, params: Dict[str, Any] = None) -> Dict:
        """Send a JSON-RPC style request to the MCP server."""
        if not self._session:
            raise ConnectionError("Not connected. Call connect() first.")

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }

        for attempt in range(1, self.config.max_retries + 1):
            try:
                async with self._session.post(self.config.url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "error" in data:
                            log.error(f"MCP error: {data['error']}")
                            return {"error": data["error"]}
                        return data.get("result", data)
                    elif resp.status == 401:
                        log.error("Authentication failed — check access token.")
                        return {"error": {"code": 401, "message": "Unauthorized"}}
                    elif resp.status == 429:
                        wait = float(resp.headers.get("Retry-After", 5))
                        log.warning(f"Rate limited. Waiting {wait}s...")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        text = await resp.text()
                        log.error(f"HTTP {resp.status}: {text}")
                        if attempt < self.config.max_retries:
                            await asyncio.sleep(self.config.retry_delay * attempt)
                            continue
                        return {"error": {"code": resp.status, "message": text}}
            except asyncio.TimeoutError:
                log.warning(f"Request timeout (attempt {attempt})")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_delay * attempt)
            except aiohttp.ClientError as e:
                log.error(f"Client error: {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_delay * attempt)

        return {"error": {"code": -1, "message": "Max retries exceeded"}}

    # ──────────────────────────────────────────
    # Account Operations
    # ──────────────────────────────────────────

    async def get_account_info(self) -> Optional[AccountInfo]:
        """Get current account information."""
        result = await self._request("getAccountInfo", {
            "accountId": self.config.account_id,
        })
        if "error" in result:
            return None

        return AccountInfo(
            account_id=str(result.get("accountId", self.config.account_id)),
            balance=float(result.get("balance", 0)),
            equity=float(result.get("equity", 0)),
            margin_used=float(result.get("marginUsed", 0)),
            free_margin=float(result.get("freeMargin", 0)),
            margin_level=float(result.get("marginLevel", 0)),
            unrealized_pnl=float(result.get("unrealizedPnl", 0)),
            currency=result.get("currency", "USD"),
            leverage=int(result.get("leverage", 100)),
            is_live=result.get("isLive", False),
        )

    async def get_positions(self, symbol: str = None) -> List[Position]:
        """Get all open positions, optionally filtered by symbol."""
        params = {"accountId": self.config.account_id}
        if symbol:
            params["symbol"] = symbol

        result = await self._request("getPositions", params)
        if "error" in result:
            return []

        positions = []
        for pos in result.get("positions", []):
            positions.append(Position(
                position_id=str(pos.get("positionId", "")),
                symbol=pos.get("symbol", ""),
                side=pos.get("side", ""),
                volume=float(pos.get("volume", 0)),
                entry_price=float(pos.get("entryPrice", 0)),
                current_price=float(pos.get("currentPrice", 0)),
                stop_loss=pos.get("stopLoss"),
                take_profit=pos.get("takeProfit"),
                pnl=float(pos.get("pnl", 0)),
                swap=float(pos.get("swap", 0)),
                commission=float(pos.get("commission", 0)),
                open_time=pos.get("openTime", ""),
                comment=pos.get("comment", ""),
            ))
        return positions

    # ──────────────────────────────────────────
    # Order Execution
    # ──────────────────────────────────────────

    async def send_market_order(
        self,
        symbol: str,
        side: OrderSide,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "Chastiefol",
    ) -> OrderResult:
        """Send a market order."""
        params = {
            "accountId": self.config.account_id,
            "symbol": symbol,
            "side": side.value,
            "type": OrderType.MARKET.value,
            "volume": volume,
            "comment": comment,
        }
        if stop_loss is not None:
            params["stopLoss"] = stop_loss
        if take_profit is not None:
            params["takeProfit"] = take_profit

        log.info(f"Sending market order: {side.value} {volume} {symbol} "
                 f"SL={stop_loss} TP={take_profit}")

        result = await self._request("sendOrder", params)

        if "error" in result:
            err = result["error"]
            log.error(f"Order rejected: {err}")
            return OrderResult(
                success=False,
                error_code=str(err.get("code", "")),
                error_message=str(err.get("message", "")),
            )

        order_result = OrderResult(
            success=True,
            order_id=str(result.get("orderId", "")),
            position_id=str(result.get("positionId", "")),
            execution_price=float(result.get("executionPrice", 0)),
            filled_volume=float(result.get("filledVolume", volume)),
        )
        log.info(f"✓ Order filled: {order_result.order_id} @ "
                 f"{order_result.execution_price:.2f}")
        return order_result

    async def send_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        volume: float,
        price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "Chastiefol",
    ) -> OrderResult:
        """Send a limit order."""
        params = {
            "accountId": self.config.account_id,
            "symbol": symbol,
            "side": side.value,
            "type": OrderType.LIMIT.value,
            "volume": volume,
            "price": price,
            "comment": comment,
        }
        if stop_loss is not None:
            params["stopLoss"] = stop_loss
        if take_profit is not None:
            params["takeProfit"] = take_profit

        log.info(f"Sending limit order: {side.value} {volume} {symbol} @ {price}")
        result = await self._request("sendOrder", params)

        if "error" in result:
            return OrderResult(
                success=False,
                error_code=str(result["error"].get("code", "")),
                error_message=str(result["error"].get("message", "")),
            )

        return OrderResult(
            success=True,
            order_id=str(result.get("orderId", "")),
            position_id=str(result.get("positionId", "")),
            execution_price=float(result.get("executionPrice", 0)),
            filled_volume=float(result.get("filledVolume", volume)),
        )

    # ──────────────────────────────────────────
    # Position Management
    # ──────────────────────────────────────────

    async def close_position(self, position_id: str, volume: Optional[float] = None) -> OrderResult:
        """Close a position (full or partial)."""
        params = {
            "accountId": self.config.account_id,
            "positionId": position_id,
        }
        if volume is not None:
            params["volume"] = volume

        log.info(f"Closing position {position_id}" +
                 (f" (partial: {volume})" if volume else " (full)"))

        result = await self._request("closePosition", params)

        if "error" in result:
            return OrderResult(
                success=False,
                error_code=str(result["error"].get("code", "")),
                error_message=str(result["error"].get("message", "")),
            )

        return OrderResult(
            success=True,
            order_id=str(result.get("orderId", "")),
            position_id=position_id,
            execution_price=float(result.get("executionPrice", 0)),
            filled_volume=float(result.get("closedVolume", 0)),
        )

    async def close_all_positions(self, symbol: str = None) -> List[OrderResult]:
        """Close all open positions, optionally filtered by symbol."""
        positions = await self.get_positions(symbol)
        results = []
        for pos in positions:
            result = await self.close_position(pos.position_id)
            results.append(result)
        return results

    async def modify_position(
        self,
        position_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Modify SL/TP of an existing position."""
        params = {
            "accountId": self.config.account_id,
            "positionId": position_id,
        }
        if stop_loss is not None:
            params["stopLoss"] = stop_loss
        if take_profit is not None:
            params["takeProfit"] = take_profit

        log.info(f"Modifying position {position_id}: SL={stop_loss}, TP={take_profit}")
        result = await self._request("modifyPosition", params)

        if "error" in result:
            return OrderResult(
                success=False,
                error_code=str(result["error"].get("code", "")),
                error_message=str(result["error"].get("message", "")),
            )

        return OrderResult(success=True, position_id=position_id)

    # ──────────────────────────────────────────
    # Market Data
    # ──────────────────────────────────────────

    async def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        """Get symbol specification (pip size, lot size, spread, etc.)."""
        result = await self._request("getSymbolInfo", {
            "symbol": symbol,
        })
        if "error" in result:
            return None
        return result

    async def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get current bid/ask quote for a symbol."""
        result = await self._request("getQuote", {
            "symbol": symbol,
        })
        if "error" in result:
            return None
        return result
