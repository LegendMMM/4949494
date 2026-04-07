"""Browser engine factory."""

from __future__ import annotations

import logging

from ticket_bot.browser.base import BrowserEngine

logger = logging.getLogger(__name__)


def create_engine(engine_name: str) -> BrowserEngine:
    """Create a browser engine instance."""
    name = engine_name.lower().strip()

    if name == "nodriver":
        try:
            from ticket_bot.browser.nodriver_engine import NodriverEngine
        except Exception as exc:
            logger.warning(
                "nodriver unavailable, falling back to Playwright: %s: %s",
                type(exc).__name__,
                exc,
            )
            from ticket_bot.browser.playwright_engine import PlaywrightEngine

            return PlaywrightEngine()
        return NodriverEngine()

    if name == "playwright":
        from ticket_bot.browser.playwright_engine import PlaywrightEngine

        return PlaywrightEngine()

    raise ValueError(
        f"Unsupported browser engine: {engine_name!r}. "
        "Expected one of: nodriver, playwright"
    )
