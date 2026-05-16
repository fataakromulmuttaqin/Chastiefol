"""
Chastiefol — Crypto Risk Manager
Position sizing, drawdown protection, and portfolio risk for crypto trading.

Key Differences from Gold/Forex Risk:
- Spot trading: no leverage by default (1:1)
- Position sized in base asset units (not lots)
- 24/7 market: no session-based restrictions
- Higher volatility: dynamic position sizing based on vol percentile
- Multi-pair portfolio: correlation-based exposure limits
- Category-based risk limits (e.g. max 10% in meme coins)

Usage:
    from Risk.crypto_risk_manager import CryptoRiskManager, CryptoAccountState
    from Analysis.crypto_pair_config import get_crypto_pair_config

    risk_mgr = CryptoRiskManager(CryptoRiskConfig(initial_balance=10000))
    account = risk_mgr.account

    # Check if trading allowed
    status = risk_mgr.check_risk()

    # Calculate position size
    position = risk_mgr.calculate_position(
        symbol="BTC/USDT",
        entry_price=65000,
        stop_loss=63000,
        pair_config=get_crypto_pair_config("BTC/USDT"),
    )

    # Record trade
    risk_mgr.open_trade("BTC/USDT", "buy", position.amount, 65000, 63000, 69000)
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from enum import Enum
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("CryptoRisk")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

class RiskModel(str, Enum):
    FIXED_PERCENT = "fixed_percent"     # Risk fixed % per trade
    VOLATILITY_ADJUSTED = "volatility"  # Scale by volatility
    KELLY = "kelly"                     # Kelly criterion


@dataclass
class CryptoRiskConfig:
    """Crypto risk management configuration."""
    # Account
    initial_balance: float = 10000.0    # Starting USDT balance
    currency: str = "USDT"

    # Per-Trade Risk
    risk_pct_per_trade: float = 1.5     # % of balance to risk per trade
    max_risk_pct_per_trade: float = 3.0 # Hard cap on single trade risk
    min_trade_value: float = 10.0       # Minimum USDT trade value (Binance min)

    # Portfolio Risk
    max_total_risk_pct: float = 10.0    # Max total portfolio risk across all trades
    max_open_trades: int = 5            # Max concurrent open trades
    max_same_direction: int = 4         # Max trades in same direction
    max_per_category_pct: float = 20.0  # Max % in one category (meme, defi, etc.)
    max_single_pair_pct: float = 10.0   # Max % of portfolio in one pair

    # Drawdown Protection
    max_daily_loss_pct: float = 5.0     # Daily loss limit
    max_drawdown_pct: float = 15.0      # Max equity drawdown from peak
    warning_drawdown_pct: float = 8.0   # Warning level
    reduce_size_drawdown_pct: float = 8.0  # Halve position size at this DD
    cooldown_after_loss_streak: int = 3  # Pause after N consecutive losses
    cooldown_minutes: int = 60          # Cooldown duration

    # Volatility Adjustment
    vol_scale_enabled: bool = True      # Scale position by volatility
    high_vol_reduction: float = 0.5     # Reduce to 50% size in high vol
    low_vol_boost: float = 1.3          # Increase to 130% in low vol

    # Correlation
    correlation_threshold: float = 0.7  # Reduce exposure if correlated
    correlation_reduction: float = 0.5  # Reduce by 50% if correlated


# ──────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────

@dataclass
class CryptoAccountState:
    """Crypto trading account state."""
    balance: float = 10000.0        # Available USDT
    equity: float = 10000.0         # Balance + unrealized P&L
    peak_balance: float = 10000.0   # All-time high balance
    daily_start_balance: float = 10000.0  # Balance at start of day
    daily_pnl: float = 0.0         # Realized P&L today
    total_pnl: float = 0.0         # All-time realized P&L
    open_trades: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    consecutive_losses: int = 0
    last_trade_time: str = ""

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance <= 0:
            return 0.0
        return (self.peak_balance - self.equity) / self.peak_balance * 100

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_start_balance <= 0:
            return 0.0
        return max(0, (self.daily_start_balance - self.equity) / self.daily_start_balance * 100)

    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        return self.winning_trades / total if total > 0 else 0.0

    def update_peak(self):
        if self.equity > self.peak_balance:
            self.peak_balance = self.equity


@dataclass
class CryptoPositionSize:
    """Calculated position size for a crypto trade."""
    symbol: str = ""
    amount: float = 0.0             # Base asset amount to trade
    value_usdt: float = 0.0         # Total position value in USDT
    risk_usdt: float = 0.0          # Dollar risk on this trade
    risk_pct: float = 0.0           # % of balance risked
    stop_distance_pct: float = 0.0  # SL distance as % of entry
    vol_multiplier: float = 1.0     # Volatility adjustment applied
    category_multiplier: float = 1.0  # Category-based adjustment
    is_valid: bool = True           # Whether position passes all checks
    rejection_reason: str = ""      # Why position was rejected (if invalid)


@dataclass
class OpenCryptoTrade:
    """Tracks an open crypto trade for risk management."""
    symbol: str
    side: str                       # "buy" or "sell"
    amount: float                   # Base asset amount
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_usdt: float                # Dollar risk
    category: str = ""              # Pair category
    open_time: str = ""
    current_price: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.side == "buy":
            return (self.current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - self.current_price) / self.entry_price * 100


# ──────────────────────────────────────────────
# Crypto Risk Manager
# ──────────────────────────────────────────────

class CryptoRiskManager:
    """
    Comprehensive risk manager for crypto trading on Binance.

    Features:
    - Per-trade position sizing (fixed % or volatility-adjusted)
    - Portfolio-level risk limits
    - Category-based exposure caps
    - Drawdown protection with circuit breakers
    - Consecutive loss cooldown
    - Correlation-aware sizing
    - Dynamic daily reset

    Architecture:
    ┌───────────────────────────────────────────────┐
    │           CryptoRiskManager                    │
    ├───────────────────────────────────────────────┤
    │  Position Sizer                               │
    │    ├─ Fixed % model                           │
    │    ├─ Volatility-adjusted                     │
    │    └─ Category caps                           │
    ├───────────────────────────────────────────────┤
    │  Drawdown Guard                               │
    │    ├─ Daily loss limit                        │
    │    ├─ Max drawdown circuit breaker            │
    │    └─ Consecutive loss cooldown               │
    ├───────────────────────────────────────────────┤
    │  Portfolio Monitor                            │
    │    ├─ Open trades tracker                     │
    │    ├─ Category exposure                       │
    │    └─ Total risk aggregation                  │
    └───────────────────────────────────────────────┘
    """

    def __init__(self, config: CryptoRiskConfig = None):
        self.config = config or CryptoRiskConfig()

        self.account = CryptoAccountState(
            balance=self.config.initial_balance,
            equity=self.config.initial_balance,
            peak_balance=self.config.initial_balance,
            daily_start_balance=self.config.initial_balance,
        )

        # Open trades tracking
        self._open_trades: Dict[str, OpenCryptoTrade] = {}

        # Daily tracking
        self._daily_date: str = ""
        self._cooldown_until: float = 0.0  # timestamp

        log.info(f"CryptoRiskManager initialized | "
                 f"Balance: ${self.config.initial_balance:,.2f} | "
                 f"Risk/trade: {self.config.risk_pct_per_trade}% | "
                 f"Max DD: {self.config.max_drawdown_pct}%")

    # ──────────────────────────────────────────
    # Risk Check (pre-trade validation)
    # ──────────────────────────────────────────

    def check_risk(self) -> Dict:
        """
        Check if trading is currently allowed.
        
        Returns dict with:
        - trading_allowed: bool
        - size_multiplier: float (1.0 = normal, 0.5 = halved)
        - reason: str (if blocked)
        - warnings: List[str]
        """
        self._check_daily_reset()
        self.account.update_peak()

        warnings = []
        trading_allowed = True
        size_multiplier = 1.0
        reason = ""

        # 1. Max drawdown circuit breaker
        dd = self.account.drawdown_pct
        if dd >= self.config.max_drawdown_pct:
            trading_allowed = False
            reason = f"Max drawdown breached: {dd:.1f}% >= {self.config.max_drawdown_pct}%"

        # 2. Daily loss limit
        daily_loss = self.account.daily_loss_pct
        if daily_loss >= self.config.max_daily_loss_pct:
            trading_allowed = False
            reason = f"Daily loss limit: {daily_loss:.1f}% >= {self.config.max_daily_loss_pct}%"

        # 3. Max open trades
        if self.account.open_trades >= self.config.max_open_trades:
            trading_allowed = False
            reason = f"Max open trades ({self.config.max_open_trades}) reached"

        # 4. Consecutive loss cooldown
        if self.account.consecutive_losses >= self.config.cooldown_after_loss_streak:
            if time.time() < self._cooldown_until:
                trading_allowed = False
                remaining = int(self._cooldown_until - time.time()) // 60
                reason = f"Cooldown active ({remaining}min remaining) after {self.account.consecutive_losses} losses"
            else:
                # Cooldown expired — reset
                self.account.consecutive_losses = 0

        # 5. Drawdown warning zone — reduce size
        if dd >= self.config.reduce_size_drawdown_pct and trading_allowed:
            size_multiplier = 0.5
            warnings.append(f"Drawdown {dd:.1f}% — position size halved")

        if dd >= self.config.warning_drawdown_pct and trading_allowed:
            warnings.append(f"Approaching max drawdown: {dd:.1f}%")

        return {
            "trading_allowed": trading_allowed,
            "size_multiplier": size_multiplier,
            "reason": reason,
            "warnings": warnings,
            "drawdown_pct": round(dd, 2),
            "daily_loss_pct": round(daily_loss, 2),
            "open_trades": self.account.open_trades,
        }

    # ──────────────────────────────────────────
    # Position Sizing
    # ──────────────────────────────────────────

    def calculate_position(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        pair_config=None,
        volatility_percentile: float = 50.0,
    ) -> CryptoPositionSize:
        """
        Calculate optimal position size for a crypto trade.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT")
            entry_price: Planned entry price
            stop_loss: Stop loss price
            pair_config: CryptoPairConfig for the pair
            volatility_percentile: Current vol as percentile (0-100)

        Returns:
            CryptoPositionSize with amount and validation
        """
        result = CryptoPositionSize(symbol=symbol)

        # Validate inputs
        if entry_price <= 0 or stop_loss <= 0:
            result.is_valid = False
            result.rejection_reason = "Invalid entry/SL price"
            return result

        # Stop loss distance
        sl_distance = abs(entry_price - stop_loss)
        sl_distance_pct = sl_distance / entry_price * 100
        result.stop_distance_pct = sl_distance_pct

        if sl_distance <= 0:
            result.is_valid = False
            result.rejection_reason = "Stop loss distance is zero"
            return result

        # Base risk amount
        risk_pct = self.config.risk_pct_per_trade
        risk_usdt = self.account.balance * (risk_pct / 100)

        # Apply volatility adjustment
        vol_mult = 1.0
        if self.config.vol_scale_enabled:
            if volatility_percentile > 80:
                vol_mult = self.config.high_vol_reduction
            elif volatility_percentile < 20:
                vol_mult = self.config.low_vol_boost
            else:
                # Linear scale between 20-80 percentile
                vol_mult = 1.3 - (volatility_percentile - 20) / 60 * 0.8

        result.vol_multiplier = vol_mult
        risk_usdt *= vol_mult

        # Apply category limits
        category_mult = 1.0
        if pair_config:
            category = getattr(pair_config, 'category', None)
            if category:
                category_str = category.value if hasattr(category, 'value') else str(category)
                # Reduce size for volatile categories
                if category_str in ("meme", "small_cap"):
                    category_mult = 0.6
                elif category_str == "ai":
                    category_mult = 0.8

                # Check category exposure
                cat_exposure = self._get_category_exposure(category_str)
                if cat_exposure >= self.config.max_per_category_pct:
                    result.is_valid = False
                    result.rejection_reason = f"Category '{category_str}' exposure limit reached ({cat_exposure:.1f}%)"
                    return result

        result.category_multiplier = category_mult
        risk_usdt *= category_mult

        # Cap risk
        max_risk = self.account.balance * (self.config.max_risk_pct_per_trade / 100)
        risk_usdt = min(risk_usdt, max_risk)

        # Calculate position amount
        # amount = risk_usdt / sl_distance (in price units)
        amount = risk_usdt / sl_distance
        value_usdt = amount * entry_price

        # Check max single pair exposure
        max_value = self.account.balance * (self.config.max_single_pair_pct / 100)
        if value_usdt > max_value:
            amount = max_value / entry_price
            value_usdt = max_value
            risk_usdt = amount * sl_distance

        # Check minimum trade value
        if value_usdt < self.config.min_trade_value:
            result.is_valid = False
            result.rejection_reason = f"Position value ${value_usdt:.2f} below minimum ${self.config.min_trade_value}"
            return result

        # Apply pair-specific min/max
        if pair_config:
            min_amount = getattr(pair_config, 'min_order_amount', 0)
            max_amount = getattr(pair_config, 'max_order_amount', 9999)
            precision = getattr(pair_config, 'amount_precision', 4)

            amount = round(amount, precision)
            amount = max(min_amount, min(amount, max_amount))

        # Check total portfolio risk
        total_risk = self._get_total_open_risk() + risk_usdt
        max_total = self.account.balance * (self.config.max_total_risk_pct / 100)
        if total_risk > max_total:
            # Reduce to fit within limit
            available_risk = max_total - self._get_total_open_risk()
            if available_risk <= 0:
                result.is_valid = False
                result.rejection_reason = "Total portfolio risk limit reached"
                return result
            amount = available_risk / sl_distance
            value_usdt = amount * entry_price
            risk_usdt = available_risk

        result.amount = amount
        result.value_usdt = round(value_usdt, 2)
        result.risk_usdt = round(risk_usdt, 2)
        result.risk_pct = round(risk_usdt / self.account.balance * 100, 3)
        result.is_valid = True

        return result

    # ──────────────────────────────────────────
    # Trade Tracking
    # ──────────────────────────────────────────

    def open_trade(
        self,
        symbol: str,
        side: str,
        amount: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        category: str = "",
    ):
        """Register a new open trade for risk tracking."""
        sl_distance = abs(entry_price - stop_loss)
        risk_usdt = amount * sl_distance

        trade = OpenCryptoTrade(
            symbol=symbol,
            side=side,
            amount=amount,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_usdt=risk_usdt,
            category=category,
            open_time=datetime.now(timezone.utc).isoformat(),
            current_price=entry_price,
        )

        self._open_trades[symbol] = trade
        self.account.open_trades = len(self._open_trades)
        self.account.total_trades += 1

        log.info(f"[Risk] Trade opened: {side.upper()} {amount} {symbol} @ ${entry_price:,.2f} | "
                 f"Risk: ${risk_usdt:,.2f} ({risk_usdt/self.account.balance*100:.1f}%)")

    def close_trade(self, symbol: str, exit_price: float) -> float:
        """
        Close a trade and update account state.
        Returns realized P&L in USDT.
        """
        trade = self._open_trades.pop(symbol, None)
        if not trade:
            log.warning(f"[Risk] No open trade for {symbol}")
            return 0.0

        # Calculate P&L
        if trade.side == "buy":
            pnl = (exit_price - trade.entry_price) * trade.amount
        else:
            pnl = (trade.entry_price - exit_price) * trade.amount

        # Update account
        self.account.balance += pnl
        self.account.equity = self.account.balance + self._get_unrealized_pnl()
        self.account.daily_pnl += pnl
        self.account.total_pnl += pnl
        self.account.open_trades = len(self._open_trades)
        self.account.last_trade_time = datetime.now(timezone.utc).isoformat()
        self.account.update_peak()

        # Win/loss tracking
        if pnl > 0:
            self.account.winning_trades += 1
            self.account.consecutive_losses = 0
        else:
            self.account.losing_trades += 1
            self.account.consecutive_losses += 1
            # Activate cooldown if streak reached
            if self.account.consecutive_losses >= self.config.cooldown_after_loss_streak:
                self._cooldown_until = time.time() + (self.config.cooldown_minutes * 60)
                log.warning(f"[Risk] Cooldown activated: {self.config.cooldown_minutes}min "
                           f"after {self.account.consecutive_losses} consecutive losses")

        log.info(f"[Risk] Trade closed: {symbol} | P&L: ${pnl:+,.2f} | "
                 f"Balance: ${self.account.balance:,.2f} | "
                 f"Win rate: {self.account.win_rate*100:.0f}%")

        return pnl

    def update_trade_price(self, symbol: str, current_price: float):
        """Update current price for an open trade (for unrealized P&L)."""
        trade = self._open_trades.get(symbol)
        if trade:
            trade.current_price = current_price
            if trade.side == "buy":
                trade.unrealized_pnl = (current_price - trade.entry_price) * trade.amount
            else:
                trade.unrealized_pnl = (trade.entry_price - current_price) * trade.amount

            # Update equity
            self.account.equity = self.account.balance + self._get_unrealized_pnl()

    # ──────────────────────────────────────────
    # Trade Management Signals
    # ──────────────────────────────────────────

    def check_trade_management(self, symbol: str, current_price: float) -> Dict:
        """
        Check if an open trade needs management (SL/TP hit, breakeven move, etc.)
        
        Returns:
            Dict with action recommendations
        """
        trade = self._open_trades.get(symbol)
        if not trade:
            return {"action": "none"}

        self.update_trade_price(symbol, current_price)
        actions = []

        # Check SL hit
        if trade.side == "buy" and current_price <= trade.stop_loss:
            return {"action": "close", "reason": "Stop loss hit", "pnl": trade.unrealized_pnl}
        elif trade.side == "sell" and current_price >= trade.stop_loss:
            return {"action": "close", "reason": "Stop loss hit", "pnl": trade.unrealized_pnl}

        # Check TP hit
        if trade.side == "buy" and current_price >= trade.take_profit:
            return {"action": "close", "reason": "Take profit hit", "pnl": trade.unrealized_pnl}
        elif trade.side == "sell" and current_price <= trade.take_profit:
            return {"action": "close", "reason": "Take profit hit", "pnl": trade.unrealized_pnl}

        # Breakeven move at 1:1 R:R
        risk = abs(trade.entry_price - trade.stop_loss)
        current_profit = abs(current_price - trade.entry_price)
        if current_profit >= risk and trade.stop_loss != trade.entry_price:
            if (trade.side == "buy" and current_price > trade.entry_price) or \
               (trade.side == "sell" and current_price < trade.entry_price):
                actions.append({
                    "type": "move_sl",
                    "new_sl": trade.entry_price,
                    "reason": "Move to breakeven (1:1 R:R reached)",
                })

        return {"action": "manage", "recommendations": actions, "pnl": trade.unrealized_pnl}

    # ──────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────

    def _get_total_open_risk(self) -> float:
        """Get total dollar risk across all open trades."""
        return sum(t.risk_usdt for t in self._open_trades.values())

    def _get_unrealized_pnl(self) -> float:
        """Get total unrealized P&L across all open trades."""
        return sum(t.unrealized_pnl for t in self._open_trades.values())

    def _get_category_exposure(self, category: str) -> float:
        """Get current exposure % in a category."""
        if self.account.balance <= 0:
            return 0.0
        cat_value = sum(
            t.amount * t.current_price
            for t in self._open_trades.values()
            if t.category == category
        )
        return cat_value / self.account.balance * 100

    def _check_daily_reset(self):
        """Reset daily metrics at start of new UTC day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date = today
            self.account.daily_start_balance = self.account.balance
            self.account.daily_pnl = 0.0
            log.info(f"[Risk] Daily reset | Balance: ${self.account.balance:,.2f}")

    # ──────────────────────────────────────────
    # Status & Reporting
    # ──────────────────────────────────────────

    def get_status(self) -> Dict:
        """Get comprehensive risk status."""
        return {
            "balance": round(self.account.balance, 2),
            "equity": round(self.account.equity, 2),
            "drawdown_pct": round(self.account.drawdown_pct, 2),
            "daily_pnl": round(self.account.daily_pnl, 2),
            "daily_loss_pct": round(self.account.daily_loss_pct, 2),
            "total_pnl": round(self.account.total_pnl, 2),
            "open_trades": self.account.open_trades,
            "total_trades": self.account.total_trades,
            "win_rate": round(self.account.win_rate * 100, 1),
            "consecutive_losses": self.account.consecutive_losses,
            "total_open_risk": round(self._get_total_open_risk(), 2),
            "unrealized_pnl": round(self._get_unrealized_pnl(), 2),
            "open_positions": {
                sym: {
                    "side": t.side,
                    "amount": t.amount,
                    "entry": t.entry_price,
                    "current": t.current_price,
                    "pnl": round(t.unrealized_pnl, 2),
                    "pnl_pct": round(t.pnl_pct, 2),
                }
                for sym, t in self._open_trades.items()
            },
        }

    def get_open_trades(self) -> Dict[str, OpenCryptoTrade]:
        """Get all open trades."""
        return self._open_trades.copy()
