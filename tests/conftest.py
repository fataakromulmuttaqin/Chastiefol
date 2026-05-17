"""
Pytest configuration for Chastiefol tests.

The LLM module imports `aiohttp` at module load time. In environments where
aiohttp isn't installed (e.g. minimal CI sandboxes), we provide a stub so
pure-logic tests like `_strip_thinking_tags` can still run.

CI environments that install `requirements.txt` will get the real aiohttp
and this stub becomes a no-op.
"""

import sys
import types


def pytest_configure(config):  # noqa: D401
    """Ensure heavy/optional deps don't block import of unit-tested modules."""
    if "aiohttp" not in sys.modules:
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            sys.modules["aiohttp"] = types.ModuleType("aiohttp")
