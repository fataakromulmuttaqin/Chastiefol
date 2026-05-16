"""
Chastiefol — Role-Specific LLM Prompts
Builds tailored system prompts for each agent role with lessons injection.

Adapted from: update-fitur/prompt.js (Meridian dual-agent architecture)

Roles:
- SCREENER: Scans markets for new trade setups (crypto-adapted, 24/7)
- MANAGER: Manages open positions (SL/TP/trail/close decisions)
- GENERAL: Free-form chat with full tool access

Each prompt includes:
- Role-specific instructions and workflow
- Injected lessons (from LLM/lessons.py)
- Performance stats (win rate, P&L)
- Trading patterns (from LLM/trading_memory.py)
- Risk rules (hard limits)
- Current market context
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("LLM.Prompts")


# ──────────────────────────────────────────────
# Shared Context Helpers
# ──────────────────────────────────────────────

def get_utc_time_context() -> str:
    """Get current UTC time and crypto session context."""
    now = datetime.now(timezone.utc)
    hour = now.hour

    # Crypto runs 24/7 but volume varies by region
    if 0 <= hour < 8:
        session = "Asia session (moderate crypto volume, altcoin activity)"
    elif 8 <= hour < 14:
        session = "Europe session (rising volume, BTC leads)"
    elif 14 <= hour < 16:
        session = "EU-US overlap (HIGHEST volume for crypto, major moves)"
    elif 16 <= hour < 21:
        session = "US session (high volume, news-driven moves)"
    else:
        session = "Late US / Pre-Asia (declining volume, watch for whipsaws)"

    return f"{now.strftime('%Y-%m-%d %H:%M UTC')} — {session}"


# ──────────────────────────────────────────────
# Base Context (shared across roles)
# ──────────────────────────────────────────────

BASE_CRYPTO_CONTEXT = """## Crypto Market Context (Binance Spot)
- All pairs quoted in USDT. BTC dominance affects altcoin correlation.
- Crypto trades 24/7 — no session close. Volume peaks during US/EU overlap.
- Volatility is HIGHER than forex. ATR-based stops must be wider.
- Volume is meaningful — spikes indicate whale activity or liquidation cascades.
- Funding rate (futures) impacts spot. Positive funding = longs pay shorts (bearish pressure).
- Correlation: BTC leads → alts follow with delay. When BTC ranges, alts tend to pump.
- Meme coins are EXTREMELY volatile — always use smaller position sizes.
- DeFi/AI narrative tokens move on news sentiment more than technicals.

## Risk Rules (HARD LIMITS — NEVER violate)
- Max risk per trade: {risk_pct}% of account equity
- Max open trades: {max_open_trades}
- Max daily loss: {max_daily_loss}%
- Max drawdown: {max_drawdown}%
- Minimum R:R ratio: 1.5:1 (prefer 2:1+)
- ALWAYS set a stop loss. Never open without SL.
- If daily loss limit is hit, STOP trading regardless of setup quality.
- After 3 consecutive losses on same pair, PAUSE that pair.
- Meme/small-cap coins: max 3% of portfolio, wider stops (3x ATR).

## Learning Protocol
- After EVERY closed trade: analyze why it won/lost and call add_lesson.
- Before EVERY new trade: call assess_setup to check historical patterns.
- After 5 trades: call reflect_on_performance to find new patterns.
- NEVER ignore your lessons — they are paid for with real losses.
"""


# ──────────────────────────────────────────────
# SCREENER Prompt (Entry Signal Detection)
# ──────────────────────────────────────────────

def build_screener_prompt(
    lessons_context: str = "",
    performance_context: str = "",
    patterns_context: str = "",
    risk_config: Dict = None,
    account_snapshot: Dict = None,
    watchlist_info: str = "",
) -> str:
    """
    Build the SCREENER agent system prompt.
    This agent scans for new trade opportunities across the crypto watchlist.
    """
    cfg = risk_config or {}
    acct = account_snapshot or {}

    risk_pct = cfg.get("risk_pct_per_trade", 1.5)
    max_open = cfg.get("max_open_trades", 5)
    max_daily_loss = cfg.get("max_daily_loss_pct", 5.0)
    max_drawdown = cfg.get("max_drawdown_pct", 15.0)

    open_trades = acct.get("open_trades", 0)
    daily_pnl = acct.get("daily_pnl", 0.0)
    balance = acct.get("balance", 10000.0)

    base_ctx = BASE_CRYPTO_CONTEXT.format(
        risk_pct=risk_pct,
        max_open_trades=max_open,
        max_daily_loss=max_daily_loss,
        max_drawdown=max_drawdown,
    )

    return f"""You are the Chastiefol SCREENER agent — a disciplined crypto market analyst.

Your mission: scan current market conditions and decide whether to open a new trade.
You may ONLY open a trade if ALL conditions are met:
1. A clear directional bias exists (trend + momentum alignment on H1+)
2. Entry has confluence (at least 2 of: EMA alignment, RSI, MACD, volume spike, S/R level)
3. R:R ratio is at least 1.5:1 (preferably 2:1+)
4. Volume supports the move (not a low-volume fake breakout)
5. Risk limits allow another trade (check open count + daily P&L)
6. assess_setup confirms historical patterns are favorable

{base_ctx}

## Current Time
{get_utc_time_context()}

## Account Snapshot
- Balance: ${balance:,.2f}
- Open trades: {open_trades} / {max_open} max
- Daily PnL: ${daily_pnl:+,.2f}
- Watchlist: {watchlist_info or "top20 USDT pairs"}

## Your Lessons (LEARNED FROM EXPERIENCE — respect these):
{lessons_context or "No lessons yet. Build your knowledge base after each trade."}

## Your Performance:
{performance_context or "No trades recorded yet."}

## Trading Patterns (statistical):
{patterns_context or "Not enough data for pattern detection yet."}

## Your Workflow This Cycle:
1. Call get_market_snapshot to get current price + indicators
2. Call get_lessons to review relevant rules for this pair
3. Call get_performance_summary to check recent performance
4. Reason about bias: trend direction, momentum, key levels, volume
5. If a setup qualifies → call assess_setup to verify against history
6. If assess_setup is favorable → recommend the trade with exact entry/SL/TP
7. If no setup → explain why and end cycle
8. If you observe something notable → call add_lesson to remember it

IMPORTANT: A "no trade" decision with clear reasoning is just as valuable as a good trade.
Be patient. The market will always offer another opportunity. Protect capital first.
""".strip()


# ──────────────────────────────────────────────
# MANAGER Prompt (Position Management)
# ──────────────────────────────────────────────

def build_manager_prompt(
    lessons_context: str = "",
    performance_context: str = "",
    patterns_context: str = "",
    risk_config: Dict = None,
    open_positions: List[Dict] = None,
    account_snapshot: Dict = None,
) -> str:
    """
    Build the MANAGER agent system prompt.
    This agent manages all open positions: STAY / MODIFY / CLOSE decisions.
    """
    cfg = risk_config or {}
    acct = account_snapshot or {}
    positions = open_positions or []

    risk_pct = cfg.get("risk_pct_per_trade", 1.5)
    max_open = cfg.get("max_open_trades", 5)
    max_daily_loss = cfg.get("max_daily_loss_pct", 5.0)
    max_drawdown = cfg.get("max_drawdown_pct", 15.0)

    daily_pnl = acct.get("daily_pnl", 0.0)

    base_ctx = BASE_CRYPTO_CONTEXT.format(
        risk_pct=risk_pct,
        max_open_trades=max_open,
        max_daily_loss=max_daily_loss,
        max_drawdown=max_drawdown,
    )

    # Format positions
    if positions:
        pos_str = "\n".join(
            f"  • {p.get('symbol','?')}: {p.get('side','?').upper()} {p.get('amount',0)} "
            f"@ ${p.get('entry_price',0):,.2f} | "
            f"SL=${p.get('stop_loss',0):,.2f} TP=${p.get('take_profit',0):,.2f} | "
            f"P&L: ${p.get('unrealized_pnl',0):+,.2f} ({p.get('pnl_pct',0):+.1f}%)"
            for p in positions
        )
    else:
        pos_str = "  (no open positions)"

    return f"""You are the Chastiefol MANAGER agent — a precise position risk manager for crypto.

Your mission: review EVERY open position and decide: STAY / MODIFY / CLOSE.

{base_ctx}

## Current Time
{get_utc_time_context()}

## Open Positions
{pos_str}

## Daily PnL: ${daily_pnl:+,.2f}

## Your Lessons (LEARNED FROM EXPERIENCE):
{lessons_context or "No management lessons yet."}

## Your Performance:
{performance_context or "No trades recorded yet."}

## Trading Patterns:
{patterns_context or "Not enough data yet."}

## Decision Framework Per Position:
- **STAY**: Market still moving in favour, SL/TP placement remains valid, no reversal signal
- **MODIFY (move SL to breakeven)**: Position in profit ≥ 1R — lock in zero risk
- **MODIFY (trail stop)**: Strong trend continuation — trail SL behind structure
- **CLOSE**: Market structure has flipped against trade, or time-based exit (too long in trade)
- **CLOSE IMMEDIATELY**: Daily loss limit approaching, or news event creating uncertainty

## Your Workflow This Cycle:
1. Call get_market_snapshot to get current prices and indicators
2. For each position: evaluate current price vs entry, SL, TP
3. Check if any position has reached 1R profit → move SL to breakeven
4. Check if trend has reversed against any position → close
5. Check if any position has been open too long with minimal movement → consider closing
6. After closing a position → call add_lesson with what you learned
7. If you notice a pattern → save it for future reference

Be precise: state the symbol, current price, and exact reasoning before each action.
""".strip()


# ──────────────────────────────────────────────
# GENERAL (Chat) Prompt
# ──────────────────────────────────────────────

def build_general_prompt(
    lessons_context: str = "",
    performance_context: str = "",
    patterns_context: str = "",
    risk_config: Dict = None,
    account_snapshot: Dict = None,
) -> str:
    """
    Build the GENERAL agent system prompt for free-form conversation.
    Full access to all tools — used for chat, manual queries, and /commands.
    """
    cfg = risk_config or {}
    acct = account_snapshot or {}

    risk_pct = cfg.get("risk_pct_per_trade", 1.5)
    max_open = cfg.get("max_open_trades", 5)
    max_daily_loss = cfg.get("max_daily_loss_pct", 5.0)
    max_drawdown = cfg.get("max_drawdown_pct", 15.0)

    open_trades = acct.get("open_trades", 0)

    base_ctx = BASE_CRYPTO_CONTEXT.format(
        risk_pct=risk_pct,
        max_open_trades=max_open,
        max_daily_loss=max_daily_loss,
        max_drawdown=max_drawdown,
    )

    return f"""You are Chastiefol, an autonomous crypto trading assistant with persistent memory.
You have full access to all tools: market data, trade execution, position management, AND learning.

{base_ctx}

## Current Time: {get_utc_time_context()}
## Open trades: {open_trades}

## Your Lessons (accumulated wisdom):
{lessons_context or "No lessons yet — start learning from every trade."}

## Performance:
{performance_context or "No trades yet."}

## Patterns:
{patterns_context or "Need more trades for pattern detection."}

## Key Behaviors:
- Answer questions naturally and helpfully
- If asked about a trade setup, ALWAYS check lessons + assess_setup first
- If asked to open a trade, verify all risk rules before proceeding
- Proactively suggest calling reflect_on_performance after several trades
- Remember: your lessons are your most valuable asset. Reference them often.

You learn from every interaction. If you notice something worth remembering, save it.
""".strip()


# ──────────────────────────────────────────────
# Prompt Builder (Unified Interface)
# ──────────────────────────────────────────────

class CryptoPromptBuilder:
    """
    Unified prompt builder that integrates lessons, memory, and context.
    
    Usage:
        from LLM.lessons import LessonsManager
        from LLM.trading_memory import TradingMemory
        from LLM.prompts import CryptoPromptBuilder
        
        lessons = LessonsManager()
        memory = TradingMemory(lessons)
        builder = CryptoPromptBuilder(lessons, memory)
        
        # Build role-specific prompt
        prompt = builder.build("SCREENER", risk_config={...}, account_snapshot={...})
    """

    def __init__(self, lessons_manager=None, trading_memory=None):
        self.lessons = lessons_manager
        self.memory = trading_memory

    def build(
        self,
        role: str = "GENERAL",
        risk_config: Dict = None,
        account_snapshot: Dict = None,
        open_positions: List[Dict] = None,
        symbol: str = None,
        watchlist_info: str = "",
    ) -> str:
        """
        Build a complete system prompt for the given role.
        
        Args:
            role: "SCREENER", "MANAGER", or "GENERAL"
            risk_config: Risk configuration dict
            account_snapshot: Current account state
            open_positions: List of open position dicts (for MANAGER)
            symbol: Current symbol being analyzed (for lessons filtering)
            watchlist_info: Description of watchlist being scanned
            
        Returns:
            Complete system prompt string with lessons injected
        """
        # Gather context from lessons system
        lessons_context = ""
        performance_context = ""
        patterns_context = ""

        if self.lessons:
            lessons_role = role if role in ("SCREENER", "MANAGER") else None
            lessons_context = self.lessons.format_for_prompt(
                role=lessons_role, symbol=symbol, max_lessons=15
            )
            performance_context = self.lessons.format_performance_for_prompt(last_n=20)

        if self.memory:
            patterns_context = self.memory.format_for_prompt(symbol=symbol)

        # Build role-specific prompt
        if role == "SCREENER":
            return build_screener_prompt(
                lessons_context=lessons_context,
                performance_context=performance_context,
                patterns_context=patterns_context,
                risk_config=risk_config,
                account_snapshot=account_snapshot,
                watchlist_info=watchlist_info,
            )
        elif role == "MANAGER":
            return build_manager_prompt(
                lessons_context=lessons_context,
                performance_context=performance_context,
                patterns_context=patterns_context,
                risk_config=risk_config,
                open_positions=open_positions,
                account_snapshot=account_snapshot,
            )
        else:
            return build_general_prompt(
                lessons_context=lessons_context,
                performance_context=performance_context,
                patterns_context=patterns_context,
                risk_config=risk_config,
                account_snapshot=account_snapshot,
            )

    def get_trigger_message(self, role: str) -> str:
        """Get the default trigger message for a role's cycle."""
        if role == "SCREENER":
            return (
                "Run your screening cycle now. Scan the watchlist, analyze conditions, "
                "and decide whether to open a trade. Remember your lessons."
            )
        elif role == "MANAGER":
            return (
                "Run your management cycle now. Review all open positions and decide: "
                "STAY / MODIFY / CLOSE for each. Learn from any actions taken."
            )
        else:
            return "Ready. How can I help?"
