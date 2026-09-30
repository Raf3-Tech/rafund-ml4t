"""Simulated execution — AGENT_ORCHESTRATION.md Section 51 ("Paper Trader:
run the complete employee workflow without real execution").

Deliberately simple: an execution intent either fills at its own limit
price, or it has expired — there is no order-book/fill-probability model
here. This mirrors the one real safety property Section 16 ("Limit-Order
Discipline") actually requires: a fill never happens at a worse price than
the intent specified, and an unfilled setup times out rather than getting
chased. Modeling the *probability* that a real limit order would have
filled needs order-book depth this codebase has never had access to
(state/repository_map.md's Known Limitations) — that belongs to a later
phase's real execution path (Phase 5+), not this simulation. Nothing here
talks to a browser or an exchange API.
"""
from __future__ import annotations

from datetime import datetime

from senior_trader.models import EXPIRED, FILLED, ExecutionIntent, SimulatedFill


def simulate_execution(intent: ExecutionIntent, now: datetime) -> SimulatedFill:
    if now > intent.expires_at:
        return SimulatedFill(
            intent_id=intent.intent_id, status=EXPIRED, fill_price=None, filled_at=None,
            reason="intent expired before simulated execution ran",
        )
    return SimulatedFill(
        intent_id=intent.intent_id, status=FILLED, fill_price=intent.price, filled_at=now,
        reason="simulated fill at the intent's own limit price (Phase 3: no fill-probability model)",
    )
