"""Shared utilities for Chastiefol modules.

This package holds cross-cutting concerns that don't belong to a single
vertical (DataFeed, LLM, Webhook, etc.) — circuit breakers, retry helpers,
structured logging adapters, and so on.
"""

from .circuit_breaker import CircuitBreaker, CircuitBreakerOpen, CircuitState

__all__ = ["CircuitBreaker", "CircuitBreakerOpen", "CircuitState"]
