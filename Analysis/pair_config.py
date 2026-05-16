"""
Chastiefol — Pair Configuration
Instrument-specific parameters for multi-pair support.
Each pair has its own ATR multiplier, FVG thresholds, session rules, and contract specs.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PairConfig:
    """
    Configuration for a trading instrument.
    Encapsulates all pair-specific parameters so the core engine
    remains instrument-agnostic.
    """
    # ── Identity ──
    symbol:             str                     # e.g. "XAUUSD", "BTCUSD"
    base_asset:         str                     # e.g. "XAU", "BTC"
    quote_asset:        str                     # e.g. "USD"
    fix_symbol_id:      Optional[int] = None    # cTrader FIX symbol ID

    # ── Contract Specs ──
    contract_size:      float = 100.0           # units per 1 lot (XAU=100oz, BTC=1)
    min_lot_size:       float = 0.01            # minimum trade quantity
    max_lot_size:       float = 5.0             # maximum trade quantity
    lot_step:           float = 0.01            # lot size increment
    pip_size:           float = 0.01            # minimum price movement

    # ── Risk Parameters ──
    atr_sl_multiplier:  float = 2.0             # ATR × N for stop loss
    rr_target:          float = 2.0             # reward:risk target ratio
    min_rr:             float = 1.5             # minimum acceptable R:R

    # ── Signal Filters ──
    min_adx:            int   = 20              # ADX threshold for trending
    min_confluence:     int   = 50              # minimum confluence score (0-100)

    # ── FVG / OB Thresholds ──
    min_fvg_size:       float = 0.5             # minimum FVG size in price units
    min_fvg_pct:        float = 0.0             # minimum FVG as % of price (0 = use absolute)

    # ── Session Filter ──
    use_session_filter: bool  = True            # whether to apply session filter
    high_prob_sessions: list[str] = field(default_factory=list)  # session names

    # ── Spread / Costs ──
    typical_spread:     float = 0.30            # typical spread in price units
    commission_per_lot: float = 3.50            # USD per lot round-trip
    slippage:           float = 0.10            # typical slippage in price units

    # ── Display ──
    price_decimals:     int   = 2              # decimal places for price display

    @property
    def dollar_per_point_per_lot(self) -> float:
        """How much USD P&L per 1.0 price move per 1 standard lot."""
        return self.contract_size

    def fvg_threshold(self, current_price: float) -> float:
        """
        Get the minimum FVG size threshold.
        Uses percentage-based if min_fvg_pct > 0, otherwise absolute.
        """
        if self.min_fvg_pct > 0:
            return current_price * (self.min_fvg_pct / 100)
        return self.min_fvg_size


# ──────────────────────────────────────────────
# Pre-built Pair Configurations
# ──────────────────────────────────────────────

XAUUSD_CONFIG = PairConfig(
    symbol="XAUUSD",
    base_asset="XAU",
    quote_asset="USD",
    fix_symbol_id=None,

    # Contract: 1 lot = 100 troy oz
    contract_size=100.0,
    min_lot_size=0.01,
    max_lot_size=5.0,
    lot_step=0.01,
    pip_size=0.01,

    # Risk
    atr_sl_multiplier=2.0,
    rr_target=2.0,
    min_rr=1.5,

    # Filters
    min_adx=20,
    min_confluence=50,

    # FVG: absolute $0.50 for gold (~$2,300 price)
    min_fvg_size=0.5,
    min_fvg_pct=0.0,

    # Session: London + NY (gold is most active during these)
    use_session_filter=True,
    high_prob_sessions=["London_Session", "New_York_Session"],

    # Costs
    typical_spread=0.30,
    commission_per_lot=3.50,
    slippage=0.10,

    price_decimals=2,
)


BTCUSD_CONFIG = PairConfig(
    symbol="BTCUSD",
    base_asset="BTC",
    quote_asset="USD",
    fix_symbol_id=22395,

    # Contract: 1 lot = 1 BTC
    contract_size=1.0,
    min_lot_size=0.01,
    max_lot_size=10.0,
    lot_step=0.01,
    pip_size=0.01,

    # Risk: BTC is more volatile — wider stops
    atr_sl_multiplier=2.5,
    rr_target=2.0,
    min_rr=1.5,

    # Filters
    min_adx=20,
    min_confluence=50,

    # FVG: percentage-based (0.05% of price ≈ $50 at $100k)
    min_fvg_size=50.0,
    min_fvg_pct=0.05,

    # Session: BTC trades 24/7, but highest volume during US hours
    use_session_filter=False,
    high_prob_sessions=[],

    # Costs: crypto typically has wider spreads
    typical_spread=5.0,
    commission_per_lot=0.0,     # most crypto exchanges use taker/maker fees built into spread
    slippage=2.0,

    price_decimals=2,
)


# ──────────────────────────────────────────────
# Registry: quick lookup by symbol
# ──────────────────────────────────────────────

PAIR_CONFIGS = {
    "XAUUSD": XAUUSD_CONFIG,
    "BTCUSD": BTCUSD_CONFIG,
}


def get_pair_config(symbol: str) -> PairConfig:
    """Get configuration for a symbol. Raises KeyError if not found."""
    symbol = symbol.upper().replace("/", "")
    if symbol not in PAIR_CONFIGS:
        raise KeyError(
            f"No configuration found for '{symbol}'. "
            f"Available: {list(PAIR_CONFIGS.keys())}"
        )
    return PAIR_CONFIGS[symbol]
