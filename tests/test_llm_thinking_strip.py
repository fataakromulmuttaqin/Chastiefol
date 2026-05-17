"""
Unit tests for LLMInsightAgent._strip_thinking_tags.

These tests pin down the regression fixed in the "fix: strip both HTML and
Chinese （） thinking tags in LLM parser" commit. The previous regex was
malformed (it used `<think>[\\s\\S]*?` with no closing tag, so it matched
zero characters and stripped nothing, while the second variant looked for
`）` after an unrelated open tag). The replacement helper must:

  - Strip closed HTML <think>...</think> and <thinking>...</thinking>
  - Strip closed Chinese （think ...） / （thinking ...） blocks
  - Strip closed markdown ```thinking ... ``` fences
  - Strip stray unterminated openers (model truncated mid-thought)
  - Preserve all non-thinking content (especially the JSON payload)
  - Be case-insensitive
"""

import os
import sys
import types
import unittest

# Make repo importable when running `pytest` from anywhere.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# `LLM.llm_agent` imports aiohttp at module-load time. The helper under test
# (`_strip_thinking_tags`) doesn't actually need it, so when aiohttp isn't
# installed (minimal sandboxes) we provide a stub. CI installs the real
# package via requirements.txt and this stub becomes a no-op.
#
# We register both `aiohttp` and `aiohttp.web` so other test modules that run
# under the same `unittest discover` session (e.g. webhook tests) can also
# import `from aiohttp import web`.
if "aiohttp" not in sys.modules:
    try:  # pragma: no cover — environment-dependent
        import aiohttp  # noqa: F401
    except ImportError:
        aiohttp_stub = types.ModuleType("aiohttp")
        aiohttp_web = types.ModuleType("aiohttp.web")
        aiohttp_web.Application = object
        aiohttp_web.AppRunner = object
        aiohttp_web.TCPSite = object
        aiohttp_web.Request = object
        aiohttp_web.Response = object
        aiohttp_web.json_response = lambda *a, **kw: None
        aiohttp_stub.web = aiohttp_web
        sys.modules["aiohttp"] = aiohttp_stub
        sys.modules["aiohttp.web"] = aiohttp_web


def _strip():
    """Import the helper lazily so import errors surface as a useful skip."""
    try:
        from LLM.llm_agent import LLMInsightAgent  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover — surfaces missing dep clearly
        raise unittest.SkipTest(f"LLM module unavailable: {exc}")
    return LLMInsightAgent._strip_thinking_tags


class StripThinkingTagsTest(unittest.TestCase):
    def setUp(self):
        self.strip = _strip()

    # ── Happy paths ────────────────────────────────────────────────

    def test_strips_html_think_tag(self):
        text = "<think>secret reasoning</think>{\"trade_recommendation\": \"BUY\"}"
        self.assertEqual(
            self.strip(text),
            '{"trade_recommendation": "BUY"}',
        )

    def test_strips_html_thinking_tag(self):
        text = "<thinking>secret reasoning</thinking>final"
        self.assertEqual(self.strip(text), "final")

    def test_strips_chinese_thinking_block(self):
        text = "（thinking 我在分析金价走势）{\"bias\": \"BULLISH\"}"
        self.assertEqual(self.strip(text), '{"bias": "BULLISH"}')

    def test_strips_markdown_thinking_fence(self):
        text = "```thinking\nlet me reason about this\n```\n{\"ok\": 1}"
        self.assertEqual(self.strip(text), '{"ok": 1}')

    def test_strips_multiple_blocks(self):
        text = (
            "<think>step 1</think>"
            "intermediate "
            "<thinking>step 2</thinking>"
            "final"
        )
        # The regex strips ONLY the tag spans; the single space between the
        # original "intermediate " and the second tag is preserved.
        self.assertEqual(self.strip(text), "intermediate final")

    def test_case_insensitive(self):
        text = "<THINK>upper</THINK><Thinking>mixed</Thinking>payload"
        self.assertEqual(self.strip(text), "payload")

    def test_handles_multiline_content(self):
        text = (
            "<think>\n"
            "line one\n"
            "line two with </not-a-close>\n"
            "</think>\n"
            "kept"
        )
        self.assertEqual(self.strip(text), "kept")

    # ── Truncated / unterminated cases ─────────────────────────────

    def test_strips_unterminated_html_opener(self):
        # Model was cut off before closing the tag — drop everything
        # from the opener onward so we don't carry partial reasoning.
        text = '{"trade_recommendation":"SELL"}<think>truncated...'
        self.assertEqual(self.strip(text), '{"trade_recommendation":"SELL"}')

    def test_strips_unterminated_chinese_opener(self):
        text = '{"x":1}（thinking 中途被截断'
        self.assertEqual(self.strip(text), '{"x":1}')

    # ── Negative / no-op cases ─────────────────────────────────────

    def test_returns_input_when_no_tags(self):
        text = '{"trade_recommendation":"HOLD"}'
        self.assertEqual(self.strip(text), text)

    def test_empty_input(self):
        self.assertEqual(self.strip(""), "")
        self.assertEqual(self.strip(None), "")

    def test_preserves_lookalike_tags(self):
        # `<thinker>` is NOT a thinking tag — must be preserved.
        text = "<thinker>persona</thinker>kept"
        self.assertEqual(self.strip(text), text)

    def test_does_not_strip_chinese_paren_without_keyword(self):
        # Generic Chinese parens should not be touched.
        text = "价格上涨（参考新闻）后续"
        self.assertEqual(self.strip(text), text)


if __name__ == "__main__":
    unittest.main()
