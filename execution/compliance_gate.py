"""Compliance gate — AGENT_ORCHESTRATION.md Section 11.

Automating any interaction with a live Kraken account — even read-only
observation — must not be assumed permitted merely because it is
technically possible. Kraken's Terms of Service, the Prop Evaluation
Terms, and the Funded Trader Agreement have not been reviewed by anyone in
this session; nothing here claims they do or don't permit this workflow.

Two independent, narrower-than-broader flags, both env-var-driven (never
edited to `True` in source — see live_execution_authorized()'s docstring
for why):

  KRAKEN_BROWSER_OBSERVATION_AUTHORIZED=1
      Required before execution/browser/browser_manager.py will launch a
      browser at all (Phase 4 — read-only observation).

  KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED=1
      Required before anything is allowed to submit an order (Phase 5+ —
      does not exist yet). Requires observation authorization too; the
      reverse is not true.

Neither flag is set by anything in this repository, in any test, or in any
CI-equivalent config — flipping either is exclusively a human action taken
outside this codebase (an actual shell environment variable, or a secrets
manager entry for a deployed instance), after doing the verification
Section 11 describes.
"""
from __future__ import annotations

import os

# Literal constant per AGENT_ORCHESTRATION.md Section 11's exact wording.
# Never change this to True here — see live_execution_authorized().
LIVE_BROWSER_EXECUTION_AUTHORIZED = False


class ComplianceGateError(RuntimeError):
    """Raised when code attempts a browser action without the matching
    environment-variable authorization. Never caught-and-ignored anywhere
    in this codebase — a caller seeing this should stop, not retry."""


def observation_authorized() -> bool:
    """Read-only browser observation (Phase 4). Lower risk than order
    submission, but still requires explicit opt-in: connecting Playwright
    to a real, logged-in Kraken session has account-security and
    platform-terms implications of its own (AGENT_ORCHESTRATION.md Section
    29), not just whatever risk order placement specifically carries."""
    return os.environ.get("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", "").strip() == "1"


def live_execution_authorized() -> bool:
    """True only via the environment variable — never by editing
    LIVE_BROWSER_EXECUTION_AUTHORIZED above. Keeping the authorization
    signal out of source means `git log` never shows a commit that flips
    live trading on; the on/off switch lives in whoever's shell or
    deployment config set the env var, where it belongs. Also requires
    observation_authorized() — execution without ever having verified
    observation works is not a state this gate allows."""
    if not observation_authorized():
        return False
    return os.environ.get("KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED", "").strip() == "1"


def require_observation_authorized() -> None:
    if not observation_authorized():
        raise ComplianceGateError(
            "Browser observation is not authorized. Set "
            "KRAKEN_BROWSER_OBSERVATION_AUTHORIZED=1 only after verifying Kraken's "
            "Terms of Service, Prop Evaluation Terms, and Funded Trader Agreement "
            "permit this workflow, or after obtaining written authorization "
            "(AGENT_ORCHESTRATION.md Section 11)."
        )


def require_live_execution_authorized() -> None:
    if not live_execution_authorized():
        raise ComplianceGateError(
            "Live browser order execution is not authorized "
            "(KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED != '1', or observation itself "
            "is not authorized). This capability does not exist yet in this codebase "
            "regardless — see AGENT_ORCHESTRATION.md Section 44/54 for the phases "
            "still required before it would."
        )
