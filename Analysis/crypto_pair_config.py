"""
Chastiefol — Crypto Pair Configuration
All tradeable Binance spot pairs with instrument-specific parameters.

Categories:
- LARGE_CAP: BTC, ETH, BNB, SOL, XRP (high liquidity, tighter spreads)
- MID_CAP: ADA, AVAX, DOT, LINK, UNI, etc. (moderate volatility)
- SMALL_CAP: Newer/smaller coins (higher volatility, wider spreads)
- MEME: DOGE, SHIB, PEPE, WIF (extreme volatility, speculative)
- DEFI: AAVE, MKR, UNI, CRV, LDO (DeFi governance tokens)
- AI: FET, RENDER, AGIX (AI narrative tokens)
- LAYER2: ARB, OP, MATIC, IMX (L2 scaling solutions)

Each config defines:
- Contract specs (min/max order, precision)
- Risk parameters (ATR multiplier, R:R target)
- Signal filters (ADX threshold, confluence minimum)
- FVG thresholds (percentage-based for crypto)

Usage:
    from Analysis.crypto_pair_config import get_crypto_pair_config, CRYPTO_CONFIGS

    config = get_crypto_pair_config("BTC/USDT")
    engine = CryptoAnalysisEngine(pair_config=config)
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List
from enum import Enum


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class CryptoCategory(str, Enum):
    LARGE_CAP = "large_cap"
    MID_CAP = "mid_cap"
    SMALL_CAP = "small_cap"
    MEME = "meme"
    DEFI = "defi"
    AI = "ai"
    LAYER2 = "layer2"
    GAMING = "gaming"
    INFRASTRUCTURE = "infrastructure"


# ──────────────────────────────────────────────
# Crypto Pair Config
# ──────────────────────────────────────────────

@dataclass
class CryptoPairConfig:
    """Configuration for a crypto trading pair on Binance."""

    # ── Identity ──
    symbol: str                         # CCXT format: "BTC/USDT"
    base_asset: str                     # "BTC"
    quote_asset: str = "USDT"           # Always USDT for our setup
    category: CryptoCategory = CryptoCategory.MID_CAP

    # ── Contract Specs (Binance Spot) ──
    min_order_amount: float = 0.0001    # Minimum base asset order
    max_order_amount: float = 9999.0    # Maximum base asset order
    amount_precision: int = 4           # Decimal places for amount
    price_precision: int = 2            # Decimal places for price
    min_notional: float = 10.0          # Minimum order value in USDT

    # ── Risk Parameters ──
    atr_sl_multiplier: float = 2.5      # ATR × N for stop loss
    rr_target: float = 2.0              # Reward:Risk target
    min_rr: float = 1.5                 # Minimum acceptable R:R
    max_position_pct: float = 5.0       # Max % of portfolio per position

    # ── Signal Filters ──
    min_adx: int = 18                   # ADX threshold for trending
    min_confluence: int = 45            # Minimum confluence score (0-100)

    # ── FVG Thresholds (percentage-based) ──
    min_fvg_pct: float = 0.05           # Minimum FVG as % of price

    # ── Session (24/7 for crypto) ──
    use_session_filter: bool = False
    high_volume_hours: List[int] = field(default_factory=lambda: [13, 14, 15, 16, 17, 18, 19, 20])

    # ── Spread & Costs ──
    typical_spread_pct: float = 0.05    # Typical spread as % of price
    maker_fee_pct: float = 0.10         # 0.10% maker fee
    taker_fee_pct: float = 0.10         # 0.10% taker fee

    # ── Display ──
    price_decimals: int = 2             # Decimal places for display

    # ── Volatility Profile ──
    avg_daily_range_pct: float = 3.0    # Average daily range as %
    is_volatile: bool = False           # Flag for extra-volatile coins

    @property
    def dollar_per_point_per_unit(self) -> float:
        """$1 price move = $1 per unit held (spot)."""
        return 1.0

    def fvg_threshold(self, current_price: float) -> float:
        """Get minimum FVG size threshold based on price."""
        return current_price * (self.min_fvg_pct / 100)



# ══════════════════════════════════════════════════════════════
# PRE-BUILT CONFIGURATIONS — ALL BINANCE SPOT USDT PAIRS
# ══════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────
# LARGE CAP (Top 10 by market cap)
# High liquidity, tighter stops, reliable signals
# ──────────────────────────────────────────────

BTC_USDT = CryptoPairConfig(
    symbol="BTC/USDT", base_asset="BTC", category=CryptoCategory.LARGE_CAP,
    min_order_amount=0.00001, amount_precision=5, price_precision=2,
    min_notional=10.0, atr_sl_multiplier=2.0, rr_target=2.0,
    min_adx=20, min_confluence=50, min_fvg_pct=0.03,
    typical_spread_pct=0.01, price_decimals=2,
    avg_daily_range_pct=2.5, max_position_pct=10.0,
)

ETH_USDT = CryptoPairConfig(
    symbol="ETH/USDT", base_asset="ETH", category=CryptoCategory.LARGE_CAP,
    min_order_amount=0.0001, amount_precision=4, price_precision=2,
    min_notional=10.0, atr_sl_multiplier=2.0, rr_target=2.0,
    min_adx=20, min_confluence=50, min_fvg_pct=0.03,
    typical_spread_pct=0.01, price_decimals=2,
    avg_daily_range_pct=3.0, max_position_pct=10.0,
)

BNB_USDT = CryptoPairConfig(
    symbol="BNB/USDT", base_asset="BNB", category=CryptoCategory.LARGE_CAP,
    min_order_amount=0.001, amount_precision=3, price_precision=2,
    min_notional=10.0, atr_sl_multiplier=2.2, rr_target=2.0,
    min_adx=20, min_confluence=48, min_fvg_pct=0.04,
    typical_spread_pct=0.02, price_decimals=2,
    avg_daily_range_pct=3.0, max_position_pct=8.0,
)

SOL_USDT = CryptoPairConfig(
    symbol="SOL/USDT", base_asset="SOL", category=CryptoCategory.LARGE_CAP,
    min_order_amount=0.01, amount_precision=2, price_precision=2,
    min_notional=10.0, atr_sl_multiplier=2.5, rr_target=2.0,
    min_adx=18, min_confluence=48, min_fvg_pct=0.04,
    typical_spread_pct=0.02, price_decimals=2,
    avg_daily_range_pct=4.0, max_position_pct=8.0,
)

XRP_USDT = CryptoPairConfig(
    symbol="XRP/USDT", base_asset="XRP", category=CryptoCategory.LARGE_CAP,
    min_order_amount=0.1, amount_precision=1, price_precision=4,
    min_notional=10.0, atr_sl_multiplier=2.5, rr_target=2.0,
    min_adx=18, min_confluence=48, min_fvg_pct=0.04,
    typical_spread_pct=0.02, price_decimals=4,
    avg_daily_range_pct=3.5, max_position_pct=7.0,
)

ADA_USDT = CryptoPairConfig(
    symbol="ADA/USDT", base_asset="ADA", category=CryptoCategory.LARGE_CAP,
    min_order_amount=1.0, amount_precision=1, price_precision=4,
    min_notional=10.0, atr_sl_multiplier=2.5, rr_target=2.0,
    min_adx=18, min_confluence=48, min_fvg_pct=0.04,
    typical_spread_pct=0.03, price_decimals=4,
    avg_daily_range_pct=4.0, max_position_pct=7.0,
)

DOGE_USDT = CryptoPairConfig(
    symbol="DOGE/USDT", base_asset="DOGE", category=CryptoCategory.MEME,
    min_order_amount=1.0, amount_precision=0, price_precision=5,
    min_notional=10.0, atr_sl_multiplier=3.0, rr_target=2.5,
    min_adx=18, min_confluence=50, min_fvg_pct=0.06,
    typical_spread_pct=0.04, price_decimals=5,
    avg_daily_range_pct=5.0, max_position_pct=5.0, is_volatile=True,
)

DOT_USDT = CryptoPairConfig(
    symbol="DOT/USDT", base_asset="DOT", category=CryptoCategory.LARGE_CAP,
    min_order_amount=0.1, amount_precision=2, price_precision=3,
    min_notional=10.0, atr_sl_multiplier=2.5, rr_target=2.0,
    min_adx=18, min_confluence=48, min_fvg_pct=0.04,
    typical_spread_pct=0.03, price_decimals=3,
    avg_daily_range_pct=4.0, max_position_pct=6.0,
)

AVAX_USDT = CryptoPairConfig(
    symbol="AVAX/USDT", base_asset="AVAX", category=CryptoCategory.LARGE_CAP,
    min_order_amount=0.01, amount_precision=2, price_precision=2,
    min_notional=10.0, atr_sl_multiplier=2.5, rr_target=2.0,
    min_adx=18, min_confluence=48, min_fvg_pct=0.04,
    typical_spread_pct=0.03, price_decimals=2,
    avg_daily_range_pct=4.5, max_position_pct=6.0,
)

LINK_USDT = CryptoPairConfig(
    symbol="LINK/USDT", base_asset="LINK", category=CryptoCategory.INFRASTRUCTURE,
    min_order_amount=0.01, amount_precision=2, price_precision=3,
    min_notional=10.0, atr_sl_multiplier=2.5, rr_target=2.0,
    min_adx=18, min_confluence=48, min_fvg_pct=0.04,
    typical_spread_pct=0.03, price_decimals=3,
    avg_daily_range_pct=4.0, max_position_pct=6.0,
)

# ──────────────────────────────────────────────
# MID CAP (Top 11-50)
# ──────────────────────────────────────────────

MID_CAP_PAIRS = {
    "MATIC/USDT": CryptoPairConfig(
        symbol="MATIC/USDT", base_asset="MATIC", category=CryptoCategory.LAYER2,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=4.5, max_position_pct=5.0,
    ),
    "UNI/USDT": CryptoPairConfig(
        symbol="UNI/USDT", base_asset="UNI", category=CryptoCategory.DEFI,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=4.5, max_position_pct=5.0,
    ),
    "ATOM/USDT": CryptoPairConfig(
        symbol="ATOM/USDT", base_asset="ATOM", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=4.0, max_position_pct=5.0,
    ),
    "LTC/USDT": CryptoPairConfig(
        symbol="LTC/USDT", base_asset="LTC", category=CryptoCategory.LARGE_CAP,
        min_order_amount=0.001, amount_precision=3, price_precision=2,
        atr_sl_multiplier=2.2, min_fvg_pct=0.04, price_decimals=2,
        avg_daily_range_pct=3.5, max_position_pct=6.0,
    ),
    "ETC/USDT": CryptoPairConfig(
        symbol="ETC/USDT", base_asset="ETC", category=CryptoCategory.LARGE_CAP,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=4.0, max_position_pct=5.0,
    ),
    "FIL/USDT": CryptoPairConfig(
        symbol="FIL/USDT", base_asset="FIL", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=5.0, max_position_pct=5.0,
    ),
    "APT/USDT": CryptoPairConfig(
        symbol="APT/USDT", base_asset="APT", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=5.0, max_position_pct=5.0,
    ),
    "ARB/USDT": CryptoPairConfig(
        symbol="ARB/USDT", base_asset="ARB", category=CryptoCategory.LAYER2,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=5.0, max_position_pct=5.0,
    ),
    "OP/USDT": CryptoPairConfig(
        symbol="OP/USDT", base_asset="OP", category=CryptoCategory.LAYER2,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=5.0, max_position_pct=5.0,
    ),
    "NEAR/USDT": CryptoPairConfig(
        symbol="NEAR/USDT", base_asset="NEAR", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.1, amount_precision=1, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=5.0, max_position_pct=5.0,
    ),
    "INJ/USDT": CryptoPairConfig(
        symbol="INJ/USDT", base_asset="INJ", category=CryptoCategory.DEFI,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=5.5, max_position_pct=5.0,
    ),
    "SUI/USDT": CryptoPairConfig(
        symbol="SUI/USDT", base_asset="SUI", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=5.5, max_position_pct=5.0,
    ),
    "TIA/USDT": CryptoPairConfig(
        symbol="TIA/USDT", base_asset="TIA", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=6.0, max_position_pct=4.0,
    ),
    "SEI/USDT": CryptoPairConfig(
        symbol="SEI/USDT", base_asset="SEI", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=6.0, max_position_pct=4.0,
    ),
    "ICP/USDT": CryptoPairConfig(
        symbol="ICP/USDT", base_asset="ICP", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.01, amount_precision=2, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=5.0, max_position_pct=5.0,
    ),
    "HBAR/USDT": CryptoPairConfig(
        symbol="HBAR/USDT", base_asset="HBAR", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=0, price_precision=5,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=5,
        avg_daily_range_pct=4.5, max_position_pct=5.0,
    ),
    "VET/USDT": CryptoPairConfig(
        symbol="VET/USDT", base_asset="VET", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=0, price_precision=5,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=5,
        avg_daily_range_pct=4.5, max_position_pct=5.0,
    ),
    "ALGO/USDT": CryptoPairConfig(
        symbol="ALGO/USDT", base_asset="ALGO", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=4.5, max_position_pct=5.0,
    ),
    "XLM/USDT": CryptoPairConfig(
        symbol="XLM/USDT", base_asset="XLM", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=0, price_precision=5,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=5,
        avg_daily_range_pct=4.0, max_position_pct=5.0,
    ),
    "TRX/USDT": CryptoPairConfig(
        symbol="TRX/USDT", base_asset="TRX", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=0, price_precision=5,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=5,
        avg_daily_range_pct=3.5, max_position_pct=5.0,
    ),
    "RUNE/USDT": CryptoPairConfig(
        symbol="RUNE/USDT", base_asset="RUNE", category=CryptoCategory.DEFI,
        min_order_amount=0.1, amount_precision=1, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "FTM/USDT": CryptoPairConfig(
        symbol="FTM/USDT", base_asset="FTM", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=0, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "GRT/USDT": CryptoPairConfig(
        symbol="GRT/USDT", base_asset="GRT", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=1.0, amount_precision=0, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=5.0, max_position_pct=4.0,
    ),
    "THETA/USDT": CryptoPairConfig(
        symbol="THETA/USDT", base_asset="THETA", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=5.0, max_position_pct=4.0,
    ),
    "EGLD/USDT": CryptoPairConfig(
        symbol="EGLD/USDT", base_asset="EGLD", category=CryptoCategory.INFRASTRUCTURE,
        min_order_amount=0.01, amount_precision=2, price_precision=2,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=2,
        avg_daily_range_pct=5.0, max_position_pct=4.0,
    ),
}



# ──────────────────────────────────────────────
# DeFi Tokens
# ──────────────────────────────────────────────

DEFI_PAIRS = {
    "AAVE/USDT": CryptoPairConfig(
        symbol="AAVE/USDT", base_asset="AAVE", category=CryptoCategory.DEFI,
        min_order_amount=0.001, amount_precision=3, price_precision=2,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=2,
        avg_daily_range_pct=5.0, max_position_pct=4.0,
    ),
    "MKR/USDT": CryptoPairConfig(
        symbol="MKR/USDT", base_asset="MKR", category=CryptoCategory.DEFI,
        min_order_amount=0.0001, amount_precision=4, price_precision=1,
        atr_sl_multiplier=2.5, min_fvg_pct=0.04, price_decimals=1,
        avg_daily_range_pct=4.5, max_position_pct=4.0,
    ),
    "CRV/USDT": CryptoPairConfig(
        symbol="CRV/USDT", base_asset="CRV", category=CryptoCategory.DEFI,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=3.0, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=6.0, max_position_pct=3.0, is_volatile=True,
    ),
    "LDO/USDT": CryptoPairConfig(
        symbol="LDO/USDT", base_asset="LDO", category=CryptoCategory.DEFI,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=4,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "SNX/USDT": CryptoPairConfig(
        symbol="SNX/USDT", base_asset="SNX", category=CryptoCategory.DEFI,
        min_order_amount=0.1, amount_precision=1, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=3,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "COMP/USDT": CryptoPairConfig(
        symbol="COMP/USDT", base_asset="COMP", category=CryptoCategory.DEFI,
        min_order_amount=0.01, amount_precision=2, price_precision=2,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=2,
        avg_daily_range_pct=5.0, max_position_pct=4.0,
    ),
    "SUSHI/USDT": CryptoPairConfig(
        symbol="SUSHI/USDT", base_asset="SUSHI", category=CryptoCategory.DEFI,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=3.0, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=6.0, max_position_pct=3.0, is_volatile=True,
    ),
    "1INCH/USDT": CryptoPairConfig(
        symbol="1INCH/USDT", base_asset="1INCH", category=CryptoCategory.DEFI,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=5.5, max_position_pct=3.0,
    ),
    "DYDX/USDT": CryptoPairConfig(
        symbol="DYDX/USDT", base_asset="DYDX", category=CryptoCategory.DEFI,
        min_order_amount=0.1, amount_precision=1, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.05, price_decimals=3,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "JUP/USDT": CryptoPairConfig(
        symbol="JUP/USDT", base_asset="JUP", category=CryptoCategory.DEFI,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=6.0, max_position_pct=4.0,
    ),
}

# ──────────────────────────────────────────────
# AI / Narrative Tokens
# ──────────────────────────────────────────────

AI_PAIRS = {
    "FET/USDT": CryptoPairConfig(
        symbol="FET/USDT", base_asset="FET", category=CryptoCategory.AI,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=3.0, rr_target=2.5, min_fvg_pct=0.06,
        price_decimals=4, avg_daily_range_pct=6.0, max_position_pct=4.0,
        is_volatile=True,
    ),
    "RENDER/USDT": CryptoPairConfig(
        symbol="RENDER/USDT", base_asset="RENDER", category=CryptoCategory.AI,
        min_order_amount=0.1, amount_precision=1, price_precision=3,
        atr_sl_multiplier=3.0, rr_target=2.5, min_fvg_pct=0.06,
        price_decimals=3, avg_daily_range_pct=6.5, max_position_pct=4.0,
        is_volatile=True,
    ),
    "AGIX/USDT": CryptoPairConfig(
        symbol="AGIX/USDT", base_asset="AGIX", category=CryptoCategory.AI,
        min_order_amount=1.0, amount_precision=0, price_precision=4,
        atr_sl_multiplier=3.0, rr_target=2.5, min_fvg_pct=0.06,
        price_decimals=4, avg_daily_range_pct=7.0, max_position_pct=3.0,
        is_volatile=True,
    ),
    "WLD/USDT": CryptoPairConfig(
        symbol="WLD/USDT", base_asset="WLD", category=CryptoCategory.AI,
        min_order_amount=0.1, amount_precision=1, price_precision=3,
        atr_sl_multiplier=3.0, rr_target=2.5, min_fvg_pct=0.06,
        price_decimals=3, avg_daily_range_pct=7.0, max_position_pct=3.0,
        is_volatile=True,
    ),
    "TAO/USDT": CryptoPairConfig(
        symbol="TAO/USDT", base_asset="TAO", category=CryptoCategory.AI,
        min_order_amount=0.001, amount_precision=3, price_precision=1,
        atr_sl_multiplier=3.0, rr_target=2.5, min_fvg_pct=0.05,
        price_decimals=1, avg_daily_range_pct=6.0, max_position_pct=4.0,
        is_volatile=True,
    ),
}

# ──────────────────────────────────────────────
# MEME Coins (High volatility, speculative)
# ──────────────────────────────────────────────

MEME_PAIRS = {
    "SHIB/USDT": CryptoPairConfig(
        symbol="SHIB/USDT", base_asset="SHIB", category=CryptoCategory.MEME,
        min_order_amount=1000.0, amount_precision=0, price_precision=8,
        atr_sl_multiplier=3.5, rr_target=3.0, min_confluence=52,
        min_fvg_pct=0.08, price_decimals=8,
        avg_daily_range_pct=7.0, max_position_pct=3.0, is_volatile=True,
    ),
    "PEPE/USDT": CryptoPairConfig(
        symbol="PEPE/USDT", base_asset="PEPE", category=CryptoCategory.MEME,
        min_order_amount=100000.0, amount_precision=0, price_precision=8,
        atr_sl_multiplier=3.5, rr_target=3.0, min_confluence=52,
        min_fvg_pct=0.08, price_decimals=8,
        avg_daily_range_pct=8.0, max_position_pct=2.0, is_volatile=True,
    ),
    "WIF/USDT": CryptoPairConfig(
        symbol="WIF/USDT", base_asset="WIF", category=CryptoCategory.MEME,
        min_order_amount=1.0, amount_precision=1, price_precision=4,
        atr_sl_multiplier=3.5, rr_target=3.0, min_confluence=52,
        min_fvg_pct=0.08, price_decimals=4,
        avg_daily_range_pct=10.0, max_position_pct=2.0, is_volatile=True,
    ),
    "FLOKI/USDT": CryptoPairConfig(
        symbol="FLOKI/USDT", base_asset="FLOKI", category=CryptoCategory.MEME,
        min_order_amount=100.0, amount_precision=0, price_precision=7,
        atr_sl_multiplier=3.5, rr_target=3.0, min_confluence=52,
        min_fvg_pct=0.08, price_decimals=7,
        avg_daily_range_pct=8.0, max_position_pct=2.0, is_volatile=True,
    ),
    "BONK/USDT": CryptoPairConfig(
        symbol="BONK/USDT", base_asset="BONK", category=CryptoCategory.MEME,
        min_order_amount=10000.0, amount_precision=0, price_precision=8,
        atr_sl_multiplier=3.5, rr_target=3.0, min_confluence=52,
        min_fvg_pct=0.08, price_decimals=8,
        avg_daily_range_pct=9.0, max_position_pct=2.0, is_volatile=True,
    ),
}

# ──────────────────────────────────────────────
# Gaming / Metaverse
# ──────────────────────────────────────────────

GAMING_PAIRS = {
    "AXS/USDT": CryptoPairConfig(
        symbol="AXS/USDT", base_asset="AXS", category=CryptoCategory.GAMING,
        min_order_amount=0.1, amount_precision=1, price_precision=3,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=3,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "SAND/USDT": CryptoPairConfig(
        symbol="SAND/USDT", base_asset="SAND", category=CryptoCategory.GAMING,
        min_order_amount=1.0, amount_precision=0, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "MANA/USDT": CryptoPairConfig(
        symbol="MANA/USDT", base_asset="MANA", category=CryptoCategory.GAMING,
        min_order_amount=1.0, amount_precision=0, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=5.5, max_position_pct=4.0,
    ),
    "IMX/USDT": CryptoPairConfig(
        symbol="IMX/USDT", base_asset="IMX", category=CryptoCategory.GAMING,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=6.0, max_position_pct=4.0,
    ),
    "GALA/USDT": CryptoPairConfig(
        symbol="GALA/USDT", base_asset="GALA", category=CryptoCategory.GAMING,
        min_order_amount=1.0, amount_precision=0, price_precision=5,
        atr_sl_multiplier=3.0, min_fvg_pct=0.06, price_decimals=5,
        avg_daily_range_pct=6.5, max_position_pct=3.0, is_volatile=True,
    ),
}

# ──────────────────────────────────────────────
# Layer 2 / Scaling
# ──────────────────────────────────────────────

LAYER2_PAIRS = {
    "STRK/USDT": CryptoPairConfig(
        symbol="STRK/USDT", base_asset="STRK", category=CryptoCategory.LAYER2,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=6.0, max_position_pct=4.0,
    ),
    "MANTA/USDT": CryptoPairConfig(
        symbol="MANTA/USDT", base_asset="MANTA", category=CryptoCategory.LAYER2,
        min_order_amount=0.1, amount_precision=1, price_precision=4,
        atr_sl_multiplier=2.5, min_fvg_pct=0.06, price_decimals=4,
        avg_daily_range_pct=6.0, max_position_pct=4.0,
    ),
}


# ══════════════════════════════════════════════════════════════
# MASTER REGISTRY
# ══════════════════════════════════════════════════════════════

# All configs in one dict
CRYPTO_CONFIGS: Dict[str, CryptoPairConfig] = {
    # Large Cap
    "BTC/USDT": BTC_USDT,
    "ETH/USDT": ETH_USDT,
    "BNB/USDT": BNB_USDT,
    "SOL/USDT": SOL_USDT,
    "XRP/USDT": XRP_USDT,
    "ADA/USDT": ADA_USDT,
    "DOGE/USDT": DOGE_USDT,
    "DOT/USDT": DOT_USDT,
    "AVAX/USDT": AVAX_USDT,
    "LINK/USDT": LINK_USDT,
    # Mid Cap
    **MID_CAP_PAIRS,
    # DeFi
    **DEFI_PAIRS,
    # AI
    **AI_PAIRS,
    # Meme
    **MEME_PAIRS,
    # Gaming
    **GAMING_PAIRS,
    # Layer 2
    **LAYER2_PAIRS,
}


# ──────────────────────────────────────────────
# Lookup Functions
# ──────────────────────────────────────────────

def get_crypto_pair_config(symbol: str) -> CryptoPairConfig:
    """
    Get configuration for a crypto pair.
    
    Args:
        symbol: CCXT format pair (e.g. "BTC/USDT")
    
    Returns:
        CryptoPairConfig for the pair
    
    Raises:
        KeyError if pair not found (use create_default_config as fallback)
    """
    symbol = symbol.upper().strip()
    if symbol in CRYPTO_CONFIGS:
        return CRYPTO_CONFIGS[symbol]
    raise KeyError(
        f"No config for '{symbol}'. Use create_default_config() for unknown pairs. "
        f"Known pairs: {len(CRYPTO_CONFIGS)}"
    )


def create_default_config(symbol: str, category: CryptoCategory = CryptoCategory.SMALL_CAP) -> CryptoPairConfig:
    """
    Create a default config for any unknown pair.
    Uses conservative parameters suitable for small/unknown coins.
    """
    base = symbol.split("/")[0] if "/" in symbol else symbol
    quote = symbol.split("/")[1] if "/" in symbol else "USDT"

    return CryptoPairConfig(
        symbol=f"{base}/{quote}",
        base_asset=base,
        quote_asset=quote,
        category=category,
        min_order_amount=1.0,
        amount_precision=2,
        price_precision=4,
        min_notional=10.0,
        atr_sl_multiplier=3.0,      # Wider stops for unknown coins
        rr_target=2.5,               # Higher R:R required
        min_rr=2.0,
        max_position_pct=3.0,        # Smaller position
        min_adx=20,                  # Stricter ADX filter
        min_confluence=50,           # Higher confluence required
        min_fvg_pct=0.06,
        price_decimals=4,
        avg_daily_range_pct=6.0,
        is_volatile=True,
    )


def get_config_or_default(symbol: str) -> CryptoPairConfig:
    """Get config for a pair, falling back to default if unknown."""
    try:
        return get_crypto_pair_config(symbol)
    except KeyError:
        return create_default_config(symbol)


def get_pairs_by_category(category: CryptoCategory) -> List[CryptoPairConfig]:
    """Get all pairs in a specific category."""
    return [c for c in CRYPTO_CONFIGS.values() if c.category == category]


def get_all_symbols() -> List[str]:
    """Get list of all configured symbols."""
    return list(CRYPTO_CONFIGS.keys())


def get_watchlist(tier: str = "top20") -> List[str]:
    """
    Get a pre-defined watchlist.
    
    Tiers:
    - "top10": BTC, ETH, BNB, SOL, XRP, ADA, DOT, AVAX, LINK, DOGE
    - "top20": top10 + MATIC, UNI, ATOM, LTC, ARB, OP, NEAR, INJ, SUI, APT
    - "defi": DeFi tokens only
    - "ai": AI narrative tokens
    - "meme": Meme coins (high risk)
    - "all": All configured pairs
    """
    if tier == "top10":
        return ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
                "ADA/USDT", "DOT/USDT", "AVAX/USDT", "LINK/USDT", "DOGE/USDT"]
    elif tier == "top20":
        return ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
                "ADA/USDT", "DOT/USDT", "AVAX/USDT", "LINK/USDT", "DOGE/USDT",
                "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "ARB/USDT",
                "OP/USDT", "NEAR/USDT", "INJ/USDT", "SUI/USDT", "APT/USDT"]
    elif tier == "defi":
        return [c.symbol for c in get_pairs_by_category(CryptoCategory.DEFI)]
    elif tier == "ai":
        return [c.symbol for c in get_pairs_by_category(CryptoCategory.AI)]
    elif tier == "meme":
        return [c.symbol for c in get_pairs_by_category(CryptoCategory.MEME)]
    elif tier == "all":
        return get_all_symbols()
    else:
        return get_all_symbols()
