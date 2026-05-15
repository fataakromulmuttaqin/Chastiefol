"""
Chastiefol — Risk Management Module
Position sizing, drawdown protection, trade lifecycle management.
Gold-specific: 1 lot XAU/USD = 100 troy oz. Pip value varies by lot size & broker.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
import math


class RiskModel(Enum):
    FIXED_PERCENT  = "fixed_percent"   # risk fixed % of balance per trade
    KELLY          = "kelly"           # fractional Kelly criterion
    VOLATILITY     = "volatility"      # size based on ATR


@dataclass
class AccountState:
    balance:         float            # current balance in USD
    equity:          float            # balance + floating P&L
    open_trades:     int   = 0
    daily_loss:      float = 0.0      # realized loss today
    peak_balance:    float = 0.0      # all-time high for DD calc

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance == 0:
            return 0.0
        return (self.peak_balance - self.equity) / self.peak_balance * 100

    def update_peak(self):
        if self.equity > self.peak_balance:
            self.peak_balance = self.equity


@dataclass
class PositionSpec:
    lot_size:         float        # standard lots
    units:            float        # actual units (lot_size * 100)
    risk_usd:         float        # dollar risk on this trade
    risk_pct:         float        # % of account risked
    margin_required:  float        # estimated margin
    max_lot:          float        # safety cap applied


@dataclass
class TradeLifecycle:
    entry_price:      float
    stop_loss:        float
    take_profit:      float
    lot_size:         float
    direction:        str           # "BUY" | "SELL"
    breakeven_moved:  bool = False
    partial_closed:   bool = False
    current_price:    float = 0.0

    @property
    def pips_profit(self) -> float:
        if self.direction == "BUY":
            return (self.current_price - self.entry_price)
        return (self.entry_price - self.current_price)

    @property
    def pnl_usd(self) -> float:
        """Approximate P&L for standard XAU/USD (1 lot = $100/pip for gold)."""
        return self.pips_profit * self.lot_size * 100

    @property
    def rr_achieved(self) -> float:
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0:
            return 0.0
        return self.pips_profit / risk


# ──────────────────────────────────────────────
# Position Sizer
# ──────────────────────────────────────────────

class PositionSizer:
    """
    Gold-specific position sizing.
    XAU/USD: 1 standard lot = 100 troy oz.
    Pip value ≈ $1 per 0.01 price move per 0.01 lot (varies by broker).
    """

    def __init__(self,
                 risk_pct:         float = 1.0,      # % balance to risk
                 max_risk_pct:     float = 2.0,       # hard cap
                 max_lots:         float = 5.0,
                 min_lots:         float = 1.0,    # cTrader FIX demo server min = 1.0 lot
                 leverage:         int   = 100,
                 pip_value_per_lot: float = 10.0,     # USD per pip per standard lot
                 model:            RiskModel = RiskModel.FIXED_PERCENT):
        self.risk_pct          = risk_pct
        self.max_risk_pct      = max_risk_pct
        self.max_lots          = max_lots
        self.min_lots          = min_lots
        self.leverage          = leverage
        self.pip_value_per_lot = pip_value_per_lot
        self.model             = model

    def calculate(self,
                  account:    AccountState,
                  entry:      float,
                  stop_loss:  float,
                  win_rate:   Optional[float] = None,    # for Kelly
                  avg_rr:     Optional[float] = None,    # for Kelly
                  atr:        Optional[float] = None,    # for volatility model
                  ) -> PositionSpec:

        sl_distance_usd = abs(entry - stop_loss)   # gold price in USD

        if self.model == RiskModel.KELLY and win_rate and avg_rr:
            risk_pct = self._kelly_fraction(win_rate, avg_rr)
        elif self.model == RiskModel.VOLATILITY and atr:
            risk_pct = self._volatility_risk(atr, entry)
        else:
            risk_pct = self.risk_pct

        # Clamp to max
        risk_pct  = min(risk_pct, self.max_risk_pct)
        risk_usd  = account.balance * (risk_pct / 100)

        # Lot size: risk_usd / (sl_distance * pip_value_per_lot)
        # For XAU/USD: 1 lot controls 100oz; $1 price move = $100 profit/loss per lot
        dollar_risk_per_lot = sl_distance_usd * 100   # $100 per $1 move per lot
        if dollar_risk_per_lot == 0:
            lot_size = self.min_lots
        else:
            lot_size = risk_usd / dollar_risk_per_lot

        # Round to 2 decimal places (broker minimum step)
        lot_size = round(lot_size, 2)
        lot_size = max(self.min_lots, min(lot_size, self.max_lots))

        margin = (entry * lot_size * 100) / self.leverage

        return PositionSpec(
            lot_size=lot_size,
            units=lot_size * 100,
            risk_usd=round(risk_usd, 2),
            risk_pct=round(risk_pct, 3),
            margin_required=round(margin, 2),
            max_lot=self.max_lots,
        )

    def _kelly_fraction(self, win_rate: float, avg_rr: float) -> float:
        """Fractional Kelly (half-Kelly for conservatism)."""
        q = 1 - win_rate
        kelly = (win_rate * avg_rr - q) / avg_rr
        kelly = max(0, kelly)
        return kelly * 50  # half-Kelly as % of balance (multiply by 0.5)

    def _volatility_risk(self, atr: float, price: float,
                         target_vol_pct: float = 1.0) -> float:
        """Risk scaled inversely to current volatility."""
        vol_ratio = atr / price
        base_risk = self.risk_pct
        # Lower risk when volatility is high
        adjusted  = base_risk * (0.01 / max(vol_ratio, 0.001))
        return min(adjusted, self.max_risk_pct)


# ──────────────────────────────────────────────
# Drawdown Guard
# ──────────────────────────────────────────────

class DrawdownGuard:
    """
    Protects account from catastrophic drawdown.
    Applies tiered restrictions and circuit breakers.
    """

    def __init__(self,
                 max_daily_loss_pct:    float = 3.0,    # daily loss limit %
                 max_drawdown_pct:      float = 10.0,   # max equity drawdown %
                 warning_drawdown_pct:  float = 5.0,    # warn at this level
                 reduce_size_at_pct:    float = 5.0,    # halve size at this DD
                 max_open_trades:       int   = 3,
                 max_correlated_trades: int   = 1):     # gold is one pair
        self.max_daily_loss_pct    = max_daily_loss_pct
        self.max_drawdown_pct      = max_drawdown_pct
        self.warning_drawdown_pct  = warning_drawdown_pct
        self.reduce_size_at_pct    = reduce_size_at_pct
        self.max_open_trades       = max_open_trades
        self.max_correlated_trades = max_correlated_trades

    def check(self, account: AccountState) -> dict:
        """
        Returns status dict with:
          - trading_allowed: bool
          - size_multiplier: float (1.0 = normal, 0.5 = halved)
          - warnings: list of strings
          - reason: str (if trading blocked)
        """
        account.update_peak()
        warnings = []
        trading_allowed  = True
        size_multiplier  = 1.0
        reason           = ""

        daily_loss_pct = (account.daily_loss / account.peak_balance * 100
                          if account.peak_balance else 0)
        dd_pct = account.drawdown_pct

        # Daily loss breach
        if daily_loss_pct >= self.max_daily_loss_pct:
            trading_allowed = False
            reason = f"Daily loss limit reached: {daily_loss_pct:.2f}% ≥ {self.max_daily_loss_pct}%"

        # Max drawdown breach (circuit breaker)
        if dd_pct >= self.max_drawdown_pct:
            trading_allowed = False
            reason = f"Max drawdown breached: {dd_pct:.2f}% ≥ {self.max_drawdown_pct}%"

        # Warning zone: reduce size
        if dd_pct >= self.reduce_size_at_pct and trading_allowed:
            size_multiplier = 0.5
            warnings.append(f"Drawdown {dd_pct:.2f}% — position size halved")

        if dd_pct >= self.warning_drawdown_pct and trading_allowed:
            warnings.append(f"⚠ Approaching max drawdown: {dd_pct:.2f}%")

        # Too many open trades
        if account.open_trades >= self.max_open_trades:
            trading_allowed = False
            reason = f"Max open trades ({self.max_open_trades}) reached"

        return {
            "trading_allowed": trading_allowed,
            "size_multiplier": size_multiplier,
            "drawdown_pct":    round(dd_pct, 2),
            "daily_loss_pct":  round(daily_loss_pct, 2),
            "warnings":        warnings,
            "reason":          reason,
        }


# ──────────────────────────────────────────────
# Trade Manager (Trailing Stop, BE, Partial TP)
# ──────────────────────────────────────────────

class TradeManager:
    """
    Dynamic trade management after entry:
    - Breakeven move (move SL to entry when 1:1 reached)
    - Trailing stop (ATR-based or percentage)
    - Partial take profit (close 50% at 1:1, let rest run)
    """

    def __init__(self,
                 be_trigger_rr:      float = 1.0,    # move BE at 1:1 R:R
                 trail_atr_mult:     float = 1.5,
                 partial_tp_rr:      float = 1.0,    # partial close at 1:1
                 partial_tp_pct:     float = 0.5):   # close 50% at partial
        self.be_trigger_rr  = be_trigger_rr
        self.trail_atr_mult = trail_atr_mult
        self.partial_tp_rr  = partial_tp_rr
        self.partial_tp_pct = partial_tp_pct

    def manage(self, trade: TradeLifecycle,
               current_price: float,
               current_atr: Optional[float] = None
               ) -> dict:
        """
        Returns management actions dict:
          - update_sl: float | None
          - close_partial: bool
          - close_all: bool
          - action_descriptions: list[str]
        """
        trade.current_price = current_price
        actions = []
        new_sl  = None
        close_partial = False
        close_all     = False
        rr = trade.rr_achieved

        # Check TP/SL hit
        if trade.direction == "BUY":
            if current_price >= trade.take_profit:
                close_all = True
                actions.append("Full TP hit — closing trade")
            elif current_price <= trade.stop_loss:
                close_all = True
                actions.append("SL hit — closing trade")
        else:
            if current_price <= trade.take_profit:
                close_all = True
                actions.append("Full TP hit — closing trade")
            elif current_price >= trade.stop_loss:
                close_all = True
                actions.append("SL hit — closing trade")

        if close_all:
            return {"update_sl": None, "close_partial": False,
                    "close_all": True, "action_descriptions": actions}

        # Move to breakeven
        if not trade.breakeven_moved and rr >= self.be_trigger_rr:
            new_sl = trade.entry_price
            trade.breakeven_moved = True
            actions.append(f"Breakeven move: SL → {new_sl:.2f} (entry)")

        # Partial close
        if not trade.partial_closed and rr >= self.partial_tp_rr:
            close_partial = True
            trade.partial_closed = True
            actions.append(
                f"Partial close {self.partial_tp_pct*100:.0f}% at "
                f"R:R {rr:.2f} — locking profit"
            )

        # Trailing stop (ATR-based)
        if trade.breakeven_moved and current_atr:
            trail_distance = current_atr * self.trail_atr_mult
            if trade.direction == "BUY":
                trail_sl = current_price - trail_distance
                if trail_sl > trade.stop_loss:
                    new_sl = trail_sl
                    actions.append(f"Trail SL → {new_sl:.2f}")
            else:
                trail_sl = current_price + trail_distance
                if trail_sl < trade.stop_loss:
                    new_sl = trail_sl
                    actions.append(f"Trail SL → {new_sl:.2f}")

        if new_sl:
            trade.stop_loss = new_sl

        return {
            "update_sl":           new_sl,
            "close_partial":       close_partial,
            "close_all":           close_all,
            "action_descriptions": actions,
        }


# ──────────────────────────────────────────────
# Session Filter (XAUUSD-specific)
# ──────────────────────────────────────────────

class GoldSessionFilter:
    """
    Gold is most liquid and trendy during London and New York sessions.
    Kill zones (ICT concept): highest probability trade windows.
    All times in UTC.
    """

    SESSIONS = {
        "London_Open_Killzone":   (7, 0,  9, 0),    # 07:00-09:00 UTC
        "New_York_Open_Killzone": (12, 0, 14, 0),   # 12:00-14:00 UTC
        "London_Close":           (15, 0, 17, 0),   # 15:00-17:00 UTC
        "Asian_Range":            (0, 0,  5, 0),    # 00:00-05:00 UTC (low vol)
    }

    HIGH_PROBABILITY = ["London_Open_Killzone", "New_York_Open_Killzone"]

    def is_active(self, utc_hour: int, utc_minute: int = 0,
                  high_prob_only: bool = True) -> tuple[bool, str]:
        """Returns (is_active, session_name)."""
        time_decimal = utc_hour + utc_minute / 60
        sessions     = self.HIGH_PROBABILITY if high_prob_only else list(self.SESSIONS.keys())

        for name in sessions:
            sh, sm, eh, em = self.SESSIONS[name]
            start = sh + sm / 60
            end   = eh + em / 60
            if start <= time_decimal < end:
                return True, name

        return False, "Off-session"


# ──────────────────────────────────────────────
# Demo
# ──────────────────────────────────────────────

if __name__ == "__main__":
    account = AccountState(
        balance=10_000,
        equity=10_000,
        peak_balance=10_000,
        open_trades=0,
        daily_loss=0,
    )

    sizer = PositionSizer(risk_pct=1.0, model=RiskModel.FIXED_PERCENT)
    guard = DrawdownGuard(max_daily_loss_pct=3.0, max_drawdown_pct=10.0)
    mgr   = TradeManager()
    sess  = GoldSessionFilter()

    # Check if trading is allowed
    status = guard.check(account)
    print(f"\n=== RISK CHECK ===")
    print(f"  Trading allowed:  {status['trading_allowed']}")
    print(f"  Drawdown:         {status['drawdown_pct']}%")
    print(f"  Size multiplier:  {status['size_multiplier']}")

    # Calculate position size
    entry, sl = 2050.00, 2035.00
    spec = sizer.calculate(account, entry=entry, stop_loss=sl)
    lot  = round(spec.lot_size * status["size_multiplier"], 2)

    print(f"\n=== POSITION SIZE ===")
    print(f"  Lot size:         {lot} lots")
    print(f"  Risk USD:         ${spec.risk_usd}")
    print(f"  Risk %:           {spec.risk_pct}%")
    print(f"  Margin required:  ${spec.margin_required}")

    # Session check
    active, sess_name = sess.is_active(utc_hour=8, utc_minute=30)
    print(f"\n=== SESSION ===")
    print(f"  Session active:   {active}")
    print(f"  Session name:     {sess_name}")
