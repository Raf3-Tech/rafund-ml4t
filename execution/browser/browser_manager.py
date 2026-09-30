"""Playwright browser lifecycle for the Kraken terminal observer.

kraken_terminal_session() is the ONLY place in this codebase allowed to
launch a browser. It enforces execution.compliance_gate.require_
observation_authorized() before doing anything else — every caller
(currently none; this is Phase 4 scaffolding, not wired into any CLI
command yet) inherits that guarantee for free rather than having to
remember to check it themselves.

playwright is imported lazily (inside the function, not at module load
time) so execution/browser/ stays importable — and unit-testable via
mocks — without playwright installed. See requirements.txt's Phase 4
section for the actual install step, which this module does not perform
itself.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from execution.compliance_gate import require_observation_authorized

if TYPE_CHECKING:
    from playwright.sync_api import Page

KRAKEN_TERMINAL_URL = "https://pro.kraken.com/"


@contextmanager
def kraken_terminal_session(*, headless: bool = True) -> Iterator["Page"]:
    """Yields a Playwright Page already at the Kraken terminal URL — NOT
    yet logged in, NOT yet on any specific account. Login/account
    selection is deliberately left to the caller (a human via
    `headless=False`, or a later phase's own explicit, reviewed step) —
    this function's only job is compliance-gated browser lifecycle, not
    authentication.

    Session persistence (KRAKEN_BROWSER_STORAGE_STATE_PATH, a Playwright
    storage_state file) is read from an env var, never a path committed to
    this repo — AGENT_ORCHESTRATION.md Section 29: no credentials, session
    cookies, or MFA secrets in Git."""
    require_observation_authorized()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed. See requirements.txt's Phase 4 section: "
            "`pip install playwright && playwright install chromium`."
        ) from exc

    storage_state = os.environ.get("KRAKEN_BROWSER_STORAGE_STATE_PATH") or None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        try:
            page.goto(KRAKEN_TERMINAL_URL)
            yield page
        finally:
            context.close()
            browser.close()
