"""High-level, read-only observer for the Kraken Pro Android app —
combines adb_client's device I/O with parser's XML parsing, and applies
the safety checks docs/mobile_nav/kraken_app/NAV_TREE.md's own capture
notes call out as real, previously-observed footguns before trusting a
dump: wrong/stale window focus, a live phone-call overlay, and (confirmed
empirically, not just theorized — 2026-09-30) a uiautomator dump that
silently returns the previous screen's content right after a state
change. Uses adb_client.dump_ui_stable(), never the single-shot dump_ui(),
for exactly that reason.
"""
from __future__ import annotations

from typing import Optional

from execution.mobile import adb_client
from execution.mobile.models import ObservedPropAccountScreen
from execution.mobile.parser import parse_prop_account_screen


class StaleOrUnexpectedScreenError(RuntimeError):
    """Raised when the current device focus isn't the Kraken app, or a
    call appears to be active — the dump would not reflect the Prop
    screen, per NAV_TREE.md's documented observations."""


def observe_prop_account_screen(serial: Optional[str] = None, *, verify_focus: bool = True) -> ObservedPropAccountScreen:
    """Dumps and parses whatever screen is CURRENTLY showing on the
    device — this function never navigates or taps anything, so it is the
    caller's responsibility to already be on the Prop tab (a human, or a
    later phase's own explicit, reviewed navigation step).

    verify_focus=True (default) checks current_focus() mentions the Kraken
    package and call_state() reads idle before trusting the dump, exactly
    as NAV_TREE.md's "Notes" section recommends — set False only to
    inspect a dump regardless (e.g. for debugging this parser itself)."""
    if verify_focus:
        focus = adb_client.current_focus(serial)
        if adb_client.KRAKEN_APP_PACKAGE not in focus:
            raise StaleOrUnexpectedScreenError(
                f"Current window focus does not mention {adb_client.KRAKEN_APP_PACKAGE!r}: {focus!r}"
            )
        call = adb_client.call_state(serial)
        if call and "mCallState=0" not in call and "mCallState = 0" not in call:
            raise StaleOrUnexpectedScreenError(f"Device does not report an idle call state: {call!r}")

    xml_text = adb_client.dump_ui_stable(serial)
    return parse_prop_account_screen(xml_text)
