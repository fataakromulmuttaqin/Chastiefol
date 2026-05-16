"""
Chastiefol — Trading Memory (Pattern Recognition & Experience Database)
Higher-level memory that analyzes trade history to detect recurring patterns,
winning/losing conditions, and market regime behavior.

This goes beyond individual lessons — it finds STATISTICAL patterns:
- "BTC/USDT BUY signals with confidence > 70% win 78% of the time"
- "MEME coins lose more often during Asia hours"
- "3 consecutive losses → next trade has 62% chance of winning"
- "Trades held > 4 hours perform 40% better than quick scalps"

The LLM uses this context to make better decisions.

Architecture:
    LessonsManager (lessons.py)     → Individual rules & observations
    TradingMemory (this file)       → Statistical patterns & meta-analysis
    LLM Prompts (prompts.py)        → Injects both into agent context
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("LLM.TradingMemory")


DATA_DIR = Path(__file__).parent / "data"


# ──────────────────────────────────────────────
# Pattern Models
# ──────────────────────────────────────────────

@dataclass
class TradingPattern:
    """A statistically detected trading pattern."""
    pattern_id: str
    description: str
    category: str           # "entry", "exit", "timing", "risk", "pair", "streak"
    win_rate: float         # 0-1
    sample_size: int        # How many trades support this
    avg_pnl: float          # Average P&L when pattern applies
    confidence: float       # Statistical confidence
    actionable_rule: str    # What to DO based on this pattern
    last_updated: str = ""
    symbols: List[str] = field(default_factory=list)


@dataclass
class MarketRegime:
    """Detected market condition/regime."""
    regime_type: str        # "trending", "ranging", "volatile", "quiet"
    description: str
    win_rate_in_regime: float
    avg_pnl_in_regime: float
    best_strategy: str
    worst_strategy: str
    sample_size: int


# ──────────────────────────────────────────────
# Trading Memory
# ──────────────────────────────────────────────

class TradingMemory:
    """
    Analyzes trade history to extract statistical patterns and insights.
    
    This is the "experience database" — it looks at ALL past trades and finds:
    1. Which conditions produce wins vs losses
    2. Which pairs perform best at which times
    3. How confidence correlates with actual outcomes
    4. Streak patterns (when to pause, when to push)
    5. Duration effects (scalp vs swing performance)
    
    Usage:
        from LLM.lessons import LessonsManager
        from LLM.trading_memory import TradingMemory
        
        lessons = LessonsManager()
        memory = TradingMemory(lessons)
        
        # After accumulating trades, analyze patterns
        patterns = memory.analyze_patterns()
        
        # Get context for prompt injection
        context = memory.format_for_prompt(symbol="BTC/USDT")
        
        # Check if a proposed trade matches known patterns
        assessment = memory.assess_setup(symbol="ETH/USDT", direction="buy", confidence=0.65)
    """

    def __init__(self, lessons_manager=None):
        self._data_dir = DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._patterns_file = self._data_dir / "patterns.json"
        self._lessons = lessons_manager
        
        # Cache
        self._patterns: List[Dict] = []
        self._load_patterns()

    def _load_patterns(self):
        """Load cached patterns from file."""
        if self._patterns_file.exists():
            try:
                self._patterns = json.loads(
                    self._patterns_file.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, IOError):
                self._patterns = []

    def _save_patterns(self):
        """Persist patterns to file."""
        self._patterns_file.write_text(
            json.dumps(self._patterns, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def _get_trades(self) -> List[Dict]:
        """Get trade records from lessons manager."""
        if self._lessons:
            return self._lessons.load_performance()
        return []

    # ──────────────────────────────────────────
    # Pattern Analysis
    # ──────────────────────────────────────────

    def analyze_patterns(self) -> List[Dict]:
        """
        Run full pattern analysis on trade history.
        Call periodically (e.g. after every 5 closed trades).
        
        Returns list of detected patterns.
        """
        trades = self._get_trades()
        if len(trades) < 5:
            return []

        patterns = []

        # 1. Confidence → Win Rate correlation
        patterns.extend(self._analyze_confidence_bands(trades))

        # 2. Per-symbol performance
        patterns.extend(self._analyze_by_symbol(trades))

        # 3. Direction bias
        patterns.extend(self._analyze_direction_bias(trades))

        # 4. Duration effects
        patterns.extend(self._analyze_duration_effects(trades))

        # 5. Streak patterns
        patterns.extend(self._analyze_streaks(trades))

        # 6. Close reason analysis
        patterns.extend(self._analyze_close_reasons(trades))

        # 7. Category performance
        patterns.extend(self._analyze_by_category(trades))

        # Save and return
        self._patterns = patterns
        self._save_patterns()
        
        log.info(f"[TradingMemory] Analyzed {len(trades)} trades → {len(patterns)} patterns detected")
        return patterns

    def _analyze_confidence_bands(self, trades: List[Dict]) -> List[Dict]:
        """Analyze how entry confidence correlates with outcomes."""
        patterns = []
        bands = [
            (0.0, 0.50, "low"),
            (0.50, 0.60, "medium"),
            (0.60, 0.75, "high"),
            (0.75, 1.01, "very_high"),
        ]

        for low, high, label in bands:
            band_trades = [
                t for t in trades
                if low <= t.get("confidence_at_entry", 0.5) < high
            ]
            if len(band_trades) < 3:
                continue

            wins = [t for t in band_trades if t.get("pnl_usd", 0) > 0]
            wr = len(wins) / len(band_trades)
            avg_pnl = sum(t.get("pnl_usd", 0) for t in band_trades) / len(band_trades)

            if wr >= 0.65 or wr <= 0.35:
                patterns.append({
                    "pattern_id": f"confidence_{label}",
                    "description": f"Trades with {label} confidence ({low*100:.0f}-{high*100:.0f}%) "
                                   f"have {wr*100:.0f}% win rate (n={len(band_trades)})",
                    "category": "entry",
                    "win_rate": round(wr, 3),
                    "sample_size": len(band_trades),
                    "avg_pnl": round(avg_pnl, 2),
                    "confidence": min(0.9, len(band_trades) / 20),
                    "actionable_rule": (
                        f"{'PREFER' if wr >= 0.6 else 'AVOID'} trades with {label} confidence "
                        f"({low*100:.0f}-{high*100:.0f}%)"
                    ),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                })

        return patterns

    def _analyze_by_symbol(self, trades: List[Dict]) -> List[Dict]:
        """Analyze per-symbol performance."""
        patterns = []
        by_symbol = defaultdict(list)

        for t in trades:
            sym = t.get("symbol", "")
            if sym:
                by_symbol[sym].append(t)

        for sym, sym_trades in by_symbol.items():
            if len(sym_trades) < 3:
                continue

            wins = [t for t in sym_trades if t.get("pnl_usd", 0) > 0]
            wr = len(wins) / len(sym_trades)
            avg_pnl = sum(t.get("pnl_usd", 0) for t in sym_trades) / len(sym_trades)

            if wr >= 0.70 or wr <= 0.30:
                patterns.append({
                    "pattern_id": f"symbol_{sym.replace('/', '_')}",
                    "description": f"{sym}: {wr*100:.0f}% win rate over {len(sym_trades)} trades, "
                                   f"avg P&L ${avg_pnl:+,.2f}",
                    "category": "pair",
                    "win_rate": round(wr, 3),
                    "sample_size": len(sym_trades),
                    "avg_pnl": round(avg_pnl, 2),
                    "confidence": min(0.9, len(sym_trades) / 15),
                    "actionable_rule": (
                        f"{'FOCUS on' if wr >= 0.6 else 'REDUCE exposure to'} {sym} "
                        f"(historical WR: {wr*100:.0f}%)"
                    ),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "symbols": [sym],
                })

        return patterns

    def _analyze_direction_bias(self, trades: List[Dict]) -> List[Dict]:
        """Analyze if BUY or SELL performs consistently better."""
        patterns = []

        for direction in ["buy", "sell"]:
            dir_trades = [t for t in trades if t.get("direction", "").lower() == direction]
            if len(dir_trades) < 5:
                continue

            wins = [t for t in dir_trades if t.get("pnl_usd", 0) > 0]
            wr = len(wins) / len(dir_trades)
            avg_pnl = sum(t.get("pnl_usd", 0) for t in dir_trades) / len(dir_trades)

            if wr >= 0.65 or wr <= 0.35:
                patterns.append({
                    "pattern_id": f"direction_{direction}",
                    "description": f"{direction.upper()} trades: {wr*100:.0f}% win rate "
                                   f"(n={len(dir_trades)}), avg P&L ${avg_pnl:+,.2f}",
                    "category": "entry",
                    "win_rate": round(wr, 3),
                    "sample_size": len(dir_trades),
                    "avg_pnl": round(avg_pnl, 2),
                    "confidence": min(0.85, len(dir_trades) / 20),
                    "actionable_rule": (
                        f"{'FAVOR' if wr >= 0.6 else 'BE CAUTIOUS with'} {direction.upper()} entries"
                    ),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                })

        return patterns

    def _analyze_duration_effects(self, trades: List[Dict]) -> List[Dict]:
        """Analyze how trade duration affects outcomes."""
        patterns = []
        duration_bands = [
            (0, 15, "scalp (<15min)"),
            (15, 60, "short (15-60min)"),
            (60, 240, "medium (1-4h)"),
            (240, 1440, "swing (4-24h)"),
            (1440, 99999, "position (>24h)"),
        ]

        for low, high, label in duration_bands:
            band_trades = [
                t for t in trades
                if low <= t.get("duration_min", 0) < high
            ]
            if len(band_trades) < 3:
                continue

            wins = [t for t in band_trades if t.get("pnl_usd", 0) > 0]
            wr = len(wins) / len(band_trades)
            avg_pnl = sum(t.get("pnl_usd", 0) for t in band_trades) / len(band_trades)

            patterns.append({
                "pattern_id": f"duration_{label.split(' ')[0]}",
                "description": f"{label} trades: {wr*100:.0f}% WR, avg ${avg_pnl:+,.2f} "
                               f"(n={len(band_trades)})",
                "category": "timing",
                "win_rate": round(wr, 3),
                "sample_size": len(band_trades),
                "avg_pnl": round(avg_pnl, 2),
                "confidence": min(0.8, len(band_trades) / 10),
                "actionable_rule": (
                    f"{'PREFER' if avg_pnl > 0 else 'AVOID'} {label} holding periods"
                ),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            })

        return patterns

    def _analyze_streaks(self, trades: List[Dict]) -> List[Dict]:
        """Analyze what happens after win/loss streaks."""
        patterns = []
        if len(trades) < 10:
            return patterns

        # After N consecutive losses, what's the win rate of the next trade?
        for streak_len in [2, 3, 4]:
            after_loss_streak = []
            for i in range(streak_len, len(trades)):
                # Check if previous N trades were all losses
                prev_streak = trades[i - streak_len:i]
                all_losses = all(t.get("pnl_usd", 0) < 0 for t in prev_streak)
                if all_losses:
                    after_loss_streak.append(trades[i])

            if len(after_loss_streak) >= 3:
                wins = [t for t in after_loss_streak if t.get("pnl_usd", 0) > 0]
                wr = len(wins) / len(after_loss_streak)

                patterns.append({
                    "pattern_id": f"after_{streak_len}_losses",
                    "description": f"After {streak_len} consecutive losses: "
                                   f"next trade wins {wr*100:.0f}% of the time (n={len(after_loss_streak)})",
                    "category": "streak",
                    "win_rate": round(wr, 3),
                    "sample_size": len(after_loss_streak),
                    "avg_pnl": round(
                        sum(t.get("pnl_usd", 0) for t in after_loss_streak) / len(after_loss_streak), 2
                    ),
                    "confidence": min(0.75, len(after_loss_streak) / 8),
                    "actionable_rule": (
                        f"After {streak_len} losses in a row: "
                        f"{'continue trading normally' if wr >= 0.5 else 'PAUSE and reduce size'}"
                    ),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                })

        return patterns

    def _analyze_close_reasons(self, trades: List[Dict]) -> List[Dict]:
        """Analyze outcomes by how trades were closed."""
        patterns = []
        by_reason = defaultdict(list)

        for t in trades:
            reason = t.get("close_reason", "unknown")
            by_reason[reason].append(t)

        for reason, reason_trades in by_reason.items():
            if len(reason_trades) < 3:
                continue

            avg_pnl = sum(t.get("pnl_usd", 0) for t in reason_trades) / len(reason_trades)
            wins = [t for t in reason_trades if t.get("pnl_usd", 0) > 0]
            wr = len(wins) / len(reason_trades)

            patterns.append({
                "pattern_id": f"close_{reason}",
                "description": f"Trades closed by '{reason}': {wr*100:.0f}% profitable, "
                               f"avg ${avg_pnl:+,.2f} (n={len(reason_trades)})",
                "category": "exit",
                "win_rate": round(wr, 3),
                "sample_size": len(reason_trades),
                "avg_pnl": round(avg_pnl, 2),
                "confidence": min(0.8, len(reason_trades) / 10),
                "actionable_rule": f"Close reason '{reason}' analysis — avg outcome: ${avg_pnl:+,.2f}",
                "last_updated": datetime.now(timezone.utc).isoformat(),
            })

        return patterns

    def _analyze_by_category(self, trades: List[Dict]) -> List[Dict]:
        """Analyze performance by pair category."""
        patterns = []
        by_category = defaultdict(list)

        for t in trades:
            cat = t.get("category", "unknown")
            if cat:
                by_category[cat].append(t)

        for cat, cat_trades in by_category.items():
            if len(cat_trades) < 3:
                continue

            wins = [t for t in cat_trades if t.get("pnl_usd", 0) > 0]
            wr = len(wins) / len(cat_trades)
            avg_pnl = sum(t.get("pnl_usd", 0) for t in cat_trades) / len(cat_trades)

            if wr >= 0.65 or wr <= 0.35:
                patterns.append({
                    "pattern_id": f"category_{cat}",
                    "description": f"Category '{cat}': {wr*100:.0f}% WR, avg ${avg_pnl:+,.2f} "
                                   f"(n={len(cat_trades)})",
                    "category": "pair",
                    "win_rate": round(wr, 3),
                    "sample_size": len(cat_trades),
                    "avg_pnl": round(avg_pnl, 2),
                    "confidence": min(0.8, len(cat_trades) / 10),
                    "actionable_rule": (
                        f"{'PRIORITIZE' if wr >= 0.6 else 'REDUCE'} {cat} category trades"
                    ),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                })

        return patterns

    # ──────────────────────────────────────────
    # Setup Assessment
    # ──────────────────────────────────────────

    def assess_setup(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        category: str = "",
    ) -> Dict:
        """
        Assess a proposed trade setup based on historical patterns.
        
        Returns:
            Dict with historical_win_rate, risk_level, recommendations
        """
        trades = self._get_trades()
        if len(trades) < 5:
            return {
                "assessment": "insufficient_data",
                "message": "Not enough trade history for assessment.",
                "proceed": True,
            }

        score = 0.0
        warnings = []
        positives = []

        # Check symbol history
        sym_trades = [t for t in trades if t.get("symbol") == symbol]
        if sym_trades:
            sym_wins = [t for t in sym_trades if t.get("pnl_usd", 0) > 0]
            sym_wr = len(sym_wins) / len(sym_trades)
            if sym_wr < 0.35:
                warnings.append(f"{symbol} has poor history: {sym_wr*100:.0f}% WR over {len(sym_trades)} trades")
                score -= 0.2
            elif sym_wr >= 0.65:
                positives.append(f"{symbol} performs well: {sym_wr*100:.0f}% WR")
                score += 0.2

        # Check confidence band
        band_trades = [
            t for t in trades
            if abs(t.get("confidence_at_entry", 0.5) - confidence) < 0.1
        ]
        if len(band_trades) >= 3:
            band_wins = [t for t in band_trades if t.get("pnl_usd", 0) > 0]
            band_wr = len(band_wins) / len(band_trades)
            if band_wr < 0.4:
                warnings.append(f"Confidence ~{confidence*100:.0f}% historically underperforms ({band_wr*100:.0f}% WR)")
                score -= 0.15
            elif band_wr >= 0.6:
                positives.append(f"This confidence level historically works ({band_wr*100:.0f}% WR)")
                score += 0.15

        # Check direction
        dir_trades = [t for t in trades if t.get("direction", "").lower() == direction.lower()]
        if len(dir_trades) >= 5:
            dir_wins = [t for t in dir_trades if t.get("pnl_usd", 0) > 0]
            dir_wr = len(dir_wins) / len(dir_trades)
            if dir_wr < 0.35:
                warnings.append(f"{direction.upper()} trades underperforming: {dir_wr*100:.0f}% WR")
                score -= 0.1

        # Check recent streak
        last_5 = trades[-5:]
        recent_losses = sum(1 for t in last_5 if t.get("pnl_usd", 0) < 0)
        if recent_losses >= 4:
            warnings.append(f"Currently on a {recent_losses}/5 loss streak — consider reducing size")
            score -= 0.2

        # Determine overall assessment
        if score >= 0.2:
            assessment = "favorable"
            proceed = True
        elif score <= -0.2:
            assessment = "unfavorable"
            proceed = False
        else:
            assessment = "neutral"
            proceed = True

        return {
            "assessment": assessment,
            "score": round(score, 2),
            "proceed": proceed,
            "warnings": warnings,
            "positives": positives,
            "symbol_history": f"{len(sym_trades)} trades" if sym_trades else "no history",
        }

    # ──────────────────────────────────────────
    # Format for Prompt
    # ──────────────────────────────────────────

    def format_for_prompt(self, symbol: str = None, max_patterns: int = 10) -> str:
        """
        Format trading memory patterns for LLM prompt injection.
        """
        if not self._patterns:
            self.analyze_patterns()

        if not self._patterns:
            return "No statistical patterns detected yet (need more trades)."

        # Filter relevant patterns
        relevant = self._patterns
        if symbol:
            relevant = [
                p for p in self._patterns
                if not p.get("symbols") or symbol in p.get("symbols", [])
                or p.get("category") in ("entry", "timing", "streak", "exit")
            ]

        # Sort by confidence × sample_size
        relevant.sort(
            key=lambda p: p.get("confidence", 0) * p.get("sample_size", 0),
            reverse=True
        )
        relevant = relevant[:max_patterns]

        lines = ["## Trading Patterns (from experience):"]
        for p in relevant:
            conf_marker = "★" if p.get("confidence", 0) >= 0.7 else "●"
            lines.append(
                f"  {conf_marker} {p['actionable_rule']} "
                f"[WR:{p.get('win_rate',0)*100:.0f}%, n={p.get('sample_size',0)}]"
            )

        return "\n".join(lines)

    # ──────────────────────────────────────────
    # Evolution Suggestions
    # ──────────────────────────────────────────

    def suggest_config_changes(self) -> List[Dict]:
        """
        Based on patterns, suggest configuration changes.
        Similar to update-fitur/scripts/evolve-thresholds.js
        """
        trades = self._get_trades()
        if len(trades) < 10:
            return []

        suggestions = []
        summary = self._lessons.get_performance_summary() if self._lessons else {}

        # If win rate is low, suggest tighter confidence filter
        wr_str = summary.get("win_rate", "50%")
        wr = float(wr_str.replace("%", "")) / 100 if isinstance(wr_str, str) else 0.5

        if wr < 0.45:
            suggestions.append({
                "parameter": "min_confidence",
                "current": 0.50,
                "suggested": 0.60,
                "reason": f"Win rate is {wr*100:.0f}% — raise confidence threshold to filter weak signals",
            })

        # If most losses are SL hits with short duration, suggest wider stops
        sl_trades = [t for t in trades if t.get("close_reason") == "sl_hit"]
        if sl_trades and len(sl_trades) / len(trades) > 0.5:
            avg_duration = sum(t.get("duration_min", 0) for t in sl_trades) / len(sl_trades)
            if avg_duration < 30:
                suggestions.append({
                    "parameter": "atr_sl_multiplier",
                    "current": 2.5,
                    "suggested": 3.0,
                    "reason": f"Too many quick SL hits (avg {avg_duration:.0f}min). "
                              f"Widen stops to give trades more room.",
                })

        # If profit factor is high, can increase risk
        pf = summary.get("profit_factor", 1.0)
        if isinstance(pf, (int, float)) and pf > 2.0 and wr > 0.55:
            suggestions.append({
                "parameter": "risk_pct_per_trade",
                "current": 1.5,
                "suggested": 2.0,
                "reason": f"Profit factor is {pf:.1f} with {wr*100:.0f}% WR — "
                          f"can safely increase risk per trade.",
            })

        return suggestions

    def get_stats(self) -> Dict:
        """Get trading memory statistics."""
        return {
            "total_patterns": len(self._patterns),
            "patterns_by_category": {
                cat: len([p for p in self._patterns if p.get("category") == cat])
                for cat in set(p.get("category", "") for p in self._patterns)
            },
            "total_trades_analyzed": len(self._get_trades()),
            "last_analysis": self._patterns[0].get("last_updated", "never") if self._patterns else "never",
        }
