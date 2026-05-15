"""
Chastiefol — cTrader FIX API Connector
Implements FIX 4.4 protocol for Price and Trade connections to cTrader.

Price Connection: demo-uk-eqx-01.p.c-trader.com:5211 (SSL)
Trade Connection: demo-uk-eqx-01.p.c-trader.com:5212 (SSL)

FIX Protocol Tags Reference:
- 35: MsgType (A=Logon, D=NewOrder, F=Cancel, 0=Heartbeat, V=MarketDataReq)
- 49: SenderCompID
- 56: TargetCompID
- 50: SenderSubID
- 55: Symbol
- 54: Side (1=Buy, 2=Sell)
- 38: OrderQty
- 40: OrdType (1=Market, 2=Limit, 3=Stop)
- 44: Price
- 99: StopPx
"""

import ssl
import socket
import time
import logging
import asyncio
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, Callable, List
from enum import Enum
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cTrader.FIX")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

@dataclass
class FIXConfig:
    """FIX API connection configuration."""
    # Price Connection
    price_host: str = "demo-uk-eqx-01.p.c-trader.com"
    price_port: int = 5211  # SSL

    # Trade Connection
    trade_host: str = "demo-uk-eqx-01.p.c-trader.com"
    trade_port: int = 5212  # SSL

    # Authentication
    sender_comp_id: str = "demo.ctrader.5820056"
    target_comp_id: str = "cServer"
    password: str = ""

    # Sub IDs
    price_sender_sub_id: str = "QUOTE"
    trade_sender_sub_id: str = "TRADE"

    # Connection settings
    use_ssl: bool = True
    heartbeat_interval: int = 30
    reconnect_attempts: int = 5
    reconnect_delay: float = 2.0


# ──────────────────────────────────────────────
# FIX Message Builder
# ──────────────────────────────────────────────

class FIXMessage:
    """FIX 4.4 message builder and parser."""

    SOH = "\x01"  # Standard FIX delimiter
    BEGIN_STRING = "FIX.4.4"

    def __init__(self):
        self.fields: Dict[int, str] = {}
        self._seq_num = 0

    def set(self, tag: int, value) -> "FIXMessage":
        self.fields[tag] = str(value)
        return self

    def get(self, tag: int, default: str = "") -> str:
        return self.fields.get(tag, default)

    def has(self, tag: int) -> bool:
        return tag in self.fields

    def build(self, msg_type: str, seq_num: int, sender: str, target: str,
              sender_sub: str = "", target_sub: str = "") -> bytes:
        """
        Build a complete FIX 4.4 message with proper header and checksum.
        
        FIX message structure:
          8=FIX.4.4|9=BODY_LENGTH|35=...|49=...|...|10=CHECKSUM|
        
        Body = everything from tag 35 to before tag 10 (inclusive of SOH delimiters)
        BodyLength (tag 9) = byte count of body
        Checksum (tag 10) = sum of all bytes from tag 8 through end of body, mod 256
        """
        SOH = self.SOH

        # Build body (tag 35 onwards, before checksum)
        body_parts = []
        body_parts.append(f"35={msg_type}")
        body_parts.append(f"49={sender}")
        body_parts.append(f"56={target}")
        if sender_sub:
            body_parts.append(f"50={sender_sub}")
        if target_sub:
            body_parts.append(f"57={target_sub}")
        body_parts.append(f"34={seq_num}")
        body_parts.append(f"52={self._utc_timestamp()}")

        for tag, value in self.fields.items():
            if tag not in (8, 9, 10, 35, 49, 56, 50, 57, 34, 52):
                body_parts.append(f"{tag}={value}")

        # Body string: each field followed by SOH
        body = SOH.join(body_parts) + SOH

        # Header: BeginString + BodyLength
        header = f"8={self.BEGIN_STRING}{SOH}9={len(body)}{SOH}"

        # Message without checksum
        msg_without_checksum = header + body

        # Checksum: sum of all bytes in header+body mod 256
        checksum = sum(ord(c) for c in msg_without_checksum) % 256
        
        # Complete message
        full_msg = msg_without_checksum + f"10={checksum:03d}{SOH}"

        return full_msg.encode("ascii")

    @staticmethod
    def parse(raw: str) -> "FIXMessage":
        """Parse a raw FIX message string into a FIXMessage object."""
        msg = FIXMessage()
        parts = raw.split(FIXMessage.SOH)
        for part in parts:
            if "=" in part:
                tag_str, value = part.split("=", 1)
                try:
                    msg.fields[int(tag_str)] = value
                except ValueError:
                    pass
        return msg

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]


# ──────────────────────────────────────────────
# FIX Connection
# ──────────────────────────────────────────────

class FIXConnection:
    """
    Manages a single FIX connection (either Price or Trade).
    Handles logon, heartbeat, message sending/receiving.
    """

    def __init__(self, host: str, port: int, config: FIXConfig,
                 sender_sub_id: str, connection_type: str = "PRICE"):
        self.host = host
        self.port = port
        self.config = config
        self.sender_sub_id = sender_sub_id
        self.connection_type = connection_type

        self._socket: Optional[socket.socket] = None
        self._ssl_socket: Optional[ssl.SSLSocket] = None
        self._connected = False
        self._seq_num = 0
        self._recv_seq_num = 0
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

        # Callbacks
        self.on_message: Optional[Callable[[FIXMessage], None]] = None
        self.on_execution: Optional[Callable[[Dict], None]] = None
        self.on_market_data: Optional[Callable[[Dict], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _next_seq(self) -> int:
        self._seq_num += 1
        return self._seq_num

    # ──────────────────────────────────────────
    # Connection Lifecycle
    # ──────────────────────────────────────────

    def connect(self) -> bool:
        """Establish TCP/SSL connection and send Logon."""
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(30)

            if self.config.use_ssl:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self._ssl_socket = context.wrap_socket(
                    self._socket, server_hostname=self.host
                )
                self._ssl_socket.connect((self.host, self.port))
                log.info(f"[{self.connection_type}] SSL connection established "
                         f"to {self.host}:{self.port}")
            else:
                self._socket.connect((self.host, self.port))
                log.info(f"[{self.connection_type}] TCP connection established "
                         f"to {self.host}:{self.port}")

            # Send Logon (MsgType = A)
            if self._send_logon():
                self._connected = True
                self._running = True
                self._start_heartbeat()
                self._start_reader()
                return True
            return False

        except Exception as e:
            log.error(f"[{self.connection_type}] Connection failed: {e}")
            return False

    def disconnect(self):
        """Graceful disconnect — send Logout then close socket."""
        self._running = False
        if self._connected:
            try:
                self._send_logout()
            except Exception:
                pass
        self._connected = False

        if self._ssl_socket:
            try:
                self._ssl_socket.close()
            except Exception:
                pass
        elif self._socket:
            try:
                self._socket.close()
            except Exception:
                pass

        log.info(f"[{self.connection_type}] Disconnected.")

    def reconnect(self) -> bool:
        """Reconnect with exponential backoff."""
        for attempt in range(1, self.config.reconnect_attempts + 1):
            log.info(f"[{self.connection_type}] Reconnect attempt "
                     f"{attempt}/{self.config.reconnect_attempts}")
            self.disconnect()
            time.sleep(self.config.reconnect_delay * (2 ** (attempt - 1)))
            self._seq_num = 0
            if self.connect():
                return True
        log.error(f"[{self.connection_type}] All reconnection attempts failed.")
        return False

    # ──────────────────────────────────────────
    # FIX Messages
    # ──────────────────────────────────────────

    def _send_logon(self) -> bool:
        """Send Logon message (35=A)."""
        msg = FIXMessage()
        msg.set(98, 0)   # EncryptMethod: None
        msg.set(108, self.config.heartbeat_interval)  # HeartBtInt
        msg.set(141, "Y")  # ResetSeqNumFlag
        # Username (tag 553) = numeric account ID only (e.g. "5820056")
        # Extract numeric part from sender_comp_id like "demo.ctrader.5820056" → "5820056"
        username = self.config.sender_comp_id.split(".")[-1]
        msg.set(553, username)
        if self.config.password:
            msg.set(554, self.config.password)  # Password

        raw = msg.build(
            "A", self._next_seq(),
            self.config.sender_comp_id,
            self.config.target_comp_id,
            self.sender_sub_id,
            self.sender_sub_id,  # TargetSubID = same as SenderSubID (QUOTE/TRADE)
        )
        log.info(f"[{self.connection_type}] Sending Logon... "
                 f"(Sender: {self.config.sender_comp_id}, "
                 f"Sub: {self.sender_sub_id}, "
                 f"Password: {'*' * len(self.config.password) if self.config.password else 'NONE'})")
        self._send_raw(raw)

        # Wait for Logon response (try multiple reads)
        for i in range(3):
            response = self._recv_message(timeout=10)
            if response:
                msg_type = response.get(35)
                if msg_type == "A":
                    log.info(f"[{self.connection_type}] Logon ACCEPTED.")
                    return True
                elif msg_type == "3":
                    reason = response.get(58, "Unknown reason")
                    log.error(f"[{self.connection_type}] Logon REJECTED: {reason}")
                    return False
                elif msg_type == "5":
                    reason = response.get(58, "Logout received")
                    log.error(f"[{self.connection_type}] Server sent Logout: {reason}")
                    return False
                else:
                    log.warning(f"[{self.connection_type}] Unexpected response type: {msg_type}")
                    continue

        log.error(f"[{self.connection_type}] No Logon response after 3 attempts.")
        return False

    def _send_logout(self):
        """Send Logout message (35=5)."""
        msg = FIXMessage()
        raw = msg.build(
            "5", self._next_seq(),
            self.config.sender_comp_id,
            self.config.target_comp_id,
            self.sender_sub_id,
            self.sender_sub_id,
        )
        self._send_raw(raw)
        log.info(f"[{self.connection_type}] Logout sent.")

    def send_heartbeat(self, test_req_id: str = ""):
        """Send Heartbeat message (35=0)."""
        msg = FIXMessage()
        if test_req_id:
            msg.set(112, test_req_id)  # TestReqID
        raw = msg.build(
            "0", self._next_seq(),
            self.config.sender_comp_id,
            self.config.target_comp_id,
            self.sender_sub_id,
            self.sender_sub_id,
        )
        self._send_raw(raw)

    # ──────────────────────────────────────────
    # Trading Operations (Trade Connection)
    # ──────────────────────────────────────────

    def send_new_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        client_order_id: str = "",
    ) -> str:
        """
        Send a new order (35=D).
        side: "BUY" or "SELL"
        order_type: "MARKET", "LIMIT", "STOP"
        Returns client_order_id.
        """
        if not client_order_id:
            client_order_id = f"CHAST_{int(time.time()*1000)}"

        fix_side = "1" if side.upper() == "BUY" else "2"
        fix_ord_type = {"MARKET": "1", "LIMIT": "2", "STOP": "3"}.get(order_type.upper(), "1")

        msg = FIXMessage()
        msg.set(11, client_order_id)  # ClOrdID
        msg.set(55, symbol)           # Symbol
        msg.set(54, fix_side)         # Side
        msg.set(38, quantity)         # OrderQty (in lots)
        msg.set(40, fix_ord_type)     # OrdType
        msg.set(60, FIXMessage._utc_timestamp())  # TransactTime

        if fix_ord_type == "2" and price > 0:
            msg.set(44, price)  # Price (for Limit)
        if fix_ord_type == "3" and price > 0:
            msg.set(99, price)  # StopPx (for Stop)

        # Custom tags for SL/TP (cTrader extension)
        if stop_loss > 0:
            msg.set(7539, stop_loss)  # StopLoss (cTrader custom tag)
        if take_profit > 0:
            msg.set(7540, take_profit)  # TakeProfit (cTrader custom tag)

        raw = msg.build(
            "D", self._next_seq(),
            self.config.sender_comp_id,
            self.config.target_comp_id,
            self.sender_sub_id,
            self.sender_sub_id,
        )
        self._send_raw(raw)
        log.info(f"[TRADE] New order sent: {side} {quantity} {symbol} "
                 f"({order_type}) ID={client_order_id}")
        return client_order_id

    def send_cancel_order(self, client_order_id: str, symbol: str, side: str):
        """Send order cancel request (35=F)."""
        fix_side = "1" if side.upper() == "BUY" else "2"
        msg = FIXMessage()
        msg.set(11, f"CXL_{int(time.time()*1000)}")  # New ClOrdID
        msg.set(41, client_order_id)  # OrigClOrdID
        msg.set(55, symbol)
        msg.set(54, fix_side)
        msg.set(60, FIXMessage._utc_timestamp())

        raw = msg.build(
            "F", self._next_seq(),
            self.config.sender_comp_id,
            self.config.target_comp_id,
            self.sender_sub_id,
            self.sender_sub_id,
        )
        self._send_raw(raw)
        log.info(f"[TRADE] Cancel request sent for {client_order_id}")

    # ──────────────────────────────────────────
    # Market Data (Price Connection)
    # ──────────────────────────────────────────

    def subscribe_market_data(self, symbol: str, req_id: str = "1"):
        """Subscribe to market data (35=V)."""
        msg = FIXMessage()
        msg.set(262, req_id)   # MDReqID
        msg.set(263, 1)        # SubscriptionRequestType: Snapshot + Updates
        msg.set(264, 1)        # MarketDepth: Top of Book
        msg.set(265, 1)        # MDUpdateType: Incremental
        msg.set(267, 2)        # NoMDEntryTypes: 2 (Bid + Ask)
        msg.set(269, 0)        # MDEntryType: Bid
        # Second entry type (Ask) — simplified for demo
        msg.set(146, 1)        # NoRelatedSym
        msg.set(55, symbol)    # Symbol

        raw = msg.build(
            "V", self._next_seq(),
            self.config.sender_comp_id,
            self.config.target_comp_id,
            self.sender_sub_id,
            self.sender_sub_id,
        )
        self._send_raw(raw)
        log.info(f"[PRICE] Subscribed to market data for {symbol}")

    def unsubscribe_market_data(self, symbol: str, req_id: str = "1"):
        """Unsubscribe from market data (35=V with type=2)."""
        msg = FIXMessage()
        msg.set(262, req_id)
        msg.set(263, 2)  # Unsubscribe
        msg.set(146, 1)
        msg.set(55, symbol)

        raw = msg.build(
            "V", self._next_seq(),
            self.config.sender_comp_id,
            self.config.target_comp_id,
            self.sender_sub_id,
            self.sender_sub_id,
        )
        self._send_raw(raw)
        log.info(f"[PRICE] Unsubscribed from {symbol}")

    # ──────────────────────────────────────────
    # Low-level I/O
    # ──────────────────────────────────────────

    def _send_raw(self, data: bytes):
        """Send raw bytes over socket."""
        sock = self._ssl_socket if self.config.use_ssl else self._socket
        if sock:
            sock.sendall(data)

    def _recv_message(self, timeout: float = 5.0) -> Optional[FIXMessage]:
        """Receive and parse one FIX message."""
        sock = self._ssl_socket if self.config.use_ssl else self._socket
        if not sock:
            return None

        sock.settimeout(timeout)
        try:
            data = sock.recv(4096)
            if data:
                raw = data.decode("ascii", errors="ignore")
                return FIXMessage.parse(raw)
        except socket.timeout:
            return None
        except Exception as e:
            log.error(f"[{self.connection_type}] Recv error: {e}")
            return None
        return None

    # ──────────────────────────────────────────
    # Background Threads
    # ──────────────────────────────────────────

    def _start_heartbeat(self):
        """Start heartbeat thread."""
        def _heartbeat_loop():
            while self._running and self._connected:
                time.sleep(self.config.heartbeat_interval)
                if self._running and self._connected:
                    try:
                        self.send_heartbeat()
                    except Exception as e:
                        log.error(f"[{self.connection_type}] Heartbeat failed: {e}")
                        if self.on_disconnect:
                            self.on_disconnect()
                        break

        self._heartbeat_thread = threading.Thread(
            target=_heartbeat_loop, daemon=True,
            name=f"FIX-HB-{self.connection_type}",
        )
        self._heartbeat_thread.start()

    def _start_reader(self):
        """Start message reader thread."""
        def _reader_loop():
            while self._running and self._connected:
                msg = self._recv_message(timeout=1.0)
                if msg is None:
                    continue

                msg_type = msg.get(35)
                self._recv_seq_num += 1

                # Handle different message types
                if msg_type == "0":
                    pass  # Heartbeat — ignore
                elif msg_type == "1":
                    # Test Request — respond with heartbeat
                    self.send_heartbeat(msg.get(112, ""))
                elif msg_type == "5":
                    # Logout
                    log.warning(f"[{self.connection_type}] Server sent Logout.")
                    self._connected = False
                    if self.on_disconnect:
                        self.on_disconnect()
                elif msg_type == "8":
                    # Execution Report
                    self._handle_execution_report(msg)
                elif msg_type == "W":
                    # Market Data Snapshot
                    self._handle_market_data(msg)
                elif msg_type == "X":
                    # Market Data Incremental Refresh
                    self._handle_market_data(msg)
                elif msg_type == "3":
                    # Reject
                    log.error(f"[{self.connection_type}] Message rejected: "
                              f"{msg.get(58, 'Unknown reason')}")

                # Generic callback
                if self.on_message:
                    self.on_message(msg)

        self._reader_thread = threading.Thread(
            target=_reader_loop, daemon=True,
            name=f"FIX-Reader-{self.connection_type}",
        )
        self._reader_thread.start()

    def _handle_execution_report(self, msg: FIXMessage):
        """Handle execution report (35=8)."""
        report = {
            "order_id": msg.get(37),
            "client_order_id": msg.get(11),
            "exec_type": msg.get(150),  # 0=New, F=Fill, 4=Canceled, 8=Rejected
            "order_status": msg.get(39),
            "symbol": msg.get(55),
            "side": "BUY" if msg.get(54) == "1" else "SELL",
            "quantity": msg.get(38),
            "price": msg.get(44),
            "avg_price": msg.get(6),
            "filled_qty": msg.get(14),
            "text": msg.get(58, ""),
        }

        exec_type = msg.get(150)
        if exec_type == "F":
            log.info(f"[TRADE] Order FILLED: {report['side']} {report['filled_qty']} "
                     f"{report['symbol']} @ {report['avg_price']}")
        elif exec_type == "8":
            log.error(f"[TRADE] Order REJECTED: {report['text']}")
        elif exec_type == "4":
            log.info(f"[TRADE] Order CANCELED: {report['client_order_id']}")

        if self.on_execution:
            self.on_execution(report)

    def _handle_market_data(self, msg: FIXMessage):
        """Handle market data snapshot/refresh."""
        data = {
            "symbol": msg.get(55),
            "bid": msg.get(270, ""),  # MDEntryPx (first entry = bid)
            "ask": "",
            "timestamp": msg.get(52),
        }
        if self.on_market_data:
            self.on_market_data(data)


# ──────────────────────────────────────────────
# High-Level FIX Connector
# ──────────────────────────────────────────────

class CTraderFIXConnector:
    """
    High-level wrapper managing both Price and Trade FIX connections.
    
    Usage:
        config = FIXConfig(password="...", sender_comp_id="demo.ctrader.5820056")
        connector = CTraderFIXConnector(config)
        connector.connect()
        connector.subscribe("XAUUSD")
        connector.buy("XAUUSD", 0.01, stop_loss=2340.0, take_profit=2380.0)
        connector.disconnect()
    """

    def __init__(self, config: FIXConfig):
        self.config = config
        self.price_conn = FIXConnection(
            config.price_host, config.price_port, config,
            config.price_sender_sub_id, "PRICE",
        )
        self.trade_conn = FIXConnection(
            config.trade_host, config.trade_port, config,
            config.trade_sender_sub_id, "TRADE",
        )

        # State
        self._latest_quotes: Dict[str, Dict] = {}
        self._execution_history: List[Dict] = []
        self._pending_executions: Dict[str, Optional[Dict]] = {}

        # Wire up callbacks
        self.price_conn.on_market_data = self._on_market_data
        self.trade_conn.on_execution = self._on_execution

    def connect(self) -> bool:
        """Connect both Price and Trade connections."""
        log.info("Connecting to cTrader FIX API...")
        price_ok = self.price_conn.connect()
        trade_ok = self.trade_conn.connect()

        if price_ok and trade_ok:
            log.info("✓ Both FIX connections established.")
            return True

        if not price_ok:
            log.error("Price connection failed.")
        if not trade_ok:
            log.error("Trade connection failed.")
        return False

    def disconnect(self):
        """Disconnect both connections."""
        self.price_conn.disconnect()
        self.trade_conn.disconnect()

    def subscribe(self, symbol: str):
        """Subscribe to market data for a symbol."""
        self.price_conn.subscribe_market_data(symbol)

    def unsubscribe(self, symbol: str):
        """Unsubscribe from market data."""
        self.price_conn.unsubscribe_market_data(symbol)

    def buy(self, symbol: str, volume: float, order_type: str = "MARKET",
            price: float = 0.0, stop_loss: float = 0.0, take_profit: float = 0.0) -> str:
        """Send a BUY order."""
        return self.trade_conn.send_new_order(
            symbol, "BUY", volume, order_type, price, stop_loss, take_profit
        )

    def sell(self, symbol: str, volume: float, order_type: str = "MARKET",
             price: float = 0.0, stop_loss: float = 0.0, take_profit: float = 0.0) -> str:
        """Send a SELL order."""
        return self.trade_conn.send_new_order(
            symbol, "SELL", volume, order_type, price, stop_loss, take_profit
        )

    def cancel_order(self, client_order_id: str, symbol: str, side: str):
        """Cancel a pending order."""
        self.trade_conn.send_cancel_order(client_order_id, symbol, side)

    def get_latest_quote(self, symbol: str) -> Optional[Dict]:
        """Get latest cached quote for a symbol."""
        return self._latest_quotes.get(symbol)

    @property
    def execution_history(self) -> List[Dict]:
        return self._execution_history

    def _on_market_data(self, data: Dict):
        """Callback for market data updates."""
        symbol = data.get("symbol", "")
        if symbol:
            self._latest_quotes[symbol] = data

    def _on_execution(self, report: Dict):
        """Callback for execution reports."""
        self._execution_history.append(report)
        # Store in pending dict only for terminal states (Fill, Reject, Cancel)
        # Ignore intermediate states like "0" (New/Acknowledged) to avoid
        # premature pickup by execute_order() polling loop
        exec_type = report.get("exec_type", "")
        client_id = report.get("client_order_id", "")
        if client_id and exec_type in ("F", "8", "4", "C"):
            # F=Fill, 8=Rejected, 4=Canceled, C=Expired
            self._pending_executions[client_id] = report

    async def execute_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        order_type: str = "MARKET",
        price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        timeout: float = 30.0,
    ) -> Dict:
        """
        Send order and wait for broker execution confirmation.
        Returns execution report dict with keys:
            success: bool
            order_id: str (broker's OrderID)
            execution_price: float
            filled_volume: float
            text: str (error message if rejected)
        """
        import asyncio

        client_id = f"CHAST_{uuid.uuid4().hex[:12]}"
        self._pending_executions[client_id] = None

        # Send order
        self.trade_conn.send_new_order(
            symbol, side.upper(), volume, order_type, price, stop_loss, take_profit,
            client_order_id=client_id,
        )
        log.info(f"[TRADE] Order sent: {side.upper()} {volume} {symbol} | ClientID={client_id}")

        # Wait for execution report
        start = time.time()
        while time.time() - start < timeout:
            result = self._pending_executions.get(client_id)
            if result is not None:
                del self._pending_executions[client_id]
                exec_type = result.get("exec_type", "")
                if exec_type == "F":
                    log.info(f"[TRADE] Order CONFIRMED by broker: {result['side']} {result['filled_qty']} {result['symbol']} @ {result['avg_price']} | BrokerID={result['order_id']}")
                    return {
                        "success": True,
                        "order_id": result.get("order_id", client_id),
                        "execution_price": float(result.get("avg_price") or 0),
                        "filled_volume": float(result.get("filled_qty") or 0),
                        "text": "",
                    }
                elif exec_type == "8":
                    log.error(f"[TRADE] Order REJECTED by broker: {result.get('text')}")
                    return {
                        "success": False,
                        "order_id": client_id,
                        "execution_price": 0,
                        "filled_volume": 0,
                        "text": result.get("text", "Order rejected by broker"),
                    }
                else:
                    # Other terminal states (4=Canceled, C=Expired)
                    log.warning(f"[TRADE] Order terminal state: exec_type={exec_type} | {result.get('text', '')}")
                    return {
                        "success": False,
                        "order_id": client_id,
                        "execution_price": 0,
                        "filled_volume": 0,
                        "text": result.get("text", f"Order ended with exec_type={exec_type}"),
                    }
            await asyncio.sleep(0.1)

        # Timeout — order pending but no confirmation
        log.warning(f"[TRADE] Order PENDING (no confirm in {timeout}s): {client_id}")
        return {
            "success": False,
            "order_id": client_id,
            "execution_price": 0,
            "filled_volume": 0,
            "text": f"Timeout waiting for broker confirmation ({timeout}s)",
        }

    async def wait_for_fill(self, client_order_id: str, timeout: float = 10.0) -> Dict:
        """Wait for a specific order to be filled."""
        start = time.time()
        while time.time() - start < timeout:
            result = self._pending_executions.get(client_order_id)
            if result is not None:
                del self._pending_executions[client_order_id]
                return result
            await asyncio.sleep(0.1)
        return {}
