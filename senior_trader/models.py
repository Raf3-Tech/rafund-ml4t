"""Data models for Senior Trader: decisions, escalations, execution intents,
simulated fills, reconciliation results, audit findings, session metrics,
and journal entries.

Plain frozen dataclasses, matching the rest of this codebase's convention
(risk/pretrade_gate.py's GateDecision, signals/pending_signal_detector.py's
PendingSignal) rather than introducing an ORM or a second modeling style.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

TAKE = "TAKE"
NO_TRADE = "NO_TRADE"

# Standardized no-trade reasons (AGENT_ORCHESTRATION.md Section 25). Only the
# ones Phase 1's review_session.py can actually produce without inventing new
# risk math are defined here — the full enumerated list in the spec is
# aspirational until a later phase adds the checks that would justify it.
NO_TRADE_STALE_SIGNAL = "NO_TRADE_STALE_SIGNAL"
NO_TRADE_NO_ACTIVE_SIGNALS = "NO_TRADE_NO_ACTIVE_SIGNALS"


@dataclass(frozen=True)
class DecisionRecord:
    """One row of the `decisions` table — including a NO_TRADE, per
    AGENT_ORCHESTRATION.md Section 23 ("every decision must create a
    decision record even when the decision is NO_TRADE"), so the employee's
    own decision history can't survivorship-bias itself toward only ever
    having recorded the trades it took."""
    decision_id: str
    pending_signal_id: Optional[int]
    gate_decision_id: Optional[int]
    decision: str  # TAKE | NO_TRADE
    no_trade_reason: Optional[str]
    evidence: Dict[str, Any]
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class EscalationEvent:
    """One row of the `incidents` table (AGENT_ORCHESTRATION.md Section 20).
    `category` is one of the constants in senior_trader/escalation.py."""
    event_id: str
    category: str
    severity: str
    current_state: Dict[str, Any]
    observed_state: Dict[str, Any]
    expected_state: Dict[str, Any]
    evidence: Dict[str, Any]
    recommended_action: str
    execution_status: str = "none"
    created_at: Optional[datetime] = None


FILLED = "filled"
EXPIRED = "expired"

RECONCILED = "RECONCILED"
RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"


@dataclass(frozen=True)
class ExecutionIntent:
    """AGENT_ORCHESTRATION.md Section 13/14 — the immutable bridge between a
    TAKE decision and anything that could eventually submit an order. Phase
    3 builds and simulates these in-process (senior_trader/execution_intent.py,
    senior_trader/simulation.py); nothing here talks to a browser or an
    exchange API."""
    intent_id: str
    decision_id: str
    pending_signal_id: Optional[int]
    account: str
    symbol: str
    side: str  # BUY | SELL
    order_type: str  # always "limit" — AGENT_ORCHESTRATION.md Section 16
    price: Decimal
    quantity: Decimal
    reduce_only: bool
    gate_decision: str  # APPROVE | REDUCE | UNKNOWN
    gate_snapshot: Dict[str, Any]
    setup_tag: str
    strategy_id: str
    expires_at: datetime
    execution_mode: str  # RESEARCH | PAPER | DRY_RUN — see senior_trader/config.py


@dataclass(frozen=True)
class SimulatedFill:
    """Phase 3's stand-in for a real fill result — see
    senior_trader/simulation.py's module docstring for why this is
    deliberately not a fill-probability model."""
    intent_id: str
    status: str  # FILLED | EXPIRED
    fill_price: Optional[Decimal]
    filled_at: Optional[datetime]
    reason: str


@dataclass(frozen=True)
class ReconciliationResult:
    """AGENT_ORCHESTRATION.md Section 38 — compares an ExecutionIntent
    against a fill result (Phase 3: SimulatedFill; a later phase: a
    browser-observed fill, through the same function unchanged)."""
    status: str  # RECONCILED | RECONCILIATION_FAILURE
    evidence: Dict[str, Any]


@dataclass
class AuditFinding:
    """One check from senior_trader/auditor.py's daily audit."""
    check: str
    passed: bool
    evidence: Dict[str, Any]


@dataclass
class AuditReport:
    findings: List[AuditFinding]

    @property
    def all_passed(self) -> bool:
        return all(f.passed for f in self.findings)


@dataclass(frozen=True)
class SessionMetrics:
    """AGENT_ORCHESTRATION.md Section 51's "Measure" list, scoped to what
    Phase 3's schema can honestly answer — see senior_trader/metrics.py's
    module docstring for why missed_opportunities/false_positives are None
    rather than a fabricated number."""
    decisions_total: int
    decisions_take: int
    decisions_no_trade: int
    stale_signal_no_trade: int
    intents_built: int
    fills_filled: int
    fills_expired: int
    reconciliation_failures: int
    incidents_total: int
    incidents_by_category: Dict[str, int]
    missed_opportunities: Optional[int]
    false_positives: Optional[int]


@dataclass(frozen=True)
class JournalEntry:
    """One row of the `trade_journal` table (AGENT_ORCHESTRATION.md Section
    21). Phase 1 only proves the schema round-trips — nothing yet populates
    execution_timestamp/fill_price/close_* fields, since nothing in this
    phase executes a trade. A later phase fills those in once a human
    reports back what they actually did with an approved setup."""
    trade_id: str
    decision_id: str
    pending_signal_id: Optional[int]
    setup_tag: str
    symbol: str
    direction: str
    signal_price: Decimal
    decision_timestamp: datetime
    execution_timestamp: Optional[datetime] = None
    fill_price: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    close_timestamp: Optional[datetime] = None
    close_price: Optional[Decimal] = None
    close_reason: Optional[str] = None
    realized_pnl: Optional[Decimal] = None
    notes: str = ""
