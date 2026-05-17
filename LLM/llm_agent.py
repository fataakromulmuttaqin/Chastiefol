"""
Chastiefol — LLM Integration (ReAct Agent)
AI-driven market insight generation for XAUUSD trading decisions.

Framework: ReAct (Reason + Act)
Cycle: Thought → Action → Observation → Thought → ... → Final Answer

Supported LLM Providers:
- OpenAI (GPT-4o, GPT-4-turbo)
- Anthropic (Claude 3.5 Sonnet)
- Groq (Llama 3, Mixtral)
- OpenRouter (multi-model gateway)
- Local (Ollama)

Capabilities:
- Technical analysis interpretation
- News sentiment analysis
- Macro correlation (DXY, Treasury yields)
- Risk assessment
- Trade recommendation with probability
"""

import json
import re
import logging
import asyncio
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Callable
from enum import Enum
from abc import ABC, abstractmethod

import aiohttp

from Common.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from Common.health import HealthStatus, default_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("LLM.Agent")


class _LLMCallFailed(Exception):
    """Internal sentinel: an LLM call failed in a way the breaker should count.

    Distinct from CircuitBreakerOpen so the breaker counts it as a failure
    and re-raises it; chat() then translates it back to an empty string for
    callers that expect "no response" semantics.
    """


# ──────────────────────────────────────────────
# Configuration & Models
# ──────────────────────────────────────────────

class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    MINIMAX = "minimax"


@dataclass
class LLMConfig:
    """LLM provider configuration."""
    provider: LLMProvider = LLMProvider.OPENAI
    api_key: str = ""
    model: str = "gpt-4o"
    base_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout: int = 30
    max_react_steps: int = 5
    # Retry policy for transient errors (network, 429/5xx). Permanent
    # errors (4xx other than 429) are NOT retried — they indicate a bug
    # or auth issue and retrying just delays the failure.
    max_retries: int = 3
    retry_backoff_base: float = 1.0  # seconds; doubles per attempt

    # Provider-specific defaults
    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = LLMProvider(os.getenv("LLM_PROVIDER", "openai"))
        defaults = {
            LLMProvider.OPENAI: {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "api_key_env": "OPENAI_API_KEY",
            },
            LLMProvider.ANTHROPIC: {
                "base_url": "https://api.anthropic.com/v1",
                "model": "claude-sonnet-4-20250514",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            LLMProvider.GROQ: {
                "base_url": "https://api.groq.com/openai/v1",
                "model": "llama-3.1-70b-versatile",
                "api_key_env": "GROQ_API_KEY",
            },
            LLMProvider.OPENROUTER: {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "anthropic/claude-sonnet-4-20250514",
                "api_key_env": "OPENROUTER_API_KEY",
            },
            LLMProvider.OLLAMA: {
                "base_url": "http://localhost:11434/v1",
                "model": "llama3.1",
                "api_key_env": "",
            },
            LLMProvider.MINIMAX: {
                "base_url": "https://api.minimaxi.chat/v1",
                "model": "MiniMax-Text-01",
                "api_key_env": "MINIMAX_API_KEY",
            },
        }
        d = defaults[provider]
        return cls(
            provider=provider,
            api_key=os.getenv(d["api_key_env"], os.getenv("LLM_API_KEY", "")),
            model=os.getenv("LLM_MODEL", d["model"]),
            base_url=os.getenv("LLM_BASE_URL", d["base_url"]),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
        )


class InsightType(str, Enum):
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    MACRO = "macro"
    RISK = "risk"
    FULL = "full"


@dataclass
class MarketInsight:
    """AI-generated market insight."""
    summary: str = ""
    bias: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    confidence: float = 0.5
    probability_up: float = 0.5
    probability_down: float = 0.5
    key_levels: Dict[str, float] = field(default_factory=dict)
    risk_factors: List[str] = field(default_factory=list)
    trade_recommendation: str = "HOLD"  # BUY / SELL / HOLD
    reasoning: List[str] = field(default_factory=list)
    news_sentiment: str = "neutral"
    macro_context: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_response: str = ""


@dataclass
class ReActStep:
    """Single step in the ReAct reasoning chain."""
    step_num: int
    thought: str = ""
    action: str = ""
    action_input: str = ""
    observation: str = ""


# ──────────────────────────────────────────────
# Tool Definitions (for ReAct Agent)
# ──────────────────────────────────────────────

class AgentTool(ABC):
    """Base class for agent tools."""
    name: str = ""
    description: str = ""

    @abstractmethod
    async def execute(self, input_data: str) -> str:
        pass


class GetGoldPriceTool(AgentTool):
    """Fetch current gold price."""
    name = "get_gold_price"
    description = "Fetches the current real-time XAUUSD spot price. No input needed."

    def __init__(self, price_fn: Optional[Callable] = None):
        self._price_fn = price_fn
        self._last_price = 0.0

    async def execute(self, input_data: str) -> str:
        if self._price_fn:
            price = await self._price_fn()
            self._last_price = price
            return f"Current XAUUSD price: ${price:.2f}"
        return f"Current XAUUSD price: ${self._last_price:.2f} (cached)"

    def update_price(self, price: float):
        self._last_price = price


class CalculateIndicatorsTool(AgentTool):
    """Calculate technical indicators from market data."""
    name = "calculate_indicators"
    description = ("Calculates technical indicators for XAUUSD. "
                   "Returns RSI, MACD, EMA alignment, Bollinger Band position, ATR.")

    def __init__(self, indicator_fn: Optional[Callable] = None):
        self._indicator_fn = indicator_fn
        self._cached_indicators: Dict = {}

    async def execute(self, input_data: str) -> str:
        if self._indicator_fn:
            indicators = await self._indicator_fn()
            self._cached_indicators = indicators
        if self._cached_indicators:
            return json.dumps(self._cached_indicators, indent=2)
        return "No indicator data available."

    def update_indicators(self, indicators: Dict):
        self._cached_indicators = indicators


class SearchNewsTool(AgentTool):
    """Search for gold-related news headlines."""
    name = "search_gold_news"
    description = ("Searches recent financial news related to gold, Fed, inflation, "
                   "geopolitics. Input: optional search keywords.")

    def __init__(self, news_fn: Optional[Callable] = None):
        self._news_fn = news_fn
        self._cached_news: List[str] = []

    async def execute(self, input_data: str) -> str:
        if self._news_fn:
            news = await self._news_fn(input_data)
            self._cached_news = news
            return "\n".join(f"- {n}" for n in news[:5])
        if self._cached_news:
            return "\n".join(f"- {n}" for n in self._cached_news[:5])
        return "No recent gold news available."

    def update_news(self, headlines: List[str]):
        self._cached_news = headlines


class AnalyzeMacroTool(AgentTool):
    """Analyze macro correlations (DXY, Treasury yields)."""
    name = "analyze_macro"
    description = ("Analyzes macroeconomic factors affecting gold: DXY, "
                   "US Treasury yields, inflation expectations, Fed rate outlook.")

    def __init__(self, macro_fn: Optional[Callable] = None):
        self._macro_fn = macro_fn
        self._cached_macro: Dict = {}

    async def execute(self, input_data: str) -> str:
        if self._macro_fn:
            data = await self._macro_fn()
            self._cached_macro = data
        if self._cached_macro:
            return json.dumps(self._cached_macro, indent=2)
        return ("Macro context: DXY correlation inverse to gold. "
                "Rising yields typically bearish for gold. "
                "Geopolitical tension supports gold as safe haven.")

    def update_macro(self, data: Dict):
        self._cached_macro = data


class MarketStructureTool(AgentTool):
    """Get market structure analysis (SMC)."""
    name = "get_market_structure"
    description = ("Returns Smart Money Concepts analysis: market bias, "
                   "order blocks, FVGs, BOS/CHoCH detection.")

    def __init__(self):
        self._cached_structure: Dict = {}

    async def execute(self, input_data: str) -> str:
        if self._cached_structure:
            return json.dumps(self._cached_structure, indent=2)
        return "No market structure data available."

    def update_structure(self, structure: Dict):
        self._cached_structure = structure


# ──────────────────────────────────────────────
# Learning & Memory Tools (NEW — lessons system)
# ──────────────────────────────────────────────

class AddLessonTool(AgentTool):
    """Save a trading lesson to persistent memory."""
    name = "add_lesson"
    description = (
        "Save a new trading insight or rule to long-term memory. "
        "Call after notable events: winning pattern, losing pattern, false signal, "
        "or any observation worth remembering for future trades. "
        "Input: JSON with 'role' (SCREENER/MANAGER/RISK/GENERAL), "
        "'lesson' (the insight), and optional 'context' (what triggered it)."
    )

    def __init__(self, lessons_manager=None):
        self._lessons = lessons_manager

    async def execute(self, input_data: str) -> str:
        if not self._lessons:
            return "Lessons system not available."

        try:
            data = json.loads(input_data) if input_data.strip().startswith("{") else {}
        except json.JSONDecodeError:
            # Plain text lesson
            data = {"lesson": input_data, "role": "GENERAL"}

        lesson_text = data.get("lesson", input_data)
        role = data.get("role", "GENERAL")
        context = data.get("context", "")
        symbol = data.get("symbol", "")

        count = self._lessons.add_lesson(
            lesson=lesson_text,
            role=role,
            source="agent",
            context=context,
            symbol=symbol,
        )
        return f"Lesson saved (total: {count}). Role: {role}. Content: {lesson_text[:100]}"


class GetLessonsTool(AgentTool):
    """Retrieve stored lessons from memory."""
    name = "get_lessons"
    description = (
        "Retrieve your saved trading lessons. "
        "Input: optional role filter (SCREENER/MANAGER/RISK/GENERAL) "
        "or symbol filter (e.g. 'BTC/USDT'). Returns your accumulated wisdom."
    )

    def __init__(self, lessons_manager=None):
        self._lessons = lessons_manager

    async def execute(self, input_data: str) -> str:
        if not self._lessons:
            return "Lessons system not available."

        role = None
        symbol = None
        if input_data:
            input_upper = input_data.strip().upper()
            if input_upper in ("SCREENER", "MANAGER", "RISK", "GENERAL", "MARKET"):
                role = input_upper
            elif "/" in input_data:
                symbol = input_data.strip().upper()

        return self._lessons.format_for_prompt(role=role, symbol=symbol, max_lessons=15)


class GetPerformanceTool(AgentTool):
    """Get trading performance summary with statistics."""
    name = "get_performance_summary"
    description = (
        "Get your win rate, total PnL, average pips, streak info, and last 10 trades. "
        "Use to evaluate if your strategy is working or needs adjustment. "
        "Input: optional number of recent trades to analyze (default: all)."
    )

    def __init__(self, lessons_manager=None):
        self._lessons = lessons_manager

    async def execute(self, input_data: str) -> str:
        if not self._lessons:
            return "Performance tracking not available."

        last_n = None
        if input_data and input_data.strip().isdigit():
            last_n = int(input_data.strip())

        summary = self._lessons.get_performance_summary(last_n=last_n)
        return json.dumps(summary, indent=2, default=str)


class AssessSetupTool(AgentTool):
    """Assess a proposed trade setup against historical patterns."""
    name = "assess_setup"
    description = (
        "Before opening a trade, assess it against your historical patterns. "
        "Input: JSON with 'symbol', 'direction' (buy/sell), 'confidence' (0-1). "
        "Returns whether similar setups historically win or lose."
    )

    def __init__(self, trading_memory=None):
        self._memory = trading_memory

    async def execute(self, input_data: str) -> str:
        if not self._memory:
            return "Trading memory not available."

        try:
            data = json.loads(input_data)
        except json.JSONDecodeError:
            return "Invalid input. Provide JSON: {\"symbol\": \"BTC/USDT\", \"direction\": \"buy\", \"confidence\": 0.65}"

        assessment = self._memory.assess_setup(
            symbol=data.get("symbol", ""),
            direction=data.get("direction", ""),
            confidence=data.get("confidence", 0.5),
            category=data.get("category", ""),
        )
        return json.dumps(assessment, indent=2)


class ReflectTool(AgentTool):
    """Trigger self-reflection on recent performance."""
    name = "reflect_on_performance"
    description = (
        "Analyze your recent trading performance and generate new lessons. "
        "Call this after a series of trades to learn what's working and what isn't. "
        "No input needed — analyzes your last 20 trades automatically."
    )

    def __init__(self, lessons_manager=None, trading_memory=None):
        self._lessons = lessons_manager
        self._memory = trading_memory

    async def execute(self, input_data: str) -> str:
        if not self._lessons:
            return "Lessons system not available."

        # Get performance + patterns context
        perf = self._lessons.format_performance_for_prompt(last_n=20)
        patterns = ""
        if self._memory:
            self._memory.analyze_patterns()
            patterns = self._memory.format_for_prompt()

        suggestions = ""
        if self._memory:
            config_suggestions = self._memory.suggest_config_changes()
            if config_suggestions:
                suggestions = "\nConfig suggestions:\n" + json.dumps(config_suggestions, indent=2)

        return (
            f"## Performance Review:\n{perf}\n\n"
            f"## Detected Patterns:\n{patterns}\n"
            f"{suggestions}\n\n"
            f"Based on this data, what lessons should I add? "
            f"Use add_lesson to save any new insights."
        )


# ──────────────────────────────────────────────
# LLM Client (Multi-Provider)
# ──────────────────────────────────────────────

class LLMClient:
    """Unified LLM client supporting multiple providers."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        # Trip the breaker after several consecutive request failures so a
        # dead/rate-limited provider doesn't burn through every analyse()
        # cycle. Recovery_timeout is generous — LLM outages are typically
        # multi-minute and we'd rather degrade to HOLD than hammer the API.
        self._breaker = CircuitBreaker(
            name=f"llm:{config.provider.value}",
            failure_threshold=int(os.getenv("LLM_BREAKER_THRESHOLD", "5")),
            recovery_timeout=float(os.getenv("LLM_BREAKER_COOLDOWN", "60")),
        )
        # Health reporting: register a "llm" component so /health shows
        # this provider explicitly even before the first call.
        self._health = default_registry()
        self._health_component = f"llm:{config.provider.value}"
        self._health.register(self._health_component)

    async def init(self):
        timeout_env = int(os.getenv("LLM_TIMEOUT", "120"))
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_env)
        )

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def chat(self, messages: List[Dict[str, str]]) -> str:
        """Send chat completion request to the configured LLM provider.

        Calls flow through a circuit breaker so a dead/rate-limited
        provider fails fast instead of stalling the trading loop. When
        the breaker is OPEN we return an empty string — callers already
        treat that as "LLM unavailable, fall back to HOLD" — and log the
        skip at debug level to avoid spamming.
        """
        if not self._session:
            await self.init()

        async def _do_call() -> str:
            if self.config.provider == LLMProvider.ANTHROPIC:
                result = await self._anthropic_chat(messages)
            else:
                result = await self._openai_compatible_chat(messages)
            # Empty string from _post_with_retry means a permanent error
            # (4xx auth/model bug) or exhausted retries — both should count
            # toward tripping the breaker so we stop hammering the provider.
            if not result:
                raise _LLMCallFailed("LLM returned empty response")
            return result

        try:
            result = await self._breaker.call(_do_call)
            self._health.report(
                self._health_component,
                HealthStatus.HEALTHY,
                detail=f"breaker={self._breaker.state.value}",
            )
            return result
        except CircuitBreakerOpen as e:
            log.debug("Skipping LLM call: %s", e)
            self._health.report(
                self._health_component,
                HealthStatus.UNHEALTHY,
                detail=str(e),
                breaker=self._breaker.state.value,
            )
            return ""
        except _LLMCallFailed:
            # Already logged by _post_with_retry; just propagate the empty.
            self._health.report(
                self._health_component,
                # If the breaker tripped we surface UNHEALTHY; otherwise
                # this is a single-call failure -> DEGRADED.
                HealthStatus.UNHEALTHY
                if self._breaker.state.value != "closed"
                else HealthStatus.DEGRADED,
                detail="LLM call failed",
                breaker=self._breaker.state.value,
            )
            return ""

    # ── Shared retry helper ──────────────────────────────────────
    # Retries on:
    #   - asyncio.TimeoutError / aiohttp.ClientError (transient network)
    #   - HTTP 408 (Request Timeout), 425 (Too Early), 429 (Rate Limit)
    #   - HTTP 5xx (server errors)
    # Does NOT retry on 4xx (bug / auth / model-not-found) — those are
    # permanent and retrying just wastes the user's tokens & time.

    _RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

    async def _post_with_retry(
        self,
        url: str,
        payload: Dict,
        headers: Dict,
        provider_label: str,
    ) -> Optional[Dict]:
        """POST JSON with retry/backoff on transient errors.

        Returns parsed JSON on success, None on permanent failure or after
        exhausting retries. Logs every failure with provider context so
        operators can spot rate-limits vs misconfiguration.
        """
        max_attempts = max(1, self.config.max_retries)
        for attempt in range(1, max_attempts + 1):
            try:
                async with self._session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()

                    # Capture body for diagnostic logging (truncated).
                    body = await resp.text()
                    if resp.status in self._RETRYABLE_STATUSES and attempt < max_attempts:
                        # Honor Retry-After when present (seconds or HTTP-date),
                        # otherwise fall back to exponential backoff.
                        delay = self._compute_backoff(resp.headers.get("Retry-After"), attempt)
                        log.warning(
                            "%s API %s on attempt %d/%d — retrying in %.1fs: %s",
                            provider_label, resp.status, attempt, max_attempts, delay, body[:200],
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Permanent error or final attempt.
                    log.error(
                        "%s API error %s (final attempt %d/%d): %s",
                        provider_label, resp.status, attempt, max_attempts, body[:200],
                    )
                    return None

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                if attempt < max_attempts:
                    delay = self._compute_backoff(None, attempt)
                    log.warning(
                        "%s request failed (%s) on attempt %d/%d — retrying in %.1fs: %s",
                        provider_label, type(e).__name__, attempt, max_attempts, delay, e,
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error(
                    "%s request failed after %d attempts: %s: %s",
                    provider_label, max_attempts, type(e).__name__, e,
                )
                return None

        return None  # unreachable, but keeps type-checkers happy

    def _compute_backoff(self, retry_after_header: Optional[str], attempt: int) -> float:
        """Compute backoff delay; respects Retry-After when sane."""
        if retry_after_header:
            try:
                # Header is either delta-seconds (int) or HTTP-date.
                delay = float(retry_after_header)
                # Clamp to a sane range so a misbehaving server can't
                # stall us for hours.
                return max(0.0, min(delay, 60.0))
            except ValueError:
                pass  # non-numeric (HTTP-date) — fall through to backoff

        # Exponential backoff: base * 2^(attempt-1), with a 30s ceiling.
        return min(self.config.retry_backoff_base * (2 ** (attempt - 1)), 30.0)

    async def _openai_compatible_chat(self, messages: List[Dict[str, str]]) -> str:
        """OpenAI-compatible API (works for OpenAI, Groq, OpenRouter, Ollama)."""
        url = f"{self.config.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}

        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        data = await self._post_with_retry(url, payload, headers, "LLM")
        if not data:
            return ""
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            log.error(f"LLM response missing expected fields: {e} | payload={data!r}")
            return ""

    async def _anthropic_chat(self, messages: List[Dict[str, str]]) -> str:
        """Anthropic Messages API."""
        url = f"{self.config.base_url}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }

        # Extract system message
        system_msg = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                chat_messages.append(msg)

        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": chat_messages,
        }
        if system_msg:
            payload["system"] = system_msg

        data = await self._post_with_retry(url, payload, headers, "Anthropic")
        if not data:
            return ""
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            log.error(f"Anthropic response missing expected fields: {e} | payload={data!r}")
            return ""



# ──────────────────────────────────────────────
# ReAct Agent
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are Chastiefol, a Senior Commodity & Crypto Strategist AI with deep experience in trading precious metals (XAUUSD) and cryptocurrencies (all Binance spot pairs). You are conservative, data-driven, and highly analytical.

You analyze markets using:
1. Technical analysis (indicators, price action, Smart Money Concepts)
2. Macroeconomic factors (Fed policy, DXY, Treasury yields, inflation, BTC dominance)
3. News sentiment (geopolitical tension, economic data, crypto narratives)
4. Risk assessment (volatility, correlation shifts)
5. YOUR OWN LESSONS — wisdom from past trades (wins AND losses)

You follow the ReAct reasoning framework:
- Thought: Analyze what you know and what you need
- Action: Use a tool to gather information
- Observation: Process the tool output
- Repeat until you have enough information
- Final Answer: Provide structured market insight

Available tools:
{tools_description}

## YOUR TRADING LESSONS (from experience — NEVER violate these):
{lessons_context}

## YOUR PERFORMANCE STATS:
{performance_context}

## TRADING PATTERNS (statistical — from your history):
{patterns_context}

CRITICAL RULES:
- ALWAYS check your lessons before making decisions. Past mistakes are expensive teachers.
- ALWAYS call assess_setup before recommending a trade to check historical patterns.
- After a notable outcome (win/loss/observation), call add_lesson to remember it.
- After every 5 trades, call reflect_on_performance to find new patterns.
- Never give financial advice. Provide statistical probabilities only.
- Always cite specific data points for your conclusions.
- Be conservative — when in doubt, recommend HOLD.
- Focus on risk management above profit potential.

Respond using this exact format:
Thought: [your reasoning]
Action: [tool_name]
Action Input: [input for the tool]

OR when you have enough information:
Thought: [final reasoning]
Final Answer: [your structured response in JSON format]

The Final Answer MUST be valid JSON with these fields:
{{
  "summary": "concise market overview",
  "bias": "BULLISH|BEARISH|NEUTRAL",
  "confidence": 0.0-1.0,
  "probability_up": 0.0-1.0,
  "probability_down": 0.0-1.0,
  "key_levels": {{"support": price, "resistance": price}},
  "risk_factors": ["factor1", "factor2"],
  "trade_recommendation": "BUY|SELL|HOLD",
  "reasoning": ["reason1", "reason2", "reason3"],
  "lessons_applied": ["which lessons influenced this decision"]
}}"""


class LLMInsightAgent:
    """
    ReAct-based LLM agent for generating market insights.
    
    Usage:
        config = LLMConfig.from_env()
        agent = LLMInsightAgent(config)
        await agent.initialize()
        
        # Update context
        agent.update_price(2365.50)
        agent.update_indicators({...})
        agent.update_news(["Fed holds rates...", ...])
        
        # Get insight
        insight = await agent.analyze()
        print(insight.summary, insight.bias, insight.trade_recommendation)
        
        await agent.shutdown()
    """

    def __init__(self, config: LLMConfig = None, lessons_manager=None, trading_memory=None):
        self.config = config or LLMConfig.from_env()
        self.client = LLMClient(self.config)

        # Learning & Memory System
        self.lessons_manager = lessons_manager
        self.trading_memory = trading_memory

        # If not provided, create defaults
        if not self.lessons_manager:
            try:
                from .lessons import LessonsManager
                self.lessons_manager = LessonsManager()
            except ImportError:
                pass

        if not self.trading_memory and self.lessons_manager:
            try:
                from .trading_memory import TradingMemory
                self.trading_memory = TradingMemory(self.lessons_manager)
            except ImportError:
                pass

        # Tools
        self.price_tool = GetGoldPriceTool()
        self.indicator_tool = CalculateIndicatorsTool()
        self.news_tool = SearchNewsTool()
        self.macro_tool = AnalyzeMacroTool()
        self.structure_tool = MarketStructureTool()

        # Learning tools
        self.add_lesson_tool = AddLessonTool(self.lessons_manager)
        self.get_lessons_tool = GetLessonsTool(self.lessons_manager)
        self.get_perf_tool = GetPerformanceTool(self.lessons_manager)
        self.assess_setup_tool = AssessSetupTool(self.trading_memory)
        self.reflect_tool = ReflectTool(self.lessons_manager, self.trading_memory)

        self.tools: Dict[str, AgentTool] = {
            self.price_tool.name: self.price_tool,
            self.indicator_tool.name: self.indicator_tool,
            self.news_tool.name: self.news_tool,
            self.macro_tool.name: self.macro_tool,
            self.structure_tool.name: self.structure_tool,
            # Learning & memory tools
            self.add_lesson_tool.name: self.add_lesson_tool,
            self.get_lessons_tool.name: self.get_lessons_tool,
            self.get_perf_tool.name: self.get_perf_tool,
            self.assess_setup_tool.name: self.assess_setup_tool,
            self.reflect_tool.name: self.reflect_tool,
        }

        self._react_history: List[ReActStep] = []
        self._initialized = False

        log.info(f"LLM Agent initialized | Provider: {self.config.provider.value} | "
                 f"Model: {self.config.model} | "
                 f"Lessons: {'enabled' if self.lessons_manager else 'disabled'}")

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    @staticmethod
    def _strip_thinking_tags(text: str) -> str:
        """
        Remove "thinking" / reasoning blocks emitted by various LLM providers.

        Supports the following formats (case-insensitive, multi-line):
          - HTML/XML style:   <think>...</think>, <thinking>...</thinking>
          - Chinese full-width parens: （think ...） or （thinking ...）
          - Markdown-style:   ```thinking ... ```

        Also strips any leftover, unterminated opening tag (e.g. the model
        was truncated mid-thought) so we never carry partial reasoning into
        downstream JSON parsing.

        Args:
            text: Raw LLM response text.

        Returns:
            Text with all reasoning blocks removed and surrounding
            whitespace trimmed. Returns empty string if input is falsy.
        """
        if not text:
            return ""

        # 1) Closed HTML/XML thinking tags: <think>...</think>, <thinking>...</thinking>
        text = re.sub(
            r'<\s*think(?:ing)?\s*>[\s\S]*?<\s*/\s*think(?:ing)?\s*>',
            '',
            text,
            flags=re.IGNORECASE,
        )

        # 2) Closed Chinese full-width-paren thinking blocks: （think ...） / （thinking ...）
        text = re.sub(
            r'（\s*think(?:ing)?\b[\s\S]*?）',
            '',
            text,
            flags=re.IGNORECASE,
        )

        # 3) Closed markdown fenced thinking blocks: ```thinking ... ```
        text = re.sub(
            r'```\s*think(?:ing)?\b[\s\S]*?```',
            '',
            text,
            flags=re.IGNORECASE,
        )

        # 4) Stray unterminated opening tags — drop everything from the tag
        #    to the end of the string so we don't carry partial reasoning.
        text = re.sub(
            r'<\s*think(?:ing)?\s*>[\s\S]*$',
            '',
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r'（\s*think(?:ing)?\b[\s\S]*$',
            '',
            text,
            flags=re.IGNORECASE,
        )

        return text.strip()

    async def initialize(self):
        """Initialize the LLM client."""
        await self.client.init()
        self._initialized = True
        log.info("LLM Agent ready.")

    async def shutdown(self):
        """Cleanup resources."""
        await self.client.close()
        self._initialized = False

    # ──────────────────────────────────────────
    # Context Updates
    # ──────────────────────────────────────────

    def update_price(self, price: float):
        """Update current gold price for the agent."""
        self.price_tool.update_price(price)

    def update_indicators(self, indicators: Dict):
        """Update technical indicators data."""
        self.indicator_tool.update_indicators(indicators)

    def update_news(self, headlines: List[str]):
        """Update news headlines."""
        self.news_tool.update_news(headlines)

    def update_macro(self, macro_data: Dict):
        """Update macroeconomic data."""
        self.macro_tool.update_macro(macro_data)

    def update_market_structure(self, structure: Dict):
        """Update SMC market structure analysis."""
        self.structure_tool.update_structure(structure)

    # ──────────────────────────────────────────
    # Main Analysis
    # ──────────────────────────────────────────

    async def analyze(self, query: str = None, role: str = None, skip_tools: bool = False) -> MarketInsight:
        """
        Run ReAct reasoning loop to generate market insight.
        Returns a structured MarketInsight object.
        
        Args:
            query: Custom analysis query (optional)
            role: Agent role for lessons filtering (SCREENER/MANAGER/GENERAL)
        """
        if not self._initialized:
            await self.initialize()

        if not query:
            query = ("Analyze the current market conditions. "
                     "Provide a comprehensive technical and fundamental overview "
                     "with a trade recommendation and probability assessment. "
                     "Remember to check your lessons and assess any setup against history.")

        # Build tools description
        tools_desc = "\n".join(
            f"- {name}: {tool.description}" for name, tool in self.tools.items()
        )

        # Inject lessons context into system prompt
        lessons_context = "No lessons yet — you're starting fresh."
        performance_context = "No trades recorded yet."
        patterns_context = "No patterns detected yet."

        if self.lessons_manager:
            lessons_context = self.lessons_manager.format_for_prompt(
                role=role, max_lessons=15
            )
            performance_context = self.lessons_manager.format_performance_for_prompt(
                last_n=20
            )

        if self.trading_memory:
            patterns_context = self.trading_memory.format_for_prompt()

        system = SYSTEM_PROMPT.format(
            tools_description=tools_desc,
            lessons_context=lessons_context,
            performance_context=performance_context,
            patterns_context=patterns_context,
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ]

        self._react_history = []

        # ── Fast path: single-shot (no tools, no ReAct loop) ──
        if skip_tools:
            response = await self.client.chat(messages)
            if response:
                # Try Final Answer format first, then strip thinking + parse JSON
                if "Final Answer:" in response:
                    return self._parse_final_answer(response)

                # Strip thinking tags and try direct JSON parse
                # Supports HTML <think>/<thinking>, Chinese （think ...）, and
                # markdown ```thinking ... ``` blocks (see _strip_thinking_tags).
                clean = self._strip_thinking_tags(response)

                result = self._parse_json_insight(clean)
                if result.trade_recommendation in ("BUY", "SELL", "HOLD"):
                    log.info(f"[LLM] Direct JSON → {result.trade_recommendation}: {result.summary[:80]}")
                    return result

                # Last resort: extract BUY/SELL/HOLD and reasoning from thinking
                return self._extract_from_thinking(response)
            return MarketInsight(summary="LLM call failed — no response.", bias="NEUTRAL", trade_recommendation="HOLD")

        # ReAct Loop
        for step in range(1, self.config.max_react_steps + 1):
            response = await self.client.chat(messages)
            if not response:
                log.warning(f"Empty LLM response at step {step}")
                break

            log.debug(f"ReAct step {step}: {response[:100]}...")

            # Check for Final Answer
            if "Final Answer:" in response:
                return self._parse_final_answer(response)

            # Parse Action
            react_step = self._parse_react_step(response, step)
            self._react_history.append(react_step)

            if react_step.action and react_step.action in self.tools and not skip_tools:
                # Execute tool
                observation = await self.tools[react_step.action].execute(
                    react_step.action_input
                )
                react_step.observation = observation

                # Add to conversation
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": f"Observation: {observation}"
                })
            else:
                # No valid action — ask for final answer
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": ("Based on the information gathered, "
                                "please provide your Final Answer in JSON format.")
                })

        # If loop exhausted, try to get final answer
        messages.append({
            "role": "user",
            "content": "Please provide your Final Answer now in JSON format."
        })
        response = await self.client.chat(messages)
        if response:
            return self._parse_final_answer(response)

        # Fallback
        return MarketInsight(
            summary="Unable to generate insight — LLM did not respond.",
            bias="NEUTRAL",
            trade_recommendation="HOLD",
        )

    async def quick_analysis(self, price: float, indicators: Dict) -> MarketInsight:
        """
        Quick single-shot analysis without ReAct loop.
        Faster but less thorough than full analyze().
        """
        if not self._initialized:
            await self.initialize()

        prompt = f"""Analyze XAUUSD with this data:

Current Price: ${price:.2f}
Indicators: {json.dumps(indicators, indent=2)}

Provide a brief JSON response with:
- summary (1-2 sentences)
- bias (BULLISH/BEARISH/NEUTRAL)
- confidence (0.0-1.0)
- trade_recommendation (BUY/SELL/HOLD)
- reasoning (2-3 key reasons)

Respond ONLY with valid JSON."""

        messages = [
            {"role": "system", "content": "You are a gold market analyst. Be concise and data-driven."},
            {"role": "user", "content": prompt},
        ]

        response = await self.client.chat(messages)
        if response:
            return self._parse_json_insight(response)

        return MarketInsight(summary="Quick analysis failed.", bias="NEUTRAL")

    # ──────────────────────────────────────────
    # Parsing Helpers
    # ──────────────────────────────────────────

    def _parse_react_step(self, response: str, step_num: int) -> ReActStep:
        """Parse a ReAct response into structured step."""
        step = ReActStep(step_num=step_num)
        lines = response.strip().split("\n")

        for line in lines:
            line_stripped = line.strip()
            if line_stripped.startswith("Thought:"):
                step.thought = line_stripped[len("Thought:"):].strip()
            elif line_stripped.startswith("Action:"):
                step.action = line_stripped[len("Action:"):].strip()
            elif line_stripped.startswith("Action Input:"):
                step.action_input = line_stripped[len("Action Input:"):].strip()

        return step

    def _parse_final_answer(self, response: str) -> MarketInsight:
        """Parse Final Answer from ReAct response."""
        # Extract JSON from response
        json_str = ""
        if "Final Answer:" in response:
            json_str = response.split("Final Answer:")[-1].strip()
        else:
            json_str = response.strip()

        return self._parse_json_insight(json_str)

    def _parse_json_insight(self, text: str) -> MarketInsight:
        """Parse JSON string into MarketInsight. Handles thinking tags and partial responses."""
        # ── Step 1: Strip thinking tags ──
        # Supports HTML <think>/<thinking>, Chinese （think ...）, and markdown
        # ```thinking ... ``` blocks. See _strip_thinking_tags for full grammar.
        text = self._strip_thinking_tags(text)

        json_str = text.strip()

        # Handle markdown code blocks
        if "```json" in json_str:
            json_str = json_str.split("```json")[-1].split("```")[0].strip()
        elif "```" in json_str:
            parts = json_str.split("```")
            for part in parts:
                if "trade_recommendation" in part or "summary" in part:
                    json_str = part.strip()
                    break

        # Find JSON object boundaries
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start >= 0 and end > start:
            json_str = json_str[start:end]

        try:
            data = json.loads(json_str)
            return MarketInsight(
                summary=data.get("summary", ""),
                bias=data.get("bias", "NEUTRAL").upper(),
                confidence=float(data.get("confidence", 0.5)),
                probability_up=float(data.get("probability_up", 0.5)),
                probability_down=float(data.get("probability_down", 0.5)),
                key_levels=data.get("key_levels", {}),
                risk_factors=data.get("risk_factors", []),
                trade_recommendation=data.get("trade_recommendation", "HOLD").upper(),
                reasoning=data.get("reasoning", []),
                news_sentiment=data.get("news_sentiment", "neutral"),
                macro_context=data.get("macro_context", ""),
                raw_response=text,
            )
        except json.JSONDecodeError as e:
            log.warning(f"Failed to parse LLM JSON: {e}")
            # Fallback: extract what we can from raw text
            return MarketInsight(
                summary=text[:200] if text else "Parse error",
                bias="NEUTRAL",
                trade_recommendation="HOLD",
                raw_response=text,
            )

    def _extract_from_thinking(self, text: str) -> MarketInsight:
        """
        Last-resort parser: extract BUY/SELL/HOLD and reasoning from raw thinking text.
        Used when LLM doesn't output proper JSON.
        """
        # Remove thinking tags first
        # Supports HTML <think>/<thinking>, Chinese （think ...）, and markdown
        # ```thinking ... ``` blocks. See _strip_thinking_tags for full grammar.
        clean = self._strip_thinking_tags(text)
        text_lower = clean.lower()

        # Find recommendation - look in the clean text
        recommendation = "HOLD"
        reason_snippet = ""

        # Look for Final Answer section
        if "final answer:" in text_lower:
            fa_part = clean.split("final answer:")[-1][:300]
            if '"trade_recommendation"' in fa_part:
                match = re.search(r'"trade_recommendation"\s*:\s*"(\w+)"', fa_part)
                if match:
                    recommendation = match.group(1).upper()
            elif any(w in fa_part for w in ["buy", "sell"]):
                # Look for positive indicators
                for word in ["buy", "sell"]:
                    idx = fa_part.find(word)
                    if idx > 0 and "not" not in fa_part[max(0, idx-10):idx]:
                        recommendation = word.upper()
                        break
            # Extract reason from Final Answer paragraph
            lines = [l.strip() for l in fa_part.split("\n") if l.strip()][:5]
            reason_snippet = " ".join(lines)[:200]
        else:
            # Look for recommendation keywords in the thinking
            for word in ["buy", "sell", "hold", "approve", "reject", "signal"]:
                matches = [(m.start(), m.group()) for m in re.finditer(rf'\b{word}\b', text_lower)]
                for _, match in matches:
                    # Get context around the match
                    idx = text_lower.find(match)
                    context = text[max(0, idx-20):idx+80]
                    if "not" not in context.lower().split(word)[0][-20:]:
                        if match in ["buy", "sell"]:
                            recommendation = match.upper()
                        reason_snippet = context.strip()[:150]
                        break
                if reason_snippet:
                    break

        # Determine bias
        bias = "NEUTRAL"
        if "bullish" in text_lower or "buy" in text_lower:
            bias = "BULLISH"
        elif "bearish" in text_lower or "sell" in text_lower:
            bias = "BEARISH"

        log.warning(f"[LLM] Extracted from thinking → {recommendation}: {reason_snippet[:80]}")
        return MarketInsight(
            summary=reason_snippet[:300] if reason_snippet else f"Signal {recommendation}",
            bias=bias,
            confidence=0.6,
            trade_recommendation=recommendation,
            raw_response=text,
        )

    # ──────────────────────────────────────────
    # Status
    # ──────────────────────────────────────────

    @property
    def react_history(self) -> List[ReActStep]:
        """Get the reasoning trace from last analysis."""
        return self._react_history

    def get_status(self) -> Dict:
        return {
            "initialized": self._initialized,
            "provider": self.config.provider.value,
            "model": self.config.model,
            "tools": list(self.tools.keys()),
            "react_steps_last": len(self._react_history),
        }


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

async def main():
    """Test LLM agent standalone."""
    config = LLMConfig.from_env()
    agent = LLMInsightAgent(config)
    await agent.initialize()

    # Mock data
    agent.update_price(2365.50)
    agent.update_indicators({
        "rsi_14": 58.3,
        "macd_signal": "bullish_cross",
        "ema_20": 2360.0,
        "ema_50": 2345.0,
        "ema_100": 2320.0,
        "ema_200": 2280.0,
        "ema_alignment": "bullish",
        "bollinger_position": "upper_half",
        "atr_14": 18.5,
        "psar": "below_price",
    })
    agent.update_news([
        "Fed signals potential rate cut in September",
        "Gold demand from central banks hits record",
        "US CPI comes in below expectations",
        "Geopolitical tensions rise in Middle East",
    ])
    agent.update_macro({
        "dxy": 104.2,
        "dxy_trend": "weakening",
        "us_10y_yield": 4.35,
        "yield_trend": "declining",
        "fed_outlook": "dovish pivot expected",
        "inflation": "cooling",
    })

    # Full analysis
    insight = await agent.analyze()
    log.info(f"\n{'='*50}")
    log.info(f"  AI MARKET INSIGHT")
    log.info(f"{'='*50}")
    log.info(f"  Summary: {insight.summary}")
    log.info(f"  Bias: {insight.bias}")
    log.info(f"  Confidence: {insight.confidence*100:.0f}%")
    log.info(f"  Recommendation: {insight.trade_recommendation}")
    log.info(f"  P(Up): {insight.probability_up*100:.0f}%")
    log.info(f"  P(Down): {insight.probability_down*100:.0f}%")
    for r in insight.reasoning:
        log.info(f"    • {r}")
    log.info(f"{'='*50}")

    await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
