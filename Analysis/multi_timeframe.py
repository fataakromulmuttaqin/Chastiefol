"""
Chastiefol — Multi-Timeframe Analysis (MTF)
Analyzes XAUUSD across multiple timeframes simultaneously to confirm
trend direction and improve signal quality.

Timeframe Hierarchy:
  - Higher TF (D1/H4) → Establishes trend direction (bias)
  - Mid TF (H1) → Signal generation (entry/exit triggers)
  - Lower TF (M15/M5) → Entry refinement (precision timing)

Confluence Rule:
  A signal is strongest when ALL timeframes agree on direction.
  Score: 0–100 based on weighted alignment across timeframes.
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from enum import Enum

from .xauusd_engine import (
    TechnicalIndicators,
    MarketStructureAnalyzer,
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
log = logging.getLogger("MTF.Analysis")


# ──────────────────────────────────────────────
# Configuration & Data Models
# ──────────────────────────────────────────────

class Timeframe(str, Enum):
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"


# Weight for each TF in confluence scoring (higher TF = more weight)
DEFAULT_WEIGHTS = {
    Timeframe.M5: 0.05,
    Timeframe.M15: 0.10,
    Timeframe.M30: 0.10,
    Timeframe.H1: 0.20,
    Timeframe.H4: 0.25,
    Timeframe.D1: 0.20,
    Timeframe.W1: 0.10,
}


@dataclass
class MTFConfig:
    """Multi-Timeframe analysis configuration."""
    # Which timeframes to analyze
    timeframes: List[Timeframe] = field(default_factory=lambda: [
        Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1
    ])
    # Weight per timeframe for scoring
    weights: Dict[Timeframe, float] = field(default_factory=lambda: DEFAULT_WEIGHTS.copy())
    # Minimum MTF alignment score to confirm signal (0–100)
    min_alignment_score: float = 60.0
    # EMA periods for trend detection
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    # RSI period
    rsi_period: int = 14
    # ATR period for volatility context
    atr_period: int = 14


@dataclass
class TimeframeData:
    """Analysis result for a single timeframe."""
    timeframe: Timeframe
    bias: Bias = Bias.NEUTRAL
    trend_strength: float = 0.0  # 0–1, how strong the trend
    ema_alignment: str = "mixed"  # "bullish", "bearish", "mixed"
    rsi: float = 50.0
    macd_signal: str = "neutral"  # "bullish", "bearish", "neutral"
    atr: float = 0.0
    structure: Optional[MarketStructure] = None
    price_vs_ema200: str = "above"  # "above", "below", "at"
    key_level_near: bool = False  # near support/resistance
    score: float = 0.0  # -1 to +1 (bearish to bullish)

    @property
    def is_bullish(self) -> bool:
        return self.bias == Bias.BULLISH

    @property
    def is_bearish(self) -> bool:
        return self.bias == Bias.BEARISH


@dataclass
class MTFResult:
    """Combined multi-timeframe analysis result."""
    # Overall
    overall_bias: Bias = Bias.NEUTRAL
    alignment_score: float = 0.0  # 0–100
    confidence: float = 0.0  # 0–1

    # Per-timeframe breakdown
    timeframe_data: Dict[Timeframe, TimeframeData] = field(default_factory=dict)

    # Summary
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0

    # Signal quality
    signal_quality: str = "poor"  # "excellent", "good", "fair", "poor"
    recommended_action: Signal = Signal.HOLD
    reasons: List[str] = field(default_factory=list)

    # Higher TF context
    higher_tf_bias: Bias = Bias.NEUTRAL
    lower_tf_bias: Bias = Bias.NEUTRAL

    def to_dict(self) -> dict:
        return {
            "overall_bias": self.overall_bias.value,
            "alignment_score": round(self.alignment_score, 1),
            "confidence": round(self.confidence, 3),
            "signal_quality": self.signal_quality,
            "recommended_action": self.recommended_action.value,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "higher_tf_bias": self.higher_tf_bias.value,
            "lower_tf_bias": self.lower_tf_bias.value,
            "reasons": self.reasons,
            "timeframes": {
                tf.value: {
                    "bias": data.bias.value,
                    "ema_alignment": data.ema_alignment,
                    "rsi": round(data.rsi, 1),
                    "macd_signal": data.macd_signal,
                    "trend_strength": round(data.trend_strength, 2),
                    "score": round(data.score, 3),
                }
                for tf, data in self.timeframe_data.items()
            },
        }


# ──────────────────────────────────────────────
# Multi-Timeframe Analyzer
# ──────────────────────────────────────────────

class MultiTimeframeAnalyzer:
    """
    Analyzes XAUUSD across multiple timeframes for confluence confirmation.

    Usage:
        config = MTFConfig(timeframes=[Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1])
        mtf = MultiTimeframeAnalyzer(config)

        # Provide OHLCV data for each timeframe
        data = {
            Timeframe.M15: df_m15,
            Timeframe.H1: df_h1,
            Timeframe.H4: df_h4,
            Timeframe.D1: df_d1,
        }
        result = mtf.analyze(data)
        print(result.overall_bias, result.alignment_score, result.signal_quality)
    """

    def __init__(self, config: MTFConfig = None):
        self.config = config or MTFConfig()
        self.ti = TechnicalIndicators()
        self.msa = MarketStructureAnalyzer()

        log.info(f"MTF Analyzer initialized | "
                 f"Timeframes: {[tf.value for tf in self.config.timeframes]} | "
                 f"Min alignment: {self.config.min_alignment_score}%")

    def analyze(self, data: Dict[Timeframe, pd.DataFrame]) -> MTFResult:
        """
        Perform multi-timeframe analysis on provided OHLCV data.

        Args:
            data: Dict mapping Timeframe → OHLCV DataFrame
                  DataFrame must have columns: open, high, low, close, volume

        Returns:
            MTFResult with overall bias, alignment score, and per-TF breakdown
        """
        result = MTFResult()
        reasons = []

        # Analyze each timeframe
        for tf in self.config.timeframes:
            if tf not in data or data[tf] is None or data[tf].empty:
                log.warning(f"[MTF] No data for {tf.value}, skipping.")
                continue

            df = data[tf]
            if len(df) < 50:
                log.warning(f"[MTF] Insufficient data for {tf.value} ({len(df)} bars)")
                continue

            tf_data = self._analyze_timeframe(tf, df)
            result.timeframe_data[tf] = tf_data

        if not result.timeframe_data:
            log.warning("[MTF] No timeframes could be analyzed.")
            return result

        # Aggregate results
        self._calculate_alignment(result)
        self._determine_quality(result, reasons)
        result.reasons = reasons

        log.info(f"[MTF] Result: {result.overall_bias.value} | "
                 f"Score: {result.alignment_score:.0f}% | "
                 f"Quality: {result.signal_quality} | "
                 f"Action: {result.recommended_action.value}")

        return result

    def _analyze_timeframe(self, tf: Timeframe, df: pd.DataFrame) -> TimeframeData:
        """Analyze a single timeframe's data."""
        td = TimeframeData(timeframe=tf)
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ── EMA Analysis ──
        ema_fast = self.ti.ema(close, self.config.ema_fast)
        ema_slow = self.ti.ema(close, self.config.ema_slow)
        ema_trend = self.ti.ema(close, self.config.ema_trend)

        current_price = close.iloc[-1]
        ema_f = ema_fast.iloc[-1]
        ema_s = ema_slow.iloc[-1]
        ema_t = ema_trend.iloc[-1]

        # EMA alignment
        if ema_f > ema_s > ema_t:
            td.ema_alignment = "bullish"
        elif ema_f < ema_s < ema_t:
            td.ema_alignment = "bearish"
        else:
            td.ema_alignment = "mixed"

        # Price vs EMA200
        if current_price > ema_t * 1.001:
            td.price_vs_ema200 = "above"
        elif current_price < ema_t * 0.999:
            td.price_vs_ema200 = "below"
        else:
            td.price_vs_ema200 = "at"

        # ── RSI ──
        rsi = self.ti.rsi(close, self.config.rsi_period)
        td.rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        # ── MACD ──
        macd_line, signal_line, histogram = self.ti.macd(close)
        if not pd.isna(histogram.iloc[-1]):
            if histogram.iloc[-1] > 0 and macd_line.iloc[-1] > signal_line.iloc[-1]:
                td.macd_signal = "bullish"
            elif histogram.iloc[-1] < 0 and macd_line.iloc[-1] < signal_line.iloc[-1]:
                td.macd_signal = "bearish"
            else:
                td.macd_signal = "neutral"

        # ── ATR (volatility context) ──
        atr = self.ti.atr(high, low, close, self.config.atr_period)
        td.atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0

        # ── Market Structure (SMC) ──
        try:
            td.structure = self.msa.detect_structure(df)
        except Exception:
            td.structure = None

        # ── Determine Bias ──
        bull_factors = 0
        bear_factors = 0

        if td.ema_alignment == "bullish":
            bull_factors += 2
        elif td.ema_alignment == "bearish":
            bear_factors += 2

        if td.price_vs_ema200 == "above":
            bull_factors += 1
        elif td.price_vs_ema200 == "below":
            bear_factors += 1

        if td.rsi > 55:
            bull_factors += 1
        elif td.rsi < 45:
            bear_factors += 1

        if td.macd_signal == "bullish":
            bull_factors += 1
        elif td.macd_signal == "bearish":
            bear_factors += 1

        if td.structure:
            if td.structure.bias == Bias.BULLISH:
                bull_factors += 2
            elif td.structure.bias == Bias.BEARISH:
                bear_factors += 2

        # Score & bias
        total = bull_factors + bear_factors
        if total > 0:
            td.score = (bull_factors - bear_factors) / max(total, 1)
        else:
            td.score = 0.0

        td.trend_strength = abs(td.score)

        if bull_factors > bear_factors + 1:
            td.bias = Bias.BULLISH
        elif bear_factors > bull_factors + 1:
            td.bias = Bias.BEARISH
        else:
            td.bias = Bias.NEUTRAL

        return td

    def _calculate_alignment(self, result: MTFResult):
        """Calculate overall alignment score from all timeframes."""
        weighted_score = 0.0
        total_weight = 0.0

        bull_count = 0
        bear_count = 0
        neutral_count = 0

        higher_tfs = [Timeframe.H4, Timeframe.D1, Timeframe.W1]
        lower_tfs = [Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1]

        higher_scores = []
        lower_scores = []

        for tf, td in result.timeframe_data.items():
            weight = self.config.weights.get(tf, 0.1)
            weighted_score += td.score * weight
            total_weight += weight

            if td.bias == Bias.BULLISH:
                bull_count += 1
            elif td.bias == Bias.BEARISH:
                bear_count += 1
            else:
                neutral_count += 1

            if tf in higher_tfs:
                higher_scores.append(td.score)
            if tf in lower_tfs:
                lower_scores.append(td.score)

        result.bullish_count = bull_count
        result.bearish_count = bear_count
        result.neutral_count = neutral_count

        # Normalize score to 0–100 alignment
        if total_weight > 0:
            normalized = weighted_score / total_weight  # -1 to +1
            result.alignment_score = abs(normalized) * 100
            result.confidence = abs(normalized)

            if normalized > 0.1:
                result.overall_bias = Bias.BULLISH
            elif normalized < -0.1:
                result.overall_bias = Bias.BEARISH
            else:
                result.overall_bias = Bias.NEUTRAL

        # Higher TF bias
        if higher_scores:
            avg_higher = np.mean(higher_scores)
            if avg_higher > 0.2:
                result.higher_tf_bias = Bias.BULLISH
            elif avg_higher < -0.2:
                result.higher_tf_bias = Bias.BEARISH
            else:
                result.higher_tf_bias = Bias.NEUTRAL

        # Lower TF bias
        if lower_scores:
            avg_lower = np.mean(lower_scores)
            if avg_lower > 0.2:
                result.lower_tf_bias = Bias.BULLISH
            elif avg_lower < -0.2:
                result.lower_tf_bias = Bias.BEARISH
            else:
                result.lower_tf_bias = Bias.NEUTRAL

    def _determine_quality(self, result: MTFResult, reasons: List[str]):
        """Determine signal quality and recommended action."""
        score = result.alignment_score
        total_tfs = len(result.timeframe_data)
        agree_pct = max(result.bullish_count, result.bearish_count) / max(total_tfs, 1)

        # Quality classification
        if score >= 80 and agree_pct >= 0.8:
            result.signal_quality = "excellent"
            reasons.append(f"Strong alignment ({score:.0f}%) across {total_tfs} timeframes")
        elif score >= 60 and agree_pct >= 0.6:
            result.signal_quality = "good"
            reasons.append(f"Good alignment ({score:.0f}%) — majority of TFs agree")
        elif score >= 40:
            result.signal_quality = "fair"
            reasons.append(f"Fair alignment ({score:.0f}%) — mixed signals across TFs")
        else:
            result.signal_quality = "poor"
            reasons.append(f"Weak alignment ({score:.0f}%) — conflicting TF signals")

        # Higher TF context
        if result.higher_tf_bias == Bias.BULLISH:
            reasons.append("Higher TF (H4/D1) bias: BULLISH — trade with trend")
        elif result.higher_tf_bias == Bias.BEARISH:
            reasons.append("Higher TF (H4/D1) bias: BEARISH — trade with trend")
        else:
            reasons.append("Higher TF bias: NEUTRAL — exercise caution")

        # Recommended action
        if score >= self.config.min_alignment_score:
            if result.overall_bias == Bias.BULLISH:
                result.recommended_action = Signal.BUY
                reasons.append("MTF confirms BUY — alignment above threshold")
            elif result.overall_bias == Bias.BEARISH:
                result.recommended_action = Signal.SELL
                reasons.append("MTF confirms SELL — alignment above threshold")
            else:
                result.recommended_action = Signal.HOLD
                reasons.append("Neutral bias — no clear direction")
        else:
            result.recommended_action = Signal.HOLD
            reasons.append(f"Alignment ({score:.0f}%) below threshold ({self.config.min_alignment_score}%) — HOLD")

        # Divergence warning (higher vs lower TF)
        if (result.higher_tf_bias == Bias.BULLISH and result.lower_tf_bias == Bias.BEARISH):
            reasons.append("⚠️ DIVERGENCE: Higher TF bullish but lower TF bearish — pullback likely")
        elif (result.higher_tf_bias == Bias.BEARISH and result.lower_tf_bias == Bias.BULLISH):
            reasons.append("⚠️ DIVERGENCE: Higher TF bearish but lower TF bullish — counter-trend bounce")

    # ──────────────────────────────────────────
    # Helper: Resample OHLCV to higher timeframes
    # ──────────────────────────────────────────

    @staticmethod
    def resample_ohlcv(df: pd.DataFrame, source_tf: Timeframe,
                       target_tf: Timeframe) -> Optional[pd.DataFrame]:
        """
        Resample OHLCV data from a lower timeframe to a higher one.
        Useful when you only have M15 data but need H1/H4 bars.

        Args:
            df: Source OHLCV DataFrame with DatetimeIndex or 'timestamp' column
            source_tf: Source timeframe
            target_tf: Target (higher) timeframe

        Returns:
            Resampled DataFrame or None if incompatible
        """
        # Pandas resample rules
        resample_map = {
            Timeframe.M5: "5min",
            Timeframe.M15: "15min",
            Timeframe.M30: "30min",
            Timeframe.H1: "1h",
            Timeframe.H4: "4h",
            Timeframe.D1: "1D",
            Timeframe.W1: "1W",
        }

        target_rule = resample_map.get(target_tf)
        if not target_rule:
            return None

        # Ensure datetime index
        temp_df = df.copy()
        if "timestamp" in temp_df.columns:
            temp_df["timestamp"] = pd.to_datetime(temp_df["timestamp"])
            temp_df = temp_df.set_index("timestamp")
        elif not isinstance(temp_df.index, pd.DatetimeIndex):
            # Cannot resample without datetime
            return None

        try:
            resampled = temp_df.resample(target_rule).agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()

            return resampled.reset_index() if "timestamp" in df.columns else resampled
        except Exception as e:
            log.warning(f"Resample {source_tf.value}→{target_tf.value} failed: {e}")
            return None

    @staticmethod
    def generate_synthetic_mtf(base_df: pd.DataFrame) -> Dict[Timeframe, pd.DataFrame]:
        """
        Generate multi-timeframe data from a single base DataFrame.
        Assumes base_df is M15 or H1 data and resamples to higher TFs.
        Useful for testing when you only have one timeframe of data.

        Args:
            base_df: OHLCV DataFrame (assumes minute or hourly data)

        Returns:
            Dict of Timeframe → DataFrame for testing
        """
        result = {}

        # Use base as H1
        result[Timeframe.H1] = base_df.copy()

        # Simulate lower TF (just use same data for testing)
        result[Timeframe.M15] = base_df.copy()

        # Resample to H4 (every 4 rows)
        if len(base_df) >= 20:
            h4_data = []
            for i in range(0, len(base_df) - 3, 4):
                chunk = base_df.iloc[i:i+4]
                h4_data.append({
                    "open": chunk["open"].iloc[0],
                    "high": chunk["high"].max(),
                    "low": chunk["low"].min(),
                    "close": chunk["close"].iloc[-1],
                    "volume": chunk["volume"].sum() if "volume" in chunk else 0,
                })
            result[Timeframe.H4] = pd.DataFrame(h4_data)

        # Resample to D1 (every 24 rows for H1 base)
        if len(base_df) >= 48:
            d1_data = []
            for i in range(0, len(base_df) - 23, 24):
                chunk = base_df.iloc[i:i+24]
                d1_data.append({
                    "open": chunk["open"].iloc[0],
                    "high": chunk["high"].max(),
                    "low": chunk["low"].min(),
                    "close": chunk["close"].iloc[-1],
                    "volume": chunk["volume"].sum() if "volume" in chunk else 0,
                })
            result[Timeframe.D1] = pd.DataFrame(d1_data)

        return result


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import time

    # Generate synthetic test data
    np.random.seed(42)
    n = 200
    prices = 2350 + np.cumsum(np.random.randn(n) * 2.0)

    base_df = pd.DataFrame({
        "open": prices + np.random.randn(n) * 0.3,
        "high": prices + np.abs(np.random.randn(n)) * 4,
        "low": prices - np.abs(np.random.randn(n)) * 4,
        "close": prices,
        "volume": np.abs(np.random.randn(n)) * 1000 + 500,
    })

    # Create MTF data
    mtf_data = MultiTimeframeAnalyzer.generate_synthetic_mtf(base_df)

    # Analyze
    config = MTFConfig(
        timeframes=[Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1],
        min_alignment_score=60.0,
    )
    analyzer = MultiTimeframeAnalyzer(config)
    result = analyzer.analyze(mtf_data)

    # Print results
    print(f"\n{'='*55}")
    print(f"  MULTI-TIMEFRAME ANALYSIS RESULT")
    print(f"{'='*55}")
    print(f"  Overall Bias:     {result.overall_bias.value}")
    print(f"  Alignment Score:  {result.alignment_score:.1f}%")
    print(f"  Confidence:       {result.confidence:.2f}")
    print(f"  Signal Quality:   {result.signal_quality}")
    print(f"  Recommendation:   {result.recommended_action.value}")
    print(f"  Higher TF Bias:   {result.higher_tf_bias.value}")
    print(f"  Lower TF Bias:    {result.lower_tf_bias.value}")
    print(f"  Bull/Bear/Neut:   {result.bullish_count}/{result.bearish_count}/{result.neutral_count}")
    print(f"\n  Per-Timeframe:")
    for tf, td in result.timeframe_data.items():
        print(f"    {tf.value:4s}: {td.bias.value:8s} | EMA: {td.ema_alignment:8s} | "
              f"RSI: {td.rsi:5.1f} | MACD: {td.macd_signal:8s} | Score: {td.score:+.2f}")
    print(f"\n  Reasons:")
    for r in result.reasons:
        print(f"    • {r}")
    print(f"{'='*55}\n")
