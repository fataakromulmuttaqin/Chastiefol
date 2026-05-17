"""
Chastiefol — Crypto Analysis Engine
Adapted from the XAUUSD engine for cryptocurrency trading on Binance.

Key Differences from Gold/Forex:
- 24/7 market (no session filter needed)
- Higher volatility → wider ATR multipliers
- Volume is more significant (on-chain + exchange)
- Percentage-based FVG thresholds (not absolute price)
- Funding rate awareness (for futures context)
- Market cap & dominance factors

Supports ALL Binance spot pairs via dynamic PairConfig.

Signal Logic (same core as XAUUSD):
  Primary: Parabolic SAR flip + EMA 20/50 alignment + ADX trending
  Confirmation: Market structure, RSI, MACD, OB, FVG, volume
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum

from .xauusd_engine import (
    TechnicalIndicators,
    MarketStructureAnalyzer,
    ConfluenceScorer,
    MarketStructure,
    Signal,
    Bias,
    TradeSetup,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("CryptoEngine")


# ──────────────────────────────────────────────
# Crypto-Specific Indicators
# ──────────────────────────────────────────────

class CryptoIndicators(TechnicalIndicators):
    """
    Extended indicator library for crypto markets.
    Adds volume-based and crypto-specific indicators.
    """

    @staticmethod
    def volume_profile(close: pd.Series, volume: pd.Series,
                       period: int = 20) -> pd.Series:
        """
        Volume-Weighted Average Price (rolling VWAP).
        Important for crypto where volume spikes indicate
        institutional activity or whale movements.
        """
        typical_price = close  # Simplified; use (H+L+C)/3 if available
        return (typical_price * volume).rolling(period).sum() / \
               volume.rolling(period).sum()

    @staticmethod
    def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
        """
        Current volume relative to average.
        > 2.0 = unusual volume (potential breakout/reversal)
        > 3.0 = extreme volume (whale activity)
        """
        avg_vol = volume.rolling(period).mean()
        return volume / avg_vol.replace(0, np.nan)

    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """
        On-Balance Volume — accumulation/distribution indicator.
        Rising OBV + rising price = confirmed uptrend.
        Divergence = potential reversal.
        """
        direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        return (volume * direction).cumsum()

    @staticmethod
    def mfi(high: pd.Series, low: pd.Series, close: pd.Series,
            volume: pd.Series, period: int = 14) -> pd.Series:
        """
        Money Flow Index — volume-weighted RSI.
        Crypto-specific: captures buying/selling pressure with volume.
        > 80 = overbought, < 20 = oversold
        """
        typical_price = (high + low + close) / 3
        money_flow = typical_price * volume
        
        tp_diff = typical_price.diff()
        pos_flow = money_flow.where(tp_diff > 0, 0).rolling(period).sum()
        neg_flow = money_flow.where(tp_diff < 0, 0).rolling(period).sum()
        
        mfi = 100 - (100 / (1 + pos_flow / neg_flow.replace(0, np.nan)))
        return mfi

    @staticmethod
    def volatility_percentile(close: pd.Series, period: int = 100,
                               atr_period: int = 14) -> float:
        """
        Current volatility as percentile of recent history.
        Useful for position sizing in crypto (high vol = smaller size).
        """
        returns = close.pct_change().dropna()
        if len(returns) < period:
            return 50.0
        
        current_vol = returns.tail(atr_period).std()
        historical_vols = returns.rolling(atr_period).std().dropna()
        
        if len(historical_vols) == 0:
            return 50.0
        
        percentile = (historical_vols < current_vol).sum() / len(historical_vols) * 100
        return float(percentile)

    @staticmethod
    def fibonacci_levels(high: pd.Series, low: pd.Series, close: pd.Series,
                         lookback: int = 50,
                         retracement_levels: List[float] = None,
                         extension_levels: List[float] = None,
                         ) -> Dict[str, any]:
        """
        Fibonacci Retracement & Extension levels based on recent swing high/low.

        Finds the highest high and lowest low within the lookback period,
        then calculates Fibonacci retracement levels between them.
        Also calculates extension levels beyond the swing.

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            lookback: Bars to look back for swing detection
            retracement_levels: List of Fib ratios [0.236, 0.382, 0.5, 0.618, 0.786]
            extension_levels: List of extension ratios [1.0, 1.272, 1.618, 2.0]

        Returns:
            Dict with:
            - swing_high: float
            - swing_low: float
            - trend: "up" or "down" (based on which came last)
            - retracements: Dict[float, float] mapping ratio → price level
            - extensions: Dict[float, float] mapping ratio → price level
            - current_price: float
            - nearest_level: float (closest fib level to current price)
            - nearest_ratio: float (the ratio of nearest level)
            - distance_pct: float (distance to nearest level as % of price)
        """
        if retracement_levels is None:
            retracement_levels = [0.236, 0.382, 0.5, 0.618, 0.786]
        if extension_levels is None:
            extension_levels = [1.0, 1.272, 1.618, 2.0, 2.618]

        if len(high) < lookback:
            lookback = len(high)

        recent_high = high.tail(lookback)
        recent_low = low.tail(lookback)

        swing_high = float(recent_high.max())
        swing_low = float(recent_low.min())
        current = float(close.iloc[-1])

        # Determine trend direction (which swing came last)
        high_idx = recent_high.idxmax()
        low_idx = recent_low.idxmin()
        trend = "up" if low_idx < high_idx else "down"

        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return {
                "swing_high": swing_high, "swing_low": swing_low,
                "trend": "neutral", "retracements": {}, "extensions": {},
                "current_price": current, "nearest_level": current,
                "nearest_ratio": 0.5, "distance_pct": 0.0,
            }

        # Calculate retracement levels
        retracements = {}
        if trend == "up":
            # Uptrend: retrace from high down
            for level in retracement_levels:
                retracements[level] = swing_high - (swing_range * level)
        else:
            # Downtrend: retrace from low up
            for level in retracement_levels:
                retracements[level] = swing_low + (swing_range * level)

        # Calculate extension levels
        extensions = {}
        if trend == "up":
            for level in extension_levels:
                extensions[level] = swing_high + (swing_range * (level - 1.0))
        else:
            for level in extension_levels:
                extensions[level] = swing_low - (swing_range * (level - 1.0))

        # Find nearest Fibonacci level to current price
        all_levels = {**retracements, **extensions}
        nearest_ratio = 0.5
        nearest_level = current
        min_distance = float("inf")

        for ratio, price_level in all_levels.items():
            dist = abs(current - price_level)
            if dist < min_distance:
                min_distance = dist
                nearest_level = price_level
                nearest_ratio = ratio

        distance_pct = (min_distance / current * 100) if current > 0 else 0.0

        return {
            "swing_high": swing_high,
            "swing_low": swing_low,
            "trend": trend,
            "retracements": retracements,
            "extensions": extensions,
            "current_price": current,
            "nearest_level": nearest_level,
            "nearest_ratio": nearest_ratio,
            "distance_pct": distance_pct,
        }

    @staticmethod
    def is_near_fibonacci(high: pd.Series, low: pd.Series, close: pd.Series,
                          lookback: int = 50,
                          proximity_pct: float = 0.3,
                          key_levels: List[float] = None,
                          ) -> tuple:
        """
        Quick check: is current price near a key Fibonacci level?

        Args:
            proximity_pct: How close price must be to a fib level (% of price)
            key_levels: Which fib ratios matter most [0.382, 0.5, 0.618]

        Returns:
            (is_near: bool, level_info: str)
        """
        if key_levels is None:
            key_levels = [0.382, 0.5, 0.618]

        fib = CryptoIndicators.fibonacci_levels(high, low, close, lookback=lookback)

        if not fib["retracements"]:
            return False, ""

        current = fib["current_price"]

        # Check if near any key retracement level
        for ratio, price_level in fib["retracements"].items():
            if ratio in key_levels:
                dist_pct = abs(current - price_level) / current * 100
                if dist_pct <= proximity_pct:
                    return True, f"Fib {ratio*100:.1f}% @ ${price_level:,.2f} (dist: {dist_pct:.2f}%)"

        return False, ""

    @staticmethod
    def ichimoku_cloud(high: pd.Series, low: pd.Series,
                       tenkan: int = 9, kijun: int = 26,
                       senkou_b: int = 52) -> Dict[str, pd.Series]:
        """
        Ichimoku Cloud — popular in crypto for trend + S/R levels.
        Returns dict with conversion_line, base_line, leading_span_a, leading_span_b.
        """
        # Tenkan-sen (Conversion Line)
        tenkan_high = high.rolling(tenkan).max()
        tenkan_low = low.rolling(tenkan).min()
        conversion = (tenkan_high + tenkan_low) / 2

        # Kijun-sen (Base Line)
        kijun_high = high.rolling(kijun).max()
        kijun_low = low.rolling(kijun).min()
        base = (kijun_high + kijun_low) / 2

        # Senkou Span A (Leading Span A) — shifted forward 26 periods
        span_a = ((conversion + base) / 2).shift(kijun)

        # Senkou Span B (Leading Span B) — shifted forward 26 periods
        senkou_high = high.rolling(senkou_b).max()
        senkou_low = low.rolling(senkou_b).min()
        span_b = ((senkou_high + senkou_low) / 2).shift(kijun)

        return {
            "conversion": conversion,
            "base": base,
            "span_a": span_a,
            "span_b": span_b,
        }


# ──────────────────────────────────────────────
# Crypto Confluence Scorer
# ──────────────────────────────────────────────

class CryptoConfluenceScorer(ConfluenceScorer):
    """
    Extended confluence scorer for crypto with volume-based factors.
    Reads weights from Analysis/analysis_config.py (user-editable).
    """

    def __init__(self, weights: Dict[str, int] = None):
        """
        Initialize scorer with custom weights or load from analysis_config.

        Args:
            weights: Optional custom weights dict. If None, loads from config file.
        """
        if weights:
            self.WEIGHTS = weights
        else:
            try:
                from .analysis_config import CONFLUENCE_WEIGHTS
                self.WEIGHTS = CONFLUENCE_WEIGHTS
            except ImportError:
                # Fallback defaults if config file missing
                self.WEIGHTS = {
                    "adx_trending": 12, "psar_aligned": 14,
                    "market_structure_aligned": 14, "ema_aligned": 10,
                    "rsi_zone": 8, "macd_aligned": 8,
                    "order_block_proximity": 7, "fvg_in_range": 5,
                    "session_active": 2, "bos_confirmed": 5,
                    "volume_confirmation": 10, "mfi_aligned": 5,
                    "fibonacci_level": 7,
                }

    def score(self, df: pd.DataFrame, direction: Signal,
              structure: MarketStructure, session_active: bool
              ) -> tuple[int, list[str]]:
        """Extended scoring with crypto-specific factors + Fibonacci."""
        # Get base score from parent
        score, reasons = super().score(df, direction, structure, session_active)
        
        ci = CryptoIndicators()

        # Load config for fibonacci settings
        try:
            from .analysis_config import (
                FIBONACCI_SWING_LOOKBACK, FIBONACCI_PROXIMITY_PCT,
                FIBONACCI_KEY_LEVELS, VOLUME_SPIKE_THRESHOLD,
            )
        except ImportError:
            FIBONACCI_SWING_LOOKBACK = 50
            FIBONACCI_PROXIMITY_PCT = 0.3
            FIBONACCI_KEY_LEVELS = [0.382, 0.5, 0.618]
            VOLUME_SPIKE_THRESHOLD = 1.5

        # ── Volume Confirmation ──
        if "volume" in df.columns and df["volume"].sum() > 0:
            vol_ratio = ci.volume_ratio(df["volume"])
            current_vol_ratio = vol_ratio.iloc[-1] if not pd.isna(vol_ratio.iloc[-1]) else 1.0
            
            if current_vol_ratio >= VOLUME_SPIKE_THRESHOLD:
                score += self.WEIGHTS.get("volume_confirmation", 10)
                if current_vol_ratio >= 3.0:
                    reasons.append(f"Extreme volume ({current_vol_ratio:.1f}x avg) — whale activity")
                elif current_vol_ratio >= 2.0:
                    reasons.append(f"High volume ({current_vol_ratio:.1f}x avg) — confirms move")
                else:
                    reasons.append(f"Above-avg volume ({current_vol_ratio:.1f}x) — supportive")

        # ── Money Flow Index ──
        if all(col in df.columns for col in ["high", "low", "close", "volume"]):
            if df["volume"].sum() > 0:
                mfi = ci.mfi(df["high"], df["low"], df["close"], df["volume"])
                current_mfi = mfi.iloc[-1] if not pd.isna(mfi.iloc[-1]) else 50
                
                if direction == Signal.BUY and 30 <= current_mfi <= 70:
                    score += self.WEIGHTS.get("mfi_aligned", 5)
                    reasons.append(f"MFI {current_mfi:.0f} — healthy buy zone")
                elif direction == Signal.SELL and 30 <= current_mfi <= 70:
                    score += self.WEIGHTS.get("mfi_aligned", 5)
                    reasons.append(f"MFI {current_mfi:.0f} — healthy sell zone")

        # ── Fibonacci Level Proximity ──
        if all(col in df.columns for col in ["high", "low", "close"]):
            is_near, fib_info = ci.is_near_fibonacci(
                df["high"], df["low"], df["close"],
                lookback=FIBONACCI_SWING_LOOKBACK,
                proximity_pct=FIBONACCI_PROXIMITY_PCT,
                key_levels=FIBONACCI_KEY_LEVELS,
            )
            if is_near:
                score += self.WEIGHTS.get("fibonacci_level", 7)
                reasons.append(f"Near Fibonacci: {fib_info}")

        return min(score, 100), reasons


# ──────────────────────────────────────────────
# Crypto Analysis Engine
# ──────────────────────────────────────────────

class CryptoAnalysisEngine:
    """
    Core crypto analysis engine for all Binance pairs.
    Extends the ChastiefollAgent logic with crypto-specific adaptations.

    Key Adaptations:
    - No session filter (24/7 trading)
    - Volume-weighted confluence scoring
    - Dynamic FVG thresholds (percentage of price)
    - Volatility-adjusted position sizing hints
    - Works with any CCXT-format symbol (BTC/USDT, ETH/USDT, etc.)

    Usage:
        from Analysis.crypto_engine import CryptoAnalysisEngine
        from Analysis.crypto_pair_config import get_crypto_pair_config

        config = get_crypto_pair_config("BTC/USDT")
        engine = CryptoAnalysisEngine(pair_config=config)

        setup = engine.analyze(df)
        if setup:
            print(f"Signal: {setup.signal} | Entry: {setup.entry}")
    """

    # Default thresholds (loaded from analysis_config.py — user-editable)
    MINIMUM_CONFLUENCE_SCORE = 45
    MINIMUM_RR = 1.5
    MINIMUM_ADX = 18

    def __init__(self, pair_config=None):
        """
        Initialize crypto engine with optional pair config.
        
        Loads thresholds from Analysis/analysis_config.py (user-editable).
        Pair-specific overrides from pair_config take priority.

        Args:
            pair_config: CryptoPairConfig instance. If None, uses config defaults.
        """
        self.pair_config = pair_config
        self.structure_analyzer = MarketStructureAnalyzer()
        self.ti = CryptoIndicators()

        # Load user-editable config
        try:
            from .analysis_config import (
                MIN_CONFLUENCE_SCORE, MIN_RR_RATIO, MIN_ADX,
                ATR_SL_MULTIPLIER, RR_TARGET, CONFLUENCE_WEIGHTS,
                get_category_config,
            )
            self.MINIMUM_CONFLUENCE_SCORE = MIN_CONFLUENCE_SCORE
            self.MINIMUM_RR = MIN_RR_RATIO
            self.MINIMUM_ADX = MIN_ADX
            self._default_atr_sl = ATR_SL_MULTIPLIER
            self._default_rr = RR_TARGET

            # Apply category overrides if pair config has category
            if pair_config and hasattr(pair_config, 'category'):
                cat = pair_config.category.value if hasattr(pair_config.category, 'value') else str(pair_config.category)
                cat_cfg = get_category_config(cat)
                self.MINIMUM_CONFLUENCE_SCORE = cat_cfg.get("min_confluence", MIN_CONFLUENCE_SCORE)
                self.MINIMUM_ADX = cat_cfg.get("min_adx", MIN_ADX)

            # Initialize scorer with user-editable weights
            self.scorer = CryptoConfluenceScorer(weights=CONFLUENCE_WEIGHTS)
        except ImportError:
            self._default_atr_sl = 2.5
            self._default_rr = 2.0
            self.scorer = CryptoConfluenceScorer()

        # Pair-specific thresholds override config (most specific wins)
        if pair_config:
            self.MINIMUM_CONFLUENCE_SCORE = getattr(pair_config, 'min_confluence', self.MINIMUM_CONFLUENCE_SCORE)
            self.MINIMUM_RR = getattr(pair_config, 'min_rr', self.MINIMUM_RR)
            self.MINIMUM_ADX = getattr(pair_config, 'min_adx', self.MINIMUM_ADX)

        symbol = pair_config.symbol if pair_config else "BTC/USDT"
        log.info(f"CryptoAnalysisEngine initialized for {symbol}")

    def analyze(
        self,
        df: pd.DataFrame,
        session_active: bool = True,    # Always True for crypto (24/7)
        atr_multiplier_sl: float = None,
        rr_target: float = None,
    ) -> Optional[TradeSetup]:
        """
        Full analysis pipeline for crypto.
        
        Args:
            df: OHLCV DataFrame (columns: open, high, low, close, volume)
                Minimum 50 bars recommended, 100+ ideal.
            session_active: Always True for crypto (24/7 market)
            atr_multiplier_sl: ATR × N for stop loss distance
            rr_target: Reward:Risk target ratio

        Returns:
            TradeSetup if valid signal found, None otherwise
        """
        # Use pair config defaults, then analysis_config defaults
        if atr_multiplier_sl is None:
            atr_multiplier_sl = getattr(self.pair_config, 'atr_sl_multiplier', None) or getattr(self, '_default_atr_sl', 2.5)
        if rr_target is None:
            rr_target = getattr(self.pair_config, 'rr_target', None) or getattr(self, '_default_rr', 2.0)

        if len(df) < 50:
            return None

        df = df.copy().reset_index(drop=True)

        # Ensure numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        close = df["close"].iloc[-1]
        if close <= 0 or pd.isna(close):
            return None

        # FVG threshold — percentage-based for crypto
        fvg_pct = getattr(self.pair_config, 'min_fvg_pct', 0.05)
        fvg_threshold = close * (fvg_pct / 100) if fvg_pct > 0 else close * 0.0005

        # Market structure analysis
        structure = self.structure_analyzer.detect_structure(df, fvg_min_size=fvg_threshold)
        atr_val = self.ti.atr(df["high"], df["low"], df["close"]).iloc[-1]

        if pd.isna(atr_val) or atr_val <= 0:
            return None

        # ── ADX Filter: Only trade in trending markets ──
        adx_val, plus_di, minus_di = self.ti.adx(df["high"], df["low"], df["close"])
        current_adx = adx_val.iloc[-1] if not pd.isna(adx_val.iloc[-1]) else 0
        if current_adx < self.MINIMUM_ADX:
            return None  # Market is ranging — avoid whipsaws

        # ── Primary Signal: PSAR + EMA alignment ──
        psar = self.ti.parabolic_sar(df["high"], df["low"])
        psar_val = psar.iloc[-1]
        psar_bull = close > psar_val
        psar_bear = close < psar_val

        # EMA 20/50 (primary) and 100/200 (context)
        ema20 = self.ti.ema(df["close"], 20).iloc[-1]
        ema50 = self.ti.ema(df["close"], 50).iloc[-1]

        # Relaxed: EMA20 > EMA50 + price above both
        ema_bull_basic = ema20 > ema50 and close > ema20
        ema_bear_basic = ema20 < ema50 and close < ema20

        # Determine direction
        direction = None

        if psar_bull and ema_bull_basic:
            direction = Signal.BUY
        elif psar_bear and ema_bear_basic:
            direction = Signal.SELL

        # Alternative: PSAR + price vs EMA20 + DI alignment
        if direction is None:
            if psar_bull and close > ema20 and plus_di.iloc[-1] > minus_di.iloc[-1]:
                direction = Signal.BUY
            elif psar_bear and close < ema20 and minus_di.iloc[-1] > plus_di.iloc[-1]:
                direction = Signal.SELL

        if direction is None:
            return None

        # Structure should not strongly contradict
        if (direction == Signal.BUY and structure.bias == Bias.BEARISH) or \
           (direction == Signal.SELL and structure.bias == Bias.BULLISH):
            return None

        # Score confluence (crypto-enhanced)
        conf_score, reasons = self.scorer.score(
            df, direction, structure, session_active
        )

        if conf_score < self.MINIMUM_CONFLUENCE_SCORE:
            return None

        # Calculate entry, SL, TP
        sl_distance = atr_val * atr_multiplier_sl

        if direction == Signal.BUY:
            entry = close
            stop_loss = close - sl_distance
            take_profit = close + sl_distance * rr_target
        else:
            entry = close
            stop_loss = close + sl_distance
            take_profit = close - sl_distance * rr_target

        rr = abs(take_profit - entry) / abs(stop_loss - entry) if abs(stop_loss - entry) > 0 else 0

        if rr < self.MINIMUM_RR:
            return None

        symbol = self.pair_config.symbol if self.pair_config else "CRYPTO"
        decimals = getattr(self.pair_config, 'price_decimals', 2)

        # ── Sanity check: SL must be ≠ entry, TP must be ≠ SL after rounding ──
        # Rounding to price_decimals can collapse SL/TP into entry when price is low
        # (e.g. LDO @ $0.36, sl_distance=0.001 → SL=0.359 → round(0.359,2)=0.36=entry)
        rounded_sl = round(stop_loss, decimals)
        rounded_tp = round(take_profit, decimals)
        if rounded_sl == entry or rounded_tp == entry or rounded_sl == rounded_tp:
            log.warning(f"  ⚠ SL/TP collision after rounding for {symbol} "
                        f"(entry={entry}, SL={rounded_sl}, TP={rounded_tp}) — adjusting")
            # Use entry as TP and add 1-pip buffer to SL
            if direction == Signal.BUY:
                # SL below entry, TP above entry with buffer
                stop_loss = round(entry - sl_distance - (sl_distance * 0.1), decimals)
                take_profit = round(entry + max(sl_distance * 0.5, 0.0001), decimals)
            else:
                stop_loss = round(entry + sl_distance + (sl_distance * 0.1), decimals)
                take_profit = round(entry - max(sl_distance * 0.5, 0.0001), decimals)
            rr = abs(take_profit - entry) / abs(stop_loss - entry) if abs(stop_loss - entry) > 0 else 0

        # Add volatility context to reasons
        vol_pctl = self.ti.volatility_percentile(df["close"])
        if vol_pctl > 80:
            reasons.append(f"High volatility ({vol_pctl:.0f}th pctl) — consider smaller size")
        elif vol_pctl < 20:
            reasons.append(f"Low volatility ({vol_pctl:.0f}th pctl) — breakout potential")

        symbol = self.pair_config.symbol if self.pair_config else "CRYPTO"
        decimals = getattr(self.pair_config, 'price_decimals', 2)

        return TradeSetup(
            signal=direction,
            entry=round(entry, decimals),
            stop_loss=round(stop_loss, decimals),
            take_profit=round(take_profit, decimals),
            rr_ratio=round(rr, 2),
            confidence=round(conf_score / 100, 2),
            confluence=len(reasons),
            reasons=reasons,
            timeframe="H1",
            symbol=symbol,
        )

    def analyze_quick(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Quick analysis — returns a lightweight dict instead of full TradeSetup.
        Useful for scanning many pairs rapidly.
        """
        setup = self.analyze(df)
        if not setup:
            return None

        return {
            "signal": setup.signal.value,
            "entry": setup.entry,
            "stop_loss": setup.stop_loss,
            "take_profit": setup.take_profit,
            "rr_ratio": setup.rr_ratio,
            "confidence": setup.confidence,
            "reasons": setup.reasons[:3],  # Top 3 reasons only
        }

    def get_market_bias(self, df: pd.DataFrame) -> Dict:
        """
        Get overall market bias without generating a trade signal.
        Useful for filtering/screening pairs.
        """
        if len(df) < 50:
            return {"bias": "neutral", "strength": 0}

        df = df.copy().reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        close = df["close"].iloc[-1]
        ema20 = self.ti.ema(df["close"], 20).iloc[-1]
        ema50 = self.ti.ema(df["close"], 50).iloc[-1]
        ema200 = self.ti.ema(df["close"], 200).iloc[-1]

        rsi = self.ti.rsi(df["close"]).iloc[-1]
        adx_val, plus_di, minus_di = self.ti.adx(df["high"], df["low"], df["close"])
        current_adx = adx_val.iloc[-1] if not pd.isna(adx_val.iloc[-1]) else 0

        # Determine bias
        bull_score = 0
        bear_score = 0

        if close > ema20: bull_score += 1
        else: bear_score += 1
        if close > ema50: bull_score += 1
        else: bear_score += 1
        if close > ema200: bull_score += 1
        else: bear_score += 1
        if ema20 > ema50: bull_score += 1
        else: bear_score += 1
        if rsi > 50: bull_score += 1
        else: bear_score += 1
        if not pd.isna(plus_di.iloc[-1]) and plus_di.iloc[-1] > minus_di.iloc[-1]:
            bull_score += 1
        else:
            bear_score += 1

        total = bull_score + bear_score
        if bull_score > bear_score + 1:
            bias = "bullish"
        elif bear_score > bull_score + 1:
            bias = "bearish"
        else:
            bias = "neutral"

        strength = abs(bull_score - bear_score) / total if total > 0 else 0

        return {
            "bias": bias,
            "strength": round(strength, 2),
            "adx": round(current_adx, 1),
            "rsi": round(rsi, 1) if not pd.isna(rsi) else 50,
            "trend_score": bull_score - bear_score,
            "above_ema200": close > ema200,
        }


# ──────────────────────────────────────────────
# Multi-Pair Crypto Scanner
# ──────────────────────────────────────────────

class CryptoScanner:
    """
    Scans multiple crypto pairs for trading signals.
    Optimized for scanning 50-200+ pairs quickly.

    Usage:
        scanner = CryptoScanner()
        
        # Scan multiple pairs
        signals = await scanner.scan_pairs(data_feed, pairs_to_scan)
        for signal in signals:
            print(f"{signal['symbol']}: {signal['signal']} | Conf: {signal['confidence']}")
    """

    def __init__(self, min_confidence: float = 0.50):
        self.min_confidence = min_confidence
        self._engines: Dict[str, CryptoAnalysisEngine] = {}

    def get_engine(self, symbol: str, pair_config=None) -> CryptoAnalysisEngine:
        """Get or create engine for a symbol."""
        if symbol not in self._engines:
            self._engines[symbol] = CryptoAnalysisEngine(pair_config=pair_config)
        return self._engines[symbol]

    def scan(self, pair_data: Dict[str, pd.DataFrame],
             pair_configs: Dict[str, any] = None) -> List[Dict]:
        """
        Scan multiple pairs synchronously.

        Args:
            pair_data: Dict of symbol → OHLCV DataFrame
            pair_configs: Optional dict of symbol → pair config

        Returns:
            List of signal dicts, sorted by confidence (highest first)
        """
        signals = []

        for symbol, df in pair_data.items():
            if df is None or df.empty or len(df) < 50:
                continue

            config = (pair_configs or {}).get(symbol)
            engine = self.get_engine(symbol, config)

            try:
                setup = engine.analyze(df)
                if setup and setup.confidence >= self.min_confidence:
                    signals.append({
                        "symbol": symbol,
                        "signal": setup.signal.value,
                        "entry": setup.entry,
                        "stop_loss": setup.stop_loss,
                        "take_profit": setup.take_profit,
                        "rr_ratio": setup.rr_ratio,
                        "confidence": setup.confidence,
                        "confluence": setup.confluence,
                        "reasons": setup.reasons,
                        "setup": setup,
                    })
            except Exception as e:
                log.warning(f"[Scanner] Error analyzing {symbol}: {e}")
                continue

        # Sort by confidence (highest first)
        signals.sort(key=lambda x: x["confidence"], reverse=True)
        return signals

    def get_market_overview(self, pair_data: Dict[str, pd.DataFrame]) -> Dict:
        """
        Get quick bias overview across all pairs.
        Returns summary of market conditions.
        """
        bullish = 0
        bearish = 0
        neutral = 0
        biases = {}

        for symbol, df in pair_data.items():
            if df is None or df.empty or len(df) < 50:
                continue

            engine = self.get_engine(symbol)
            bias_info = engine.get_market_bias(df)
            biases[symbol] = bias_info

            if bias_info["bias"] == "bullish":
                bullish += 1
            elif bias_info["bias"] == "bearish":
                bearish += 1
            else:
                neutral += 1

        total = bullish + bearish + neutral
        return {
            "total_pairs": total,
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "market_sentiment": "bullish" if bullish > bearish * 1.5 else (
                "bearish" if bearish > bullish * 1.5 else "mixed"
            ),
            "bullish_pct": round(bullish / total * 100, 1) if total > 0 else 0,
            "biases": biases,
        }


# ──────────────────────────────────────────────
# Quick Demo
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Synthetic BTC-like data
    np.random.seed(42)
    n = 200
    prices = 65000 + np.cumsum(np.random.randn(n) * 500)

    df = pd.DataFrame({
        "open": prices + np.random.randn(n) * 100,
        "high": prices + np.abs(np.random.randn(n)) * 800,
        "low": prices - np.abs(np.random.randn(n)) * 800,
        "close": prices,
        "volume": np.abs(np.random.randn(n)) * 100 + 50,
    })

    # Simple config mock
    class MockConfig:
        symbol = "BTC/USDT"
        atr_sl_multiplier = 2.5
        rr_target = 2.0
        min_confluence = 45
        min_rr = 1.5
        min_adx = 18
        min_fvg_pct = 0.05
        price_decimals = 2

    engine = CryptoAnalysisEngine(pair_config=MockConfig())
    setup = engine.analyze(df)

    if setup:
        print(f"\n{'='*50}")
        print(f"  CRYPTO SIGNAL — {setup.symbol}")
        print(f"{'='*50}")
        print(f"  Signal:     {setup.signal.value}")
        print(f"  Entry:      ${setup.entry:,.2f}")
        print(f"  Stop Loss:  ${setup.stop_loss:,.2f}")
        print(f"  Take Profit:${setup.take_profit:,.2f}")
        print(f"  R:R:        {setup.rr_ratio}")
        print(f"  Confidence: {setup.confidence*100:.0f}%")
        print(f"  Reasons:")
        for r in setup.reasons:
            print(f"    ✓ {r}")
    else:
        print("No signal found — conditions not met.")

    # Test market bias
    bias = engine.get_market_bias(df)
    print(f"\n  Market Bias: {bias}")
