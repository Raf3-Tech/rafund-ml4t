"""Agent Orchestration — Senior Trader, Phase 1 (Trader Foundation).

Sits above signals/pending_signal_detector.py and risk/pretrade_gate.py as a
read-only consumer, never a peer: it never recomputes a gate decision, never
sizes a position, and never places an order. See ../AGENT_ORCHESTRATION.md
for the full specification and ../state/integration_plan.md for what this
phase actually implements (and what it deliberately doesn't).
"""
