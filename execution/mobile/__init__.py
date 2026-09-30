"""Mobile (ADB/uiautomator) observation layer — an alternative to
execution/browser/ for the same read-only observation goal
(AGENT_ORCHESTRATION.md Section 52), targeting the actual Kraken Pro
Android app (`com.kraken.trade`) rather than a web terminal.

Unlike execution/browser/'s locators (unverified placeholders — nobody in
that phase had real terminal access), this package's parsing logic is
derived from a REAL accessibility-tree dump of the real app, captured via
ADB from a physical device (see docs/mobile_nav/kraken_app/NAV_TREE.md,
gitignored — account-specific, not part of this repo). tests/ use a
synthetic fixture with fabricated numbers, structurally identical to that
real dump, not the real captured file itself.

Read-only by construction: execution/mobile/adb_client.py exposes dump/
pull/screencap/diagnostic shell commands only — no `input tap`, `input
text`, or `input swipe` exists anywhere in this package. See
tests/test_execution_mobile_boundaries.py.
"""
