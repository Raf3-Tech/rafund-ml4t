"""Agent Orchestration — execution layer (AGENT_ORCHESTRATION.md Section 26).

Deliberately a top-level package, sibling to senior_trader/, not nested
inside it: senior_trader/'s own structural test
(tests/test_senior_trader_boundaries.py) asserts that package never imports
Playwright or anything execution-capable, and that guarantee stays true
regardless of what gets built here. Nothing in senior_trader/ imports from
execution/ as of Phase 4 — a later phase's supervised/autonomous execution
work is what would eventually connect the two, deliberately, through a
narrow, reviewed interface, not by senior_trader/ reaching in directly.
"""
