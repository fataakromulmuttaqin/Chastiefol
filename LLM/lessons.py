"""
Chastiefol — LLM Lessons & Learning System
Persistent memory that stores trading lessons learned from experience.

Inspired by: update-fitur/lessons.js (Meridian-style learning memory)

The LLM agent saves lessons after notable events:
- Winning trade patterns (what worked)
- Losing trade patterns (what to avoid)
- False signal observations
- Market condition insights
- Risk management rules discovered

Lessons are injected into the system prompt so the LLM never repeats mistakes.

Storage: JSON files (lessons.json, performance.json)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List
from pathlib import Path
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("LLM.Lessons")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# Lessons data directory — SHARED across all trading modes (paper/demo/testnet/live).
# This means lessons learned during demo trading are automatically available
# when switching to live mode. The LLM's knowledge persists between sessions
# and across mode changes, enabling the bot to learn in demo and apply in live.
DATA_DIR = Path(__file__).parent / "data"
LESSONS_FILE = DATA_DIR / "lessons.json"
PERFORMANCE_FILE = DATA_DIR / "performance.json"
PATTERNS_FILE = DATA_DIR / "patterns.json"


class LessonRole(str, Enum):
    SCREENER = "SCREENER"       # Lessons for entry/signal detection
    MANAGER = "MANAGER"         # Lessons for trade management
    RISK = "RISK"               # Risk management lessons
    GENERAL = "GENERAL"         # General trading wisdom
    MARKET = "MARKET"           # Market behavior patterns


class LessonSource(str, Enum):
    AGENT = "agent"             # LLM self-generated
    TRADE_CLOSE = "trade_close" # Auto-generated on trade close
    MANUAL = "manual"           # User-added
    REFLECTION = "reflection"   # From periodic self-reflection
    EVOLUTION = "evolution"     # From threshold evolution


@dataclass
class Lesson:
    """A single trading lesson."""
    id: int
    role: str
    lesson: str
    source: str
    added_at: str
    context: str = ""           # What triggered this lesson
    symbol: str = ""            # Related symbol (if any)
    confidence: float = 1.0     # How confident in this lesson (0-1)
    times_validated: int = 0    # How many times this proved true
    times_violated: int = 0     # How many times this was violated


@dataclass
class TradeRecord:
    """Record of a closed trade for performance tracking."""
    trade_id: str
    symbol: str
    direction: str              # "buy" or "sell"
    amount: float
    entry_price: float
    exit_price: float
    pnl_usd: float
    pnl_pct: float
    duration_min: int
    stop_loss: float
    take_profit: float
    confidence_at_entry: float
    reasons_at_entry: List[str]
    close_reason: str           # "tp_hit", "sl_hit", "manual", "trailing_stop", "session_end"
    category: str = ""          # Pair category
    timeframe: str = ""
    closed_at: str = ""
    market_conditions: Dict = field(default_factory=dict)


# ──────────────────────────────────────────────
# Lessons Manager
# ──────────────────────────────────────────────

class LessonsManager:
    """
    Persistent lessons storage — the LLM's trading memory.
    
    Adapts the update-fitur/lessons.js approach to Python:
    - Stores lessons in JSON file
    - Categorized by role (SCREENER, MANAGER, RISK, etc.)
    - Performance tracking with win/loss analysis
    - Pattern detection from trade history
    - Formatted output for system prompt injection
    
    Usage:
        lessons = LessonsManager()
        
        # Add a lesson
        lessons.add_lesson(
            role="SCREENER",
            lesson="BTC/USDT shows false breakouts during Asia session low volume",
            source="trade_close",
            context="Lost trade #123 due to fake breakout at 3:00 UTC"
        )
        
        # Get lessons for prompt injection
        prompt_text = lessons.format_for_prompt(role="SCREENER")
        
        # Record a closed trade
        lessons.record_trade(TradeRecord(...))
        
        # Get performance summary
        summary = lessons.get_performance_summary()
    """

    def __init__(self, data_dir: Path = None):
        self._data_dir = data_dir or DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        self._lessons_file = self._data_dir / "lessons.json"
        self._perf_file = self._data_dir / "performance.json"
        self._patterns_file = self._data_dir / "patterns.json"
        
        log.info(f"LessonsManager initialized | Data dir: {self._data_dir}")

    # ──────────────────────────────────────────
    # Lessons CRUD
    # ──────────────────────────────────────────

    def load_lessons(self) -> List[Dict]:
        """Load all lessons from file."""
        if not self._lessons_file.exists():
            return []
        try:
            return json.loads(self._lessons_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return []

    def save_lessons(self, lessons: List[Dict]):
        """Save lessons to file."""
        self._lessons_file.write_text(
            json.dumps(lessons, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def add_lesson(
        self,
        lesson: str,
        role: str = "GENERAL",
        source: str = "agent",
        context: str = "",
        symbol: str = "",
        confidence: float = 1.0,
    ) -> int:
        """
        Add a new lesson to the database.
        
        Args:
            lesson: The lesson text (should be specific and actionable)
            role: Which agent role this applies to (SCREENER/MANAGER/RISK/GENERAL)
            source: What generated this lesson (agent/trade_close/manual/reflection)
            context: What triggered this lesson (e.g. trade ID or event)
            symbol: Related trading pair
            confidence: How confident (0-1)
            
        Returns:
            Total number of lessons after adding
        """
        lessons = self.load_lessons()
        
        # Check for duplicates (similar content)
        for existing in lessons:
            if self._is_similar(existing.get("lesson", ""), lesson):
                # Boost confidence of existing instead of duplicating
                existing["times_validated"] = existing.get("times_validated", 0) + 1
                existing["confidence"] = min(1.0, existing.get("confidence", 0.5) + 0.1)
                self.save_lessons(lessons)
                log.info(f"[Lessons] Existing lesson reinforced (#{existing['id']})")
                return len(lessons)
        
        new_lesson = {
            "id": int(time.time() * 1000),
            "role": role,
            "lesson": lesson,
            "source": source,
            "context": context,
            "symbol": symbol,
            "confidence": confidence,
            "times_validated": 0,
            "times_violated": 0,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        
        lessons.append(new_lesson)
        self.save_lessons(lessons)
        
        log.info(f"[Lessons] Added: [{role}] {lesson[:80]}...")
        return len(lessons)

    def get_lessons(self, role: str = None, symbol: str = None, limit: int = 50) -> List[Dict]:
        """
        Get lessons filtered by role and/or symbol.
        Returns lessons sorted by confidence (highest first).
        """
        lessons = self.load_lessons()
        
        # Filter
        if role:
            lessons = [l for l in lessons if l.get("role") in (role, "GENERAL")]
        if symbol:
            lessons = [l for l in lessons if not l.get("symbol") or l["symbol"] == symbol]
        
        # Sort by confidence then recency
        lessons.sort(key=lambda x: (x.get("confidence", 0.5), x.get("id", 0)), reverse=True)
        
        return lessons[:limit]

    def clear_lessons(self, role: str = None):
        """Clear all lessons or only those for a specific role."""
        if not role:
            self.save_lessons([])
            log.info("[Lessons] All lessons cleared")
        else:
            lessons = self.load_lessons()
            lessons = [l for l in lessons if l.get("role") != role]
            self.save_lessons(lessons)
            log.info(f"[Lessons] Cleared lessons for role: {role}")

    def validate_lesson(self, lesson_id: int):
        """Mark a lesson as validated (proved true again)."""
        lessons = self.load_lessons()
        for l in lessons:
            if l.get("id") == lesson_id:
                l["times_validated"] = l.get("times_validated", 0) + 1
                l["confidence"] = min(1.0, l.get("confidence", 0.5) + 0.05)
                break
        self.save_lessons(lessons)

    def violate_lesson(self, lesson_id: int):
        """Mark a lesson as violated (proved false)."""
        lessons = self.load_lessons()
        for l in lessons:
            if l.get("id") == lesson_id:
                l["times_violated"] = l.get("times_violated", 0) + 1
                l["confidence"] = max(0.1, l.get("confidence", 0.5) - 0.1)
                break
        self.save_lessons(lessons)

    def remove_weak_lessons(self, min_confidence: float = 0.3):
        """Remove lessons with very low confidence (disproven over time)."""
        lessons = self.load_lessons()
        before = len(lessons)
        lessons = [l for l in lessons if l.get("confidence", 0.5) >= min_confidence]
        self.save_lessons(lessons)
        removed = before - len(lessons)
        if removed > 0:
            log.info(f"[Lessons] Pruned {removed} weak lessons (conf < {min_confidence})")

    # ──────────────────────────────────────────
    # Format for Prompt Injection
    # ──────────────────────────────────────────

    def format_for_prompt(self, role: str = None, symbol: str = None, max_lessons: int = 20) -> str:
        """
        Format lessons for injection into the LLM system prompt.
        This is the key method — ensures the LLM remembers past mistakes.
        """
        lessons = self.get_lessons(role=role, symbol=symbol, limit=max_lessons)
        
        if not lessons:
            return "No lessons recorded yet. Learn from each trade outcome."
        
        lines = []
        for i, l in enumerate(lessons, 1):
            confidence_marker = "★" if l.get("confidence", 0) >= 0.8 else "●"
            role_tag = f"[{l.get('role', 'GENERAL')}]"
            symbol_tag = f"({l.get('symbol')})" if l.get("symbol") else ""
            validated = l.get("times_validated", 0)
            
            line = f"{confidence_marker} {i}. {role_tag}{symbol_tag} {l['lesson']}"
            if validated > 0:
                line += f" (validated {validated}x)"
            lines.append(line)
        
        return "\n".join(lines)

    # ──────────────────────────────────────────
    # Performance Tracking
    # ──────────────────────────────────────────

    def load_performance(self) -> List[Dict]:
        """Load trade performance records."""
        if not self._perf_file.exists():
            return []
        try:
            return json.loads(self._perf_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return []

    def save_performance(self, records: List[Dict]):
        """Save performance records."""
        self._perf_file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def record_trade(self, trade: TradeRecord):
        """
        Record a closed trade for performance analysis.
        Also auto-generates lessons from trade outcomes.
        """
        records = self.load_performance()
        record_dict = asdict(trade) if hasattr(trade, '__dataclass_fields__') else trade.__dict__
        records.append(record_dict)
        
        # Keep last 500 trades
        if len(records) > 500:
            records = records[-500:]
        
        self.save_performance(records)
        
        # Auto-generate lessons from this trade
        self._auto_learn_from_trade(trade)
        
        log.info(f"[Perf] Trade recorded: {trade.symbol} {trade.direction} "
                 f"P&L: ${trade.pnl_usd:+,.2f} ({trade.close_reason})")

    def get_performance_summary(self, last_n: int = None, symbol: str = None) -> Dict:
        """
        Get comprehensive performance summary.
        Similar to update-fitur/lessons.js getPerfSummary().
        """
        records = self.load_performance()
        
        if symbol:
            records = [r for r in records if r.get("symbol") == symbol]
        if last_n:
            records = records[-last_n:]
        
        if not records:
            return {"trades": 0, "message": "No trades recorded yet."}
        
        wins = [r for r in records if r.get("pnl_usd", 0) > 0]
        losses = [r for r in records if r.get("pnl_usd", 0) < 0]
        breakeven = [r for r in records if r.get("pnl_usd", 0) == 0]
        
        total_pnl = sum(r.get("pnl_usd", 0) for r in records)
        avg_win = sum(r.get("pnl_usd", 0) for r in wins) / len(wins) if wins else 0
        avg_loss = sum(r.get("pnl_usd", 0) for r in losses) / len(losses) if losses else 0
        
        # Win streaks / loss streaks
        max_win_streak = 0
        max_loss_streak = 0
        current_streak = 0
        streak_type = None
        
        for r in records:
            pnl = r.get("pnl_usd", 0)
            if pnl > 0:
                if streak_type == "win":
                    current_streak += 1
                else:
                    current_streak = 1
                    streak_type = "win"
                max_win_streak = max(max_win_streak, current_streak)
            elif pnl < 0:
                if streak_type == "loss":
                    current_streak += 1
                else:
                    current_streak = 1
                    streak_type = "loss"
                max_loss_streak = max(max_loss_streak, current_streak)
        
        # By close reason
        close_reasons = {}
        for r in records:
            reason = r.get("close_reason", "unknown")
            if reason not in close_reasons:
                close_reasons[reason] = {"count": 0, "pnl": 0}
            close_reasons[reason]["count"] += 1
            close_reasons[reason]["pnl"] += r.get("pnl_usd", 0)
        
        # By symbol
        by_symbol = {}
        for r in records:
            sym = r.get("symbol", "unknown")
            if sym not in by_symbol:
                by_symbol[sym] = {"trades": 0, "wins": 0, "pnl": 0}
            by_symbol[sym]["trades"] += 1
            by_symbol[sym]["pnl"] += r.get("pnl_usd", 0)
            if r.get("pnl_usd", 0) > 0:
                by_symbol[sym]["wins"] += 1
        
        # Average duration
        avg_duration = sum(r.get("duration_min", 0) for r in records) / len(records)
        
        return {
            "trades": len(records),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate": f"{(len(wins) / len(records) * 100):.1f}%",
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(abs(avg_win * len(wins)) / abs(avg_loss * len(losses)), 2) if losses and avg_loss != 0 else float("inf"),
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "avg_duration_min": round(avg_duration, 0),
            "by_close_reason": close_reasons,
            "by_symbol": by_symbol,
            "last_10": records[-10:],
        }

    def format_performance_for_prompt(self, last_n: int = 20) -> str:
        """Format performance stats for prompt injection."""
        summary = self.get_performance_summary(last_n=last_n)
        
        if summary.get("trades", 0) == 0:
            return "No trade history yet."
        
        lines = [
            f"Total trades: {summary['trades']} | Win rate: {summary['win_rate']} | "
            f"P&L: ${summary['total_pnl']:+,.2f}",
            f"Avg win: ${summary['avg_win']:+,.2f} | Avg loss: ${summary['avg_loss']:+,.2f} | "
            f"Profit factor: {summary['profit_factor']}",
            f"Max win streak: {summary['max_win_streak']} | Max loss streak: {summary['max_loss_streak']}",
        ]
        
        # Recent trades summary
        last_5 = summary.get("last_10", [])[-5:]
        if last_5:
            lines.append("Recent trades:")
            for t in last_5:
                emoji = "✅" if t.get("pnl_usd", 0) > 0 else "❌"
                lines.append(
                    f"  {emoji} {t.get('symbol','?')} {t.get('direction','?')} "
                    f"${t.get('pnl_usd',0):+,.2f} ({t.get('close_reason','?')})"
                )
        
        return "\n".join(lines)

    # ──────────────────────────────────────────
    # Auto-Learning from Trade Outcomes
    # ──────────────────────────────────────────

    def _auto_learn_from_trade(self, trade: TradeRecord):
        """
        Automatically generate lessons from trade outcomes.
        This is the core of the learning system.
        """
        pnl = trade.pnl_usd
        reasons = trade.reasons_at_entry or []
        
        # ── Loss: Analyze what went wrong ──
        if pnl < 0:
            if trade.close_reason == "sl_hit":
                lesson = (
                    f"SL hit on {trade.symbol} {trade.direction} "
                    f"(confidence {trade.confidence_at_entry*100:.0f}%). "
                    f"Entry reasons: {', '.join(reasons[:3])}. "
                    f"Duration: {trade.duration_min}min. "
                    f"Consider: Was the setup truly valid or was it a false signal?"
                )
                self.add_lesson(
                    lesson=lesson,
                    role="SCREENER",
                    source="trade_close",
                    context=f"Lost ${abs(pnl):.2f} on {trade.symbol}",
                    symbol=trade.symbol,
                    confidence=0.7,
                )
            
            # Quick stop (< 5 min) = likely false entry
            if trade.duration_min < 5:
                self.add_lesson(
                    lesson=f"Extremely quick SL hit on {trade.symbol} ({trade.duration_min}min). "
                           f"This is often a sign of entering during high volatility or against momentum. "
                           f"Wait for candle close confirmation before entering.",
                    role="SCREENER",
                    source="trade_close",
                    context=f"Quick loss on {trade.symbol}",
                    symbol=trade.symbol,
                    confidence=0.8,
                )
            
            # Low confidence entry that lost
            if trade.confidence_at_entry < 0.55:
                self.add_lesson(
                    lesson=f"Trade with low confidence ({trade.confidence_at_entry*100:.0f}%) on "
                           f"{trade.symbol} resulted in loss. "
                           f"Rule: Skip setups below 55% confidence.",
                    role="RISK",
                    source="trade_close",
                    symbol=trade.symbol,
                    confidence=0.85,
                )
        
        # ── Win: Record what worked ──
        elif pnl > 0:
            if trade.close_reason == "tp_hit":
                lesson = (
                    f"Successful trade on {trade.symbol} {trade.direction} "
                    f"(TP hit, +${pnl:.2f}). "
                    f"Key factors: {', '.join(reasons[:3])}. "
                    f"Confidence was {trade.confidence_at_entry*100:.0f}%. "
                    f"This pattern works — look for similar setups."
                )
                self.add_lesson(
                    lesson=lesson,
                    role="SCREENER",
                    source="trade_close",
                    context=f"Won ${pnl:.2f} on {trade.symbol}",
                    symbol=trade.symbol,
                    confidence=0.8,
                )
        
        # ── Pattern: Consecutive losses on same pair ──
        records = self.load_performance()
        recent_same_pair = [r for r in records[-10:] if r.get("symbol") == trade.symbol]
        consecutive_losses = 0
        for r in reversed(recent_same_pair):
            if r.get("pnl_usd", 0) < 0:
                consecutive_losses += 1
            else:
                break
        
        if consecutive_losses >= 3:
            self.add_lesson(
                lesson=f"{consecutive_losses} consecutive losses on {trade.symbol}. "
                       f"This pair may be in unfavorable conditions or the strategy doesn't suit "
                       f"current market structure. Consider pausing {trade.symbol} or reducing size.",
                role="RISK",
                source="trade_close",
                symbol=trade.symbol,
                confidence=0.9,
            )

    # ──────────────────────────────────────────
    # Reflection & Evolution
    # ──────────────────────────────────────────

    def generate_reflection_prompt(self) -> str:
        """
        Generate a prompt for the LLM to reflect on its recent performance
        and suggest new lessons or rule changes.
        """
        summary = self.get_performance_summary(last_n=20)
        lessons = self.format_for_prompt(max_lessons=10)
        
        return f"""Reflect on your recent trading performance and generate new lessons.

## Performance (last 20 trades):
{json.dumps(summary, indent=2, default=str)}

## Current Lessons:
{lessons}

Based on this data:
1. What patterns do you see in your wins vs losses?
2. Are there any recurring mistakes?
3. What NEW lessons should be added?
4. Should any existing lessons be modified or removed?

Respond with a JSON array of new lessons:
[
  {{"role": "SCREENER|MANAGER|RISK|GENERAL", "lesson": "specific actionable rule", "confidence": 0.5-1.0}}
]
"""

    def apply_reflection_results(self, new_lessons: List[Dict]):
        """Apply lessons generated from self-reflection."""
        for l in new_lessons:
            self.add_lesson(
                lesson=l.get("lesson", ""),
                role=l.get("role", "GENERAL"),
                source="reflection",
                confidence=l.get("confidence", 0.7),
            )
        log.info(f"[Lessons] Applied {len(new_lessons)} lessons from reflection")

    # ──────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────

    @staticmethod
    def _is_similar(text1: str, text2: str, threshold: float = 0.7) -> bool:
        """Check if two lesson texts are similar enough to be duplicates."""
        # Simple word overlap check
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2) / max(len(words1), len(words2))
        return overlap >= threshold

    def get_stats(self) -> Dict:
        """Get lessons system statistics."""
        lessons = self.load_lessons()
        records = self.load_performance()
        
        role_counts = {}
        for l in lessons:
            role = l.get("role", "GENERAL")
            role_counts[role] = role_counts.get(role, 0) + 1
        
        return {
            "total_lessons": len(lessons),
            "total_trades_recorded": len(records),
            "lessons_by_role": role_counts,
            "avg_confidence": round(
                sum(l.get("confidence", 0.5) for l in lessons) / max(len(lessons), 1), 2
            ),
            "high_confidence_lessons": len([l for l in lessons if l.get("confidence", 0) >= 0.8]),
        }
