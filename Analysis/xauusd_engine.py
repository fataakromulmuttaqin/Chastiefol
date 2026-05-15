"""
Chastiefol — XAUUSD Analysis Engine
Technical indicators, market structure detection, and multi-timeframe confluence.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ──────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────

class Bias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class Signal(Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSetup:
    signal:      Signal
    entry:       float
    stop_loss:   float
    take_profit: float
    rr_ratio:    float
    confidence:  float        # 0.0 – 1.0
    confluence:  int          # count of confirming factors
    reasons:     list[str] = field(default_factory=list)
    timeframe:   str = "H1"

    @property
    def risk_pips(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def reward_pips(self) -> float:
        return abs(self.take_profit - self.entry)


@dataclass
class MarketStructure:
    bias:           Bias
    last_high:      float
    last_low:       float
    bos_detected:   bool = False   # Break of Structure
    choch_detected: bool = False   # Change of Character
    order_blocks:   list[dict] = field(default_factory=list)
    fair_value_gaps: list[dict] = field(default_factory=list)


# ──────────────────────────────────────────────
# Technical Indicators
# ──────────────────────────────────────────────

class TechnicalIndicators:
    """Pure-function technical indicator library for XAUUSD."""

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(series: pd.Series,
             fast: int = 12, slow: int = 26, signal: int = 9
             ) -> tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast   = TechnicalIndicators.ema(series, fast)
        ema_slow   = TechnicalIndicators.ema(series, slow)
        macd_line  = ema_fast - ema_slow
        signal_line = TechnicalIndicators.ema(macd_line, signal)
        histogram  = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(series: pd.Series,
                        period: int = 20, std_dev: float = 2.0
                        ) -> tuple[pd.Series, pd.Series, pd.Series]:
        mid   = series.rolling(period).mean()
        std   = series.rolling(period).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        return upper, mid, lower

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series,
            period: int = 14) -> pd.Series:
        hl  = high - low
        hc  = (high - close.shift()).abs()
        lc  = (low  - close.shift()).abs()
        tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                   k_period: int = 14, d_period: int = 3
                   ) -> tuple[pd.Series, pd.Series]:
        lowest  = low.rolling(k_period).min()
        highest = high.rolling(k_period).max()
        k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
        d = k.rolling(d_period).mean()
        return k, d

    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
             volume: pd.Series) -> pd.Series:
        typical = (high + low + close) / 3
        return (typical * volume).cumsum() / volume.cumsum()

    @staticmethod
    def pivot_points(high: float, low: float, close: float
                     ) -> dict[str, float]:
        pp = (high + low + close) / 3
        return {
            "PP": pp,
            "R1": 2 * pp - low,
            "R2": pp + (high - low),
            "R3": high + 2 * (pp - low),
            "S1": 2 * pp - high,
            "S2": pp - (high - low),
            "S3": low - 2 * (high - pp),
        }


# ──────────────────────────────────────────────
# Market Structure Detection (SMC / ICT concepts)
# ──────────────────────────────────────────────

class MarketStructureAnalyzer:
    """
    Smart Money Concepts analysis:
    - Higher Highs / Higher Lows (bullish) vs Lower Highs / Lower Lows (bearish)
    - Break of Structure (BOS)
    - Change of Character (CHoCH)
    - Order Block detection
    - Fair Value Gap (FVG) detection
    """

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def find_swing_points(self, df: pd.DataFrame
                          ) -> tuple[pd.Series, pd.Series]:
        """Identify swing highs and lows using local extrema."""
        n = 3  # bars each side for swing point confirmation
        swing_highs = pd.Series(np.nan, index=df.index)
        swing_lows  = pd.Series(np.nan, index=df.index)

        for i in range(n, len(df) - n):
            window_h = df["high"].iloc[i-n : i+n+1]
            window_l = df["low"].iloc[i-n : i+n+1]
            if df["high"].iloc[i] == window_h.max():
                swing_highs.iloc[i] = df["high"].iloc[i]
            if df["low"].iloc[i] == window_l.min():
                swing_lows.iloc[i] = df["low"].iloc[i]

        return swing_highs, swing_lows

    def detect_structure(self, df: pd.DataFrame) -> MarketStructure:
        swing_highs, swing_lows = self.find_swing_points(df)

        valid_highs = swing_highs.dropna().tail(4)
        valid_lows  = swing_lows.dropna().tail(4)

        last_high = valid_highs.iloc[-1] if len(valid_highs) else df["high"].iloc[-1]
        last_low  = valid_lows.iloc[-1]  if len(valid_lows)  else df["low"].iloc[-1]

        # Determine bias from swing structure
        bias = Bias.NEUTRAL
        bos  = False
        choch = False

        if len(valid_highs) >= 2 and len(valid_lows) >= 2:
            hh = valid_highs.iloc[-1] > valid_highs.iloc[-2]
            hl = valid_lows.iloc[-1]  > valid_lows.iloc[-2]
            lh = valid_highs.iloc[-1] < valid_highs.iloc[-2]
            ll = valid_lows.iloc[-1]  < valid_lows.iloc[-2]

            if hh and hl:
                bias = Bias.BULLISH
            elif lh and ll:
                bias = Bias.BEARISH

            # BOS: price breaks a significant swing high/low
            current_close = df["close"].iloc[-1]
            if current_close > valid_highs.iloc[-2]:
                bos = True
            elif current_close < valid_lows.iloc[-2]:
                bos = True

            # CHoCH: structure flip
            if bias == Bias.BULLISH and ll:
                choch = True
            elif bias == Bias.BEARISH and hh:
                choch = True

        order_blocks    = self._find_order_blocks(df, bias)
        fair_value_gaps = self._find_fvg(df)

        return MarketStructure(
            bias=bias,
            last_high=last_high,
            last_low=last_low,
            bos_detected=bos,
            choch_detected=choch,
            order_blocks=order_blocks,
            fair_value_gaps=fair_value_gaps,
        )

    def _find_order_blocks(self, df: pd.DataFrame, bias: Bias,
                           lookback: int = 30) -> list[dict]:
        """
        Order block: the last opposing candle before a strong impulsive move.
        Bullish OB = last bearish candle before bullish impulse.
        Bearish OB = last bullish candle before bearish impulse.
        """
        blocks = []
        data   = df.tail(lookback).reset_index(drop=True)

        for i in range(2, len(data) - 1):
            candle = data.iloc[i]
            next_c = data.iloc[i + 1]
            body   = abs(candle["close"] - candle["open"])
            spread = candle["high"] - candle["low"]
            if spread == 0:
                continue

            is_impulse = body / spread > 0.6  # strong body

            # Bullish OB
            if (bias == Bias.BULLISH
                    and candle["close"] < candle["open"]   # bearish candle
                    and next_c["close"] > next_c["open"]   # followed by bullish
                    and is_impulse):
                blocks.append({
                    "type":   "bullish",
                    "top":    candle["open"],
                    "bottom": candle["close"],
                    "index":  i,
                })

            # Bearish OB
            elif (bias == Bias.BEARISH
                    and candle["close"] > candle["open"]   # bullish candle
                    and next_c["close"] < next_c["open"]   # followed by bearish
                    and is_impulse):
                blocks.append({
                    "type":   "bearish",
                    "top":    candle["close"],
                    "bottom": candle["open"],
                    "index":  i,
                })

        return blocks[-3:] if blocks else []  # most recent 3

    def _find_fvg(self, df: pd.DataFrame, lookback: int = 30) -> list[dict]:
        """
        Fair Value Gap: 3-candle pattern where candle[i+2].low > candle[i].high
        (bullish FVG) or candle[i+2].high < candle[i].low (bearish FVG).
        """
        gaps = []
        data = df.tail(lookback).reset_index(drop=True)

        for i in range(len(data) - 2):
            c0, c2 = data.iloc[i], data.iloc[i + 2]

            if c2["low"] > c0["high"]:          # bullish FVG
                gaps.append({
                    "type":   "bullish",
                    "top":    c2["low"],
                    "bottom": c0["high"],
                    "index":  i + 1,
                    "size":   c2["low"] - c0["high"],
                })
            elif c2["high"] < c0["low"]:         # bearish FVG
                gaps.append({
                    "type":   "bearish",
                    "top":    c0["low"],
                    "bottom": c2["high"],
                    "index":  i + 1,
                    "size":   c0["low"] - c2["high"],
                })

        # Return significant gaps only (size > 0.5 USD for gold)
        return [g for g in gaps if g["size"] > 0.5][-5:]


# ──────────────────────────────────────────────
# Confluence Scorer
# ──────────────────────────────────────────────

class ConfluenceScorer:
    """
    Scores each potential trade setup from 0-100 by counting
    confirming technical factors across multiple timeframes.
    """

    WEIGHTS = {
        "market_structure_aligned": 20,
        "ema_stack":                15,
        "rsi_zone":                 12,
        "macd_aligned":             10,
        "order_block_proximity":    15,
        "fvg_in_range":             10,
        "session_active":           10,
        "bos_confirmed":            8,
    }

    def score(self, df: pd.DataFrame, direction: Signal,
              structure: MarketStructure, session_active: bool
              ) -> tuple[int, list[str]]:
        """Returns (score 0-100, list of reason strings)."""
        score   = 0
        reasons = []
        close   = df["close"].iloc[-1]
        ti      = TechnicalIndicators()

        # 1. Market structure alignment
        if (direction == Signal.BUY  and structure.bias == Bias.BULLISH) or \
           (direction == Signal.SELL and structure.bias == Bias.BEARISH):
            score += self.WEIGHTS["market_structure_aligned"]
            reasons.append(f"Structure {structure.bias.value} aligns with {direction.value}")

        # 2. EMA Stack (9 > 21 > 50 for bull, inverse for bear)
        ema9  = ti.ema(df["close"], 9).iloc[-1]
        ema21 = ti.ema(df["close"], 21).iloc[-1]
        ema50 = ti.ema(df["close"], 50).iloc[-1]
        bull_stack = ema9 > ema21 > ema50
        bear_stack = ema9 < ema21 < ema50

        if (direction == Signal.BUY  and bull_stack) or \
           (direction == Signal.SELL and bear_stack):
            score += self.WEIGHTS["ema_stack"]
            reasons.append("EMA 9/21/50 stack confirmed")

        # 3. RSI zone (not overbought for buys, not oversold for sells)
        rsi = ti.rsi(df["close"]).iloc[-1]
        if direction == Signal.BUY  and 40 <= rsi <= 65:
            score += self.WEIGHTS["rsi_zone"]
            reasons.append(f"RSI {rsi:.1f} in buy zone (40-65)")
        elif direction == Signal.SELL and 35 <= rsi <= 60:
            score += self.WEIGHTS["rsi_zone"]
            reasons.append(f"RSI {rsi:.1f} in sell zone (35-60)")

        # 4. MACD alignment
        macd_line, sig_line, hist = ti.macd(df["close"])
        if direction == Signal.BUY  and hist.iloc[-1] > 0 and hist.iloc[-1] > hist.iloc[-2]:
            score += self.WEIGHTS["macd_aligned"]
            reasons.append("MACD histogram bullish and rising")
        elif direction == Signal.SELL and hist.iloc[-1] < 0 and hist.iloc[-1] < hist.iloc[-2]:
            score += self.WEIGHTS["macd_aligned"]
            reasons.append("MACD histogram bearish and falling")

        # 5. Order block proximity
        for ob in structure.order_blocks:
            if direction == Signal.BUY and ob["type"] == "bullish":
                if ob["bottom"] <= close <= ob["top"] * 1.002:
                    score += self.WEIGHTS["order_block_proximity"]
                    reasons.append(f"Price at bullish OB ({ob['bottom']:.2f}-{ob['top']:.2f})")
                    break
            elif direction == Signal.SELL and ob["type"] == "bearish":
                if ob["bottom"] * 0.998 <= close <= ob["top"]:
                    score += self.WEIGHTS["order_block_proximity"]
                    reasons.append(f"Price at bearish OB ({ob['bottom']:.2f}-{ob['top']:.2f})")
                    break

        # 6. FVG in range
        for fvg in structure.fair_value_gaps:
            if direction == Signal.BUY and fvg["type"] == "bullish":
                if fvg["bottom"] <= close <= fvg["top"]:
                    score += self.WEIGHTS["fvg_in_range"]
                    reasons.append(f"Price filling bullish FVG ({fvg['bottom']:.2f}-{fvg['top']:.2f})")
                    break
            elif direction == Signal.SELL and fvg["type"] == "bearish":
                if fvg["bottom"] <= close <= fvg["top"]:
                    score += self.WEIGHTS["fvg_in_range"]
                    reasons.append(f"Price filling bearish FVG ({fvg['bottom']:.2f}-{fvg['top']:.2f})")
                    break

        # 7. Session filter
        if session_active:
            score += self.WEIGHTS["session_active"]
            reasons.append("Active trading session (London/NY)")

        # 8. BOS confirmed
        if structure.bos_detected:
            score += self.WEIGHTS["bos_confirmed"]
            reasons.append("Break of Structure confirmed")

        return min(score, 100), reasons


# ──────────────────────────────────────────────
# Main Signal Agent
# ──────────────────────────────────────────────

class ChastiefollAgent:
    """
    Core Chastiefol trading agent.
    Combines technical analysis, SMC structure, and confluence scoring
    to produce actionable trade setups for XAUUSD.
    """

    MINIMUM_CONFLUENCE_SCORE = 55   # only take trades ≥ 55/100
    MINIMUM_RR = 1.5                # minimum reward:risk ratio

    def __init__(self):
        self.structure_analyzer = MarketStructureAnalyzer()
        self.scorer             = ConfluenceScorer()
        self.ti                 = TechnicalIndicators()

    def analyze(self, df: pd.DataFrame,
                session_active: bool = True,
                atr_multiplier_sl: float = 1.5,
                rr_target: float = 2.0) -> Optional[TradeSetup]:
        """
        Full analysis pipeline → TradeSetup or None.

        df must have columns: open, high, low, close, volume
        Minimum 100 bars recommended.
        """
        if len(df) < 50:
            return None

        df = df.copy().reset_index(drop=True)
        structure = self.structure_analyzer.detect_structure(df)
        close     = df["close"].iloc[-1]
        atr_val   = self.ti.atr(df["high"], df["low"], df["close"]).iloc[-1]

        # Determine candidate direction from structure
        if structure.bias == Bias.BULLISH:
            direction = Signal.BUY
        elif structure.bias == Bias.BEARISH:
            direction = Signal.SELL
        else:
            return None  # No clear bias → no trade

        # Score confluence
        conf_score, reasons = self.scorer.score(
            df, direction, structure, session_active
        )

        if conf_score < self.MINIMUM_CONFLUENCE_SCORE:
            return None  # Not enough confirmation

        # Calculate entry, SL, TP
        sl_distance = atr_val * atr_multiplier_sl

        if direction == Signal.BUY:
            entry       = close
            stop_loss   = close - sl_distance
            take_profit = close + sl_distance * rr_target
        else:
            entry       = close
            stop_loss   = close + sl_distance
            take_profit = close - sl_distance * rr_target

        rr = abs(take_profit - entry) / abs(stop_loss - entry)

        if rr < self.MINIMUM_RR:
            return None  # Poor R:R ratio

        return TradeSetup(
            signal=direction,
            entry=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            rr_ratio=round(rr, 2),
            confidence=round(conf_score / 100, 2),
            confluence=len(reasons),
            reasons=reasons,
        )


# ──────────────────────────────────────────────
# Quick demo
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Synthetic XAUUSD data for testing
    np.random.seed(42)
    n = 200
    prices = 2000 + np.cumsum(np.random.randn(n) * 3)

    df = pd.DataFrame({
        "open":   prices + np.random.randn(n) * 0.5,
        "high":   prices + np.abs(np.random.randn(n)) * 4,
        "low":    prices - np.abs(np.random.randn(n)) * 4,
        "close":  prices,
        "volume": np.abs(np.random.randn(n)) * 1000 + 500,
    })

    agent  = ChastiefollAgent()
    setup  = agent.analyze(df, session_active=True)

    if setup:
        print(f"\n{'='*50}")
        print(f"  CHASTIEFOL SIGNAL — XAUUSD")
        print(f"{'='*50}")
        print(f"  Signal:     {setup.signal.value}")
        print(f"  Entry:      ${setup.entry}")
        print(f"  Stop Loss:  ${setup.stop_loss}")
        print(f"  Take Profit:${setup.take_profit}")
        print(f"  R:R Ratio:  {setup.rr_ratio}")
        print(f"  Confidence: {setup.confidence*100:.0f}%")
        print(f"  Confluence: {setup.confluence} factors")
        print(f"\n  Reasons:")
        for r in setup.reasons:
            print(f"    ✓ {r}")
    else:
        print("No valid setup found — conditions not met.")
