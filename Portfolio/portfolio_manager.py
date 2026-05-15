"""
Chastiefol — Portfolio Manager (Multi-Pair)
Manages trading across multiple currency pairs with:
- Per-pair configuration (risk, strategy, session)
- Portfolio-level risk aggregation
- Correlation-aware position sizing
- Exposure limits per currency/commodity
- Unified trade logging and P&L tracking

Supported Pairs (expandable):
- XAUUSD (Gold/USD) — Primary
- XAGUSD (Silver/USD)
- EURUSD, GBPUSD, USDJPY
- US30, NAS100 (Indices)
- Custom pairs via config
"""

import logging
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Any
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Portfolio")


# ──────────────────────────────────────────────
# Configuration & Models
# ──────────────────────────────────────────────

class PairCategory(str, Enum):
    METALS = "metals"
    FOREX_MAJOR = "forex_major"
    FOREX_MINOR = "forex_minor"
    INDICES = "indices"
    CRYPTO = "crypto"
    COMMODITIES = "commodities"


class PairStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


@dataclass
class PairConfig:
    """Configuration for a single trading pair."""
    symbol: str
    category: PairCategory = PairCategory.METALS
    enabled: bool = True
    status: PairStatus = PairStatus.ACTIVE

    # Risk
    risk_pct: float = 1.0           # Risk % per trade for this pair
    max_risk_pct: float = 2.0       # Max risk cap
    max_open_trades: int = 2        # Max simultaneous trades for this pair
    max_exposure_pct: float = 5.0   # Max portfolio exposure to this pair

    # Strategy
    min_confidence: float = 0.55
    timeframe: str = "H1"
    atr_sl_mult: float = 1.5
    rr_target: float = 2.0

    # Session filter
    session_london: bool = True
    session_ny: bool = True
    session_asia: bool = False

    # Pair-specific
    pip_value: float = 0.01         # XAUUSD=0.01, EURUSD=0.0001
    contract_size: float = 100.0    # Standard lot size
    spread_avg: float = 0.30        # Average spread in price units
    commission: float = 0.0         # Commission per lot

    # Data feed symbol mapping (may differ from broker symbol)
    feed_symbol: str = ""           # e.g. "XAU/USD" for TwelveData

    def __post_init__(self):
        if not self.feed_symbol:
            self.feed_symbol = self.symbol


@dataclass
class PairState:
    """Runtime state for a single pair."""
    symbol: str
    open_trades: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    current_exposure: float = 0.0   # Current exposure in USD
    last_signal_time: str = ""
    last_price: float = 0.0
    is_active: bool = True

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades * 100

    @property
    def avg_pnl(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.pnl / self.total_trades


@dataclass
class PortfolioConfig:
    """Portfolio-level configuration."""
    # Pairs to trade
    pairs: List[PairConfig] = field(default_factory=list)

    # Portfolio-level risk limits
    max_total_risk_pct: float = 5.0         # Max combined risk across all pairs
    max_total_exposure_pct: float = 20.0    # Max total portfolio exposure
    max_correlated_pairs: int = 3           # Max pairs with correlation > 0.7
    max_drawdown_pct: float = 10.0          # Portfolio-level circuit breaker

    # Correlation
    correlation_threshold: float = 0.7      # Pairs above this = "correlated"
    reduce_size_correlated: float = 0.5     # Reduce size by 50% for correlated pairs

    # Capital allocation
    allocation_method: str = "equal"        # "equal", "risk_parity", "custom"
    initial_balance: float = 10000.0

    @classmethod
    def default_gold_focused(cls) -> "PortfolioConfig":
        """Default config focused on gold with supporting pairs."""
        return cls(
            pairs=[
                PairConfig(
                    symbol="XAUUSD",
                    category=PairCategory.METALS,
                    risk_pct=1.0,
                    max_open_trades=2,
                    pip_value=0.01,
                    contract_size=100,
                    spread_avg=0.30,
                    feed_symbol="XAU/USD",
                ),
                PairConfig(
                    symbol="XAGUSD",
                    category=PairCategory.METALS,
                    risk_pct=0.75,
                    max_open_trades=1,
                    pip_value=0.001,
                    contract_size=5000,
                    spread_avg=0.02,
                    feed_symbol="XAG/USD",
                ),
                PairConfig(
                    symbol="EURUSD",
                    category=PairCategory.FOREX_MAJOR,
                    risk_pct=0.5,
                    max_open_trades=1,
                    pip_value=0.0001,
                    contract_size=100000,
                    spread_avg=0.00012,
                    feed_symbol="EUR/USD",
                ),
                PairConfig(
                    symbol="USDJPY",
                    category=PairCategory.FOREX_MAJOR,
                    risk_pct=0.5,
                    max_open_trades=1,
                    pip_value=0.01,
                    contract_size=100000,
                    spread_avg=0.012,
                    feed_symbol="USD/JPY",
                ),
                PairConfig(
                    symbol="GBPUSD",
                    category=PairCategory.FOREX_MAJOR,
                    risk_pct=0.5,
                    max_open_trades=1,
                    pip_value=0.0001,
                    contract_size=100000,
                    spread_avg=0.00015,
                    feed_symbol="GBP/USD",
                ),
            ],
            max_total_risk_pct=5.0,
            max_total_exposure_pct=20.0,
            max_drawdown_pct=10.0,
            initial_balance=10000.0,
        )


@dataclass
class PortfolioState:
    """Overall portfolio runtime state."""
    balance: float = 10000.0
    equity: float = 10000.0
    total_exposure: float = 0.0
    total_risk_pct: float = 0.0
    open_positions: int = 0
    total_trades: int = 0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    peak_balance: float = 10000.0
    max_drawdown_pct: float = 0.0
    pair_states: Dict[str, PairState] = field(default_factory=dict)
    is_trading_allowed: bool = True
    circuit_breaker_reason: str = ""

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance == 0:
            return 0.0
        return (self.peak_balance - self.equity) / self.peak_balance * 100

    def update_peak(self):
        if self.equity > self.peak_balance:
            self.peak_balance = self.equity


@dataclass
class CorrelationMatrix:
    """Pair correlation tracking."""
    matrix: Dict[str, Dict[str, float]] = field(default_factory=dict)
    last_updated: str = ""

    def get_correlation(self, pair_a: str, pair_b: str) -> float:
        """Get correlation between two pairs."""
        if pair_a in self.matrix and pair_b in self.matrix[pair_a]:
            return self.matrix[pair_a][pair_b]
        return 0.0

    def is_correlated(self, pair_a: str, pair_b: str, threshold: float = 0.7) -> bool:
        """Check if two pairs are significantly correlated."""
        return abs(self.get_correlation(pair_a, pair_b)) >= threshold


# ──────────────────────────────────────────────
# Portfolio Manager
# ──────────────────────────────────────────────

class PortfolioManager:
    """
    Multi-pair portfolio manager with correlation-aware risk management.

    Responsibilities:
    - Track state per pair and portfolio-wide
    - Enforce portfolio-level risk limits
    - Adjust position sizes based on correlation
    - Route signals to correct pair handler
    - Aggregate P&L and generate reports

    Usage:
        config = PortfolioConfig.default_gold_focused()
        pm = PortfolioManager(config)

        # Check if trade is allowed
        allowed, reason = pm.can_open_trade("XAUUSD", risk_usd=100)

        # Register a new trade
        pm.open_trade("XAUUSD", direction="BUY", lot_size=0.05, entry=2365.0, sl=2350.0)

        # Close a trade
        pm.close_trade("XAUUSD", pnl=75.50)

        # Get portfolio status
        status = pm.get_status()
    """

    def __init__(self, config: PortfolioConfig = None):
        self.config = config or PortfolioConfig.default_gold_focused()
        self.state = PortfolioState(
            balance=self.config.initial_balance,
            equity=self.config.initial_balance,
            peak_balance=self.config.initial_balance,
        )
        self.correlation = CorrelationMatrix()

        # Initialize pair states
        for pair_cfg in self.config.pairs:
            self.state.pair_states[pair_cfg.symbol] = PairState(symbol=pair_cfg.symbol)

        log.info(f"Portfolio Manager initialized | "
                 f"Pairs: {[p.symbol for p in self.config.pairs]} | "
                 f"Max risk: {self.config.max_total_risk_pct}% | "
                 f"Balance: ${self.config.initial_balance:,.2f}")

    # ──────────────────────────────────────────
    # Trade Gating
    # ──────────────────────────────────────────

    def can_open_trade(self, symbol: str, risk_usd: float = 0) -> tuple:
        """
        Check if a new trade is allowed for the given pair.
        Returns (allowed: bool, reason: str)
        """
        # Check circuit breaker
        if not self.state.is_trading_allowed:
            return False, f"Circuit breaker active: {self.state.circuit_breaker_reason}"

        # Check if pair exists and is enabled
        pair_cfg = self._get_pair_config(symbol)
        if not pair_cfg:
            return False, f"Pair {symbol} not configured in portfolio"
        if not pair_cfg.enabled or pair_cfg.status != PairStatus.ACTIVE:
            return False, f"Pair {symbol} is {pair_cfg.status.value}"

        pair_state = self.state.pair_states.get(symbol)
        if not pair_state:
            return False, f"No state for {symbol}"

        # Check per-pair max open trades
        if pair_state.open_trades >= pair_cfg.max_open_trades:
            return False, f"{symbol}: max open trades ({pair_cfg.max_open_trades}) reached"

        # Check portfolio-level drawdown
        dd = self.state.drawdown_pct
        if dd >= self.config.max_drawdown_pct:
            self.state.is_trading_allowed = False
            self.state.circuit_breaker_reason = f"Max drawdown {dd:.1f}% >= {self.config.max_drawdown_pct}%"
            return False, self.state.circuit_breaker_reason

        # Check total risk
        risk_pct = (risk_usd / self.state.equity * 100) if self.state.equity > 0 else 100
        projected_total_risk = self.state.total_risk_pct + risk_pct
        if projected_total_risk > self.config.max_total_risk_pct:
            return False, (f"Total risk would exceed limit: "
                           f"{projected_total_risk:.1f}% > {self.config.max_total_risk_pct}%")

        # Check total exposure
        if self.state.total_exposure / max(self.state.equity, 1) * 100 > self.config.max_total_exposure_pct:
            return False, "Max portfolio exposure exceeded"

        # Check correlation limits
        correlated_open = self._count_correlated_open(symbol)
        if correlated_open >= self.config.max_correlated_pairs:
            return False, (f"Too many correlated pairs open ({correlated_open} >= "
                           f"{self.config.max_correlated_pairs})")

        return True, "OK"

    def get_adjusted_lot_size(self, symbol: str, base_lot: float) -> float:
        """
        Adjust lot size based on correlation with other open positions.
        If the pair is highly correlated with existing positions, reduce size.
        """
        correlated_count = self._count_correlated_open(symbol)
        if correlated_count > 0:
            reduction = self.config.reduce_size_correlated ** correlated_count
            adjusted = base_lot * reduction
            log.info(f"[{symbol}] Lot adjusted for correlation: "
                     f"{base_lot:.3f} → {adjusted:.3f} ({correlated_count} correlated)")
            return round(adjusted, 3)
        return base_lot

    # ──────────────────────────────────────────
    # Trade Lifecycle
    # ──────────────────────────────────────────

    def open_trade(self, symbol: str, direction: str, lot_size: float,
                   entry: float, sl: float, tp: float = 0,
                   risk_usd: float = 0) -> bool:
        """Register a new trade opening."""
        pair_state = self.state.pair_states.get(symbol)
        if not pair_state:
            log.error(f"Cannot open trade: {symbol} not in portfolio")
            return False

        pair_state.open_trades += 1
        pair_state.last_signal_time = datetime.now(timezone.utc).isoformat()
        pair_state.last_price = entry

        # Update portfolio exposure
        pair_cfg = self._get_pair_config(symbol)
        if pair_cfg:
            exposure = lot_size * pair_cfg.contract_size * entry
            pair_state.current_exposure += exposure
            self.state.total_exposure += exposure

        # Update risk tracking
        if risk_usd > 0 and self.state.equity > 0:
            self.state.total_risk_pct += (risk_usd / self.state.equity * 100)

        self.state.open_positions += 1

        log.info(f"[Portfolio] Opened {direction} {symbol} | "
                 f"Lot: {lot_size} | Entry: {entry:.2f} | "
                 f"Open positions: {self.state.open_positions}")
        return True

    def close_trade(self, symbol: str, pnl: float, lot_size: float = 0,
                    entry: float = 0) -> bool:
        """Register a trade closing."""
        pair_state = self.state.pair_states.get(symbol)
        if not pair_state:
            log.error(f"Cannot close trade: {symbol} not in portfolio")
            return False

        # Update pair state
        pair_state.open_trades = max(0, pair_state.open_trades - 1)
        pair_state.total_trades += 1
        pair_state.pnl += pnl
        if pnl >= 0:
            pair_state.wins += 1
        else:
            pair_state.losses += 1

        # Update portfolio state
        self.state.balance += pnl
        self.state.equity = self.state.balance
        self.state.total_pnl += pnl
        self.state.daily_pnl += pnl
        self.state.total_trades += 1
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.update_peak()

        # Reduce exposure
        pair_cfg = self._get_pair_config(symbol)
        if pair_cfg and lot_size > 0 and entry > 0:
            exposure = lot_size * pair_cfg.contract_size * entry
            pair_state.current_exposure = max(0, pair_state.current_exposure - exposure)
            self.state.total_exposure = max(0, self.state.total_exposure - exposure)

        # Recalculate risk
        self._recalculate_risk()

        outcome = "WIN" if pnl >= 0 else "LOSS"
        log.info(f"[Portfolio] Closed {symbol} | {outcome} | P&L: ${pnl:+.2f} | "
                 f"Balance: ${self.state.balance:,.2f}")
        return True

    # ──────────────────────────────────────────
    # Correlation Management
    # ──────────────────────────────────────────

    def update_correlation(self, price_data: Dict[str, pd.Series]):
        """
        Update correlation matrix from price series.
        
        Args:
            price_data: Dict of symbol → pd.Series of close prices
        """
        symbols = list(price_data.keys())
        if len(symbols) < 2:
            return

        # Build returns DataFrame
        returns_df = pd.DataFrame()
        for sym, prices in price_data.items():
            if len(prices) > 20:
                returns_df[sym] = prices.pct_change().dropna()

        if returns_df.empty or len(returns_df.columns) < 2:
            return

        # Calculate correlation matrix
        corr = returns_df.corr()
        self.correlation.matrix = {}
        for sym_a in corr.columns:
            self.correlation.matrix[sym_a] = {}
            for sym_b in corr.columns:
                self.correlation.matrix[sym_a][sym_b] = float(corr.loc[sym_a, sym_b])

        self.correlation.last_updated = datetime.now(timezone.utc).isoformat()
        log.info(f"[Portfolio] Correlation matrix updated for {len(symbols)} pairs")

    def get_correlated_pairs(self, symbol: str) -> List[tuple]:
        """Get pairs correlated with the given symbol above threshold."""
        threshold = self.config.correlation_threshold
        correlated = []
        if symbol in self.correlation.matrix:
            for other, corr_val in self.correlation.matrix[symbol].items():
                if other != symbol and abs(corr_val) >= threshold:
                    correlated.append((other, corr_val))
        return sorted(correlated, key=lambda x: abs(x[1]), reverse=True)

    def _count_correlated_open(self, symbol: str) -> int:
        """Count how many correlated pairs have open positions."""
        count = 0
        correlated = self.get_correlated_pairs(symbol)
        for other_sym, _ in correlated:
            pair_state = self.state.pair_states.get(other_sym)
            if pair_state and pair_state.open_trades > 0:
                count += 1
        return count

    # ──────────────────────────────────────────
    # Portfolio Queries
    # ──────────────────────────────────────────

    def get_active_pairs(self) -> List[PairConfig]:
        """Get all enabled and active pair configurations."""
        return [p for p in self.config.pairs
                if p.enabled and p.status == PairStatus.ACTIVE]

    def get_pair_state(self, symbol: str) -> Optional[PairState]:
        """Get state for a specific pair."""
        return self.state.pair_states.get(symbol)

    def get_status(self) -> dict:
        """Get comprehensive portfolio status."""
        return {
            "balance": round(self.state.balance, 2),
            "equity": round(self.state.equity, 2),
            "total_pnl": round(self.state.total_pnl, 2),
            "daily_pnl": round(self.state.daily_pnl, 2),
            "drawdown_pct": round(self.state.drawdown_pct, 2),
            "open_positions": self.state.open_positions,
            "total_trades": self.state.total_trades,
            "total_risk_pct": round(self.state.total_risk_pct, 2),
            "total_exposure_pct": round(
                self.state.total_exposure / max(self.state.equity, 1) * 100, 2),
            "trading_allowed": self.state.is_trading_allowed,
            "pairs": {
                sym: {
                    "open_trades": ps.open_trades,
                    "total_trades": ps.total_trades,
                    "pnl": round(ps.pnl, 2),
                    "win_rate": round(ps.win_rate, 1),
                    "exposure": round(ps.current_exposure, 2),
                }
                for sym, ps in self.state.pair_states.items()
            },
            "correlation_updated": self.correlation.last_updated,
        }

    def get_allocation(self) -> Dict[str, float]:
        """
        Get capital allocation per pair based on allocation method.
        Returns dict of symbol → allocated capital (USD).
        """
        active_pairs = self.get_active_pairs()
        if not active_pairs:
            return {}

        total_capital = self.state.equity

        if self.config.allocation_method == "equal":
            per_pair = total_capital / len(active_pairs)
            return {p.symbol: per_pair for p in active_pairs}

        elif self.config.allocation_method == "risk_parity":
            # Allocate inversely proportional to risk (higher risk = less capital)
            total_inv_risk = sum(1.0 / max(p.risk_pct, 0.1) for p in active_pairs)
            return {
                p.symbol: total_capital * (1.0 / max(p.risk_pct, 0.1)) / total_inv_risk
                for p in active_pairs
            }

        else:
            # Custom: equal by default
            per_pair = total_capital / len(active_pairs)
            return {p.symbol: per_pair for p in active_pairs}

    # ──────────────────────────────────────────
    # Daily Reset & Reporting
    # ──────────────────────────────────────────

    def reset_daily(self):
        """Reset daily counters (call at start of new trading day)."""
        self.state.daily_pnl = 0.0
        # Optionally re-enable circuit breaker
        if self.state.drawdown_pct < self.config.max_drawdown_pct * 0.8:
            self.state.is_trading_allowed = True
            self.state.circuit_breaker_reason = ""
        log.info("[Portfolio] Daily counters reset.")

    def get_summary(self) -> dict:
        """Get end-of-day summary for reporting."""
        total_wins = sum(ps.wins for ps in self.state.pair_states.values())
        total_losses = sum(ps.losses for ps in self.state.pair_states.values())
        total = total_wins + total_losses
        win_rate = (total_wins / total * 100) if total > 0 else 0

        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "balance": round(self.state.balance, 2),
            "equity": round(self.state.equity, 2),
            "daily_pnl": round(self.state.daily_pnl, 2),
            "total_pnl": round(self.state.total_pnl, 2),
            "total_trades": self.state.total_trades,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": round(win_rate, 1),
            "max_drawdown_pct": round(self.state.max_drawdown_pct, 2),
            "current_drawdown_pct": round(self.state.drawdown_pct, 2),
            "pairs_performance": {
                sym: {
                    "trades": ps.total_trades,
                    "pnl": round(ps.pnl, 2),
                    "win_rate": round(ps.win_rate, 1),
                }
                for sym, ps in self.state.pair_states.items()
                if ps.total_trades > 0
            },
        }

    # ──────────────────────────────────────────
    # Pair Management
    # ──────────────────────────────────────────

    def add_pair(self, pair_config: PairConfig):
        """Add a new pair to the portfolio at runtime."""
        self.config.pairs.append(pair_config)
        self.state.pair_states[pair_config.symbol] = PairState(symbol=pair_config.symbol)
        log.info(f"[Portfolio] Added pair: {pair_config.symbol} ({pair_config.category.value})")

    def remove_pair(self, symbol: str):
        """Remove a pair from the portfolio (must have no open trades)."""
        pair_state = self.state.pair_states.get(symbol)
        if pair_state and pair_state.open_trades > 0:
            log.error(f"Cannot remove {symbol}: {pair_state.open_trades} open trades")
            return
        self.config.pairs = [p for p in self.config.pairs if p.symbol != symbol]
        if symbol in self.state.pair_states:
            del self.state.pair_states[symbol]
        log.info(f"[Portfolio] Removed pair: {symbol}")

    def pause_pair(self, symbol: str):
        """Pause trading for a specific pair."""
        pair_cfg = self._get_pair_config(symbol)
        if pair_cfg:
            pair_cfg.status = PairStatus.PAUSED
            log.info(f"[Portfolio] Paused: {symbol}")

    def resume_pair(self, symbol: str):
        """Resume trading for a paused pair."""
        pair_cfg = self._get_pair_config(symbol)
        if pair_cfg:
            pair_cfg.status = PairStatus.ACTIVE
            log.info(f"[Portfolio] Resumed: {symbol}")

    # ──────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────

    def _get_pair_config(self, symbol: str) -> Optional[PairConfig]:
        """Get configuration for a symbol."""
        for p in self.config.pairs:
            if p.symbol == symbol:
                return p
        return None

    def _recalculate_risk(self):
        """Recalculate total portfolio risk from open positions."""
        # Simplified: sum of per-pair risk
        total_risk = 0.0
        for pair_cfg in self.config.pairs:
            pair_state = self.state.pair_states.get(pair_cfg.symbol)
            if pair_state and pair_state.open_trades > 0:
                total_risk += pair_cfg.risk_pct * pair_state.open_trades
        self.state.total_risk_pct = total_risk

        # Update max drawdown tracking
        current_dd = self.state.drawdown_pct
        if current_dd > self.state.max_drawdown_pct:
            self.state.max_drawdown_pct = current_dd


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Create portfolio with default gold-focused config
    config = PortfolioConfig.default_gold_focused()
    pm = PortfolioManager(config)

    print(f"\n{'='*55}")
    print(f"  PORTFOLIO MANAGER TEST")
    print(f"{'='*55}")

    # Show active pairs
    print(f"\n  Active pairs: {[p.symbol for p in pm.get_active_pairs()]}")
    print(f"  Allocation: {pm.get_allocation()}")

    # Simulate trades
    allowed, reason = pm.can_open_trade("XAUUSD", risk_usd=100)
    print(f"\n  Can trade XAUUSD? {allowed} — {reason}")

    pm.open_trade("XAUUSD", "BUY", 0.05, entry=2365.0, sl=2350.0, risk_usd=100)
    pm.open_trade("XAGUSD", "BUY", 0.10, entry=29.50, sl=29.00, risk_usd=50)

    print(f"  Open positions: {pm.state.open_positions}")
    print(f"  Total risk: {pm.state.total_risk_pct:.1f}%")

    # Close with profit
    pm.close_trade("XAUUSD", pnl=150.0, lot_size=0.05, entry=2365.0)
    pm.close_trade("XAGUSD", pnl=-30.0, lot_size=0.10, entry=29.50)

    # Status
    status = pm.get_status()
    print(f"\n  Final Status:")
    print(f"    Balance: ${status['balance']:,.2f}")
    print(f"    P&L: ${status['total_pnl']:+.2f}")
    print(f"    Trades: {status['total_trades']}")
    print(f"    Drawdown: {status['drawdown_pct']:.2f}%")

    # Summary
    summary = pm.get_summary()
    print(f"\n  Summary: {summary}")
    print(f"{'='*55}\n")
