"""Thin subprocess wrapper around `adb` — read-only device commands only.

Every function here is diagnostic or observational: list devices, dump the
current screen's accessibility tree, capture a screenshot, or check the
two conditions docs/mobile_nav/kraken_app/NAV_TREE.md's own capture notes
flagged as real footguns (a stale/wrong-app dump, an active phone call
overlay). There is no `input tap`, `input text`, `input swipe`, or any
other command that changes device or app state anywhere in this module —
see tests/test_execution_mobile_boundaries.py, which greps the actual
source for `adb shell input` and fails the build if it ever appears.

Gated behind execution.compliance_gate.require_mobile_observation_authorized()
before any adb command runs, same pattern as
execution/browser/browser_manager.py's compliance check.

One narrow exception to "read-only": tap_bottom_nav() sends a single `adb
shell input tap` at one of the five bottom-navigation destinations —
switching tabs, never anything inside a trade/order flow. Coordinates come
from docs/mobile_nav/kraken_app/NAV_TREE.md's own prior-session capture
(portrait 1200x1920), not guessed, and the function only accepts one of
those five named tabs — never arbitrary coordinates. This exists because
the account holder explicitly authorized navigation (not just observation)
in-session; it is the ONLY tap/input capability anywhere in this package —
see tests/test_execution_mobile_boundaries.py, which asserts exactly that.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

from execution.compliance_gate import require_mobile_observation_authorized

ADB_TIMEOUT_S = 15
KRAKEN_APP_PACKAGE = "com.kraken.trade"
DEVICE_UI_DUMP_PATH = "/sdcard/window_dump.xml"


class AdbCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str  # "device" | "unauthorized" | "offline"


def _run(args: List[str]) -> str:
    """Runs one `adb ...` command and returns stdout. Raises
    AdbCommandError on a non-zero exit or missing `adb` binary — never
    swallows a failure silently."""
    try:
        result = subprocess.run(
            ["adb", *args], capture_output=True, text=True, timeout=ADB_TIMEOUT_S, check=False,
        )
    except FileNotFoundError as exc:
        raise AdbCommandError("adb is not on PATH — install Android platform-tools.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbCommandError(f"adb {' '.join(args)} timed out after {ADB_TIMEOUT_S}s") from exc

    if result.returncode != 0:
        raise AdbCommandError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def list_devices() -> List[AdbDevice]:
    """Does not require observation authorization — listing devices reveals
    nothing about a Kraken account and is how a caller decides which
    `serial` to pass everywhere else."""
    output = _run(["devices", "-l"])
    devices = []
    for line in output.splitlines()[1:]:  # skip the "List of devices attached" header
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append(AdbDevice(serial=parts[0], state=parts[1]))
    return devices


def _device_args(serial: Optional[str]) -> List[str]:
    return ["-s", serial] if serial else []


def current_focus(serial: Optional[str] = None) -> str:
    """`dumpsys window | grep mCurrentFocus` — NAV_TREE.md's own
    documented way to confirm which app/window is actually in front before
    trusting a UI dump (uiautomator can return a stale or wrong-app dump)."""
    require_mobile_observation_authorized()
    output = _run([*_device_args(serial), "shell", "dumpsys", "window"])
    for line in output.splitlines():
        if "mCurrentFocus" in line:
            return line.strip()
    return ""


def call_state(serial: Optional[str] = None) -> str:
    """`dumpsys telephony.registry | grep mCallState` — NAV_TREE.md flagged
    a live incoming-call overlay as a real observed footgun; callers should
    check this reads idle (state 0) before trusting a dump that might
    actually be showing a phone UI overlay instead of the Kraken app."""
    require_mobile_observation_authorized()
    output = _run([*_device_args(serial), "shell", "dumpsys", "telephony.registry"])
    for line in output.splitlines():
        if "mCallState" in line:
            return line.strip()
    return ""


def dump_ui(serial: Optional[str] = None) -> str:
    """`uiautomator dump` + pull, returning the raw XML as a string.
    NAV_TREE.md's own notes warned this can silently return a stale or
    wrong-app dump — CONFIRMED empirically (2026-09-30): immediately after
    tap_trade_action('Buy') opened Kraken's real order-entry form (verified
    by screenshot), the very next dump_ui() call returned content
    byte-identical to the pre-tap screen. A single dump_ui() call must
    NEVER be trusted alone for anything order-related — use
    dump_ui_stable() instead, which only returns once two consecutive
    dumps agree."""
    require_mobile_observation_authorized()
    _run([*_device_args(serial), "shell", "uiautomator", "dump", DEVICE_UI_DUMP_PATH])
    return _run([*_device_args(serial), "shell", "cat", DEVICE_UI_DUMP_PATH])


def dump_ui_stable(
    serial: Optional[str] = None,
    *,
    max_attempts: int = 5,
    delay_s: float = 0.4,
    expect_contains: Optional[Sequence[str]] = None,
) -> str:
    """dump_ui(), retried until two consecutive dumps are byte-identical.

    CONFIRMED INSUFFICIENT ON ITS OWN (2026-09-30): self-consistency alone
    caught a dump that visibly CHANGED between stale and fresh (the
    original bug this function fixed), but a later call demonstrated
    uiautomator can also return the SAME stale content on every call —
    two dumps agreeing with each other, both wrong, while a screenshot
    taken at the same moment showed a completely different real screen
    (Kraken's order-entry form, vs. a dump showing the plain Trade tab
    underneath it). Self-consistency cannot detect a consistently-wrong
    cache.

    expect_contains fixes this: pass substrings that MUST appear in the
    stabilized dump (e.g. "Quantity" or "Buy BTC" for an open order form).
    If the stable dump doesn't contain all of them, that attempt is
    treated as a miss and retried — a wrong-but-internally-consistent dump
    can no longer silently pass. Order-related callers MUST pass this;
    omitting it (None) preserves the old, weaker self-consistency-only
    behavior for non-critical callers (e.g. the Prop tab's mostly-static
    account stats, where NAV_TREE.md's original stale-dump report was
    "an unrelated app's data," not silently-wrong Kraken content).

    Raises AdbCommandError if no attempt both stabilizes and (when given)
    satisfies expect_contains within max_attempts."""
    previous: Optional[str] = None
    for _ in range(max_attempts):
        current = dump_ui(serial)
        if previous is not None and current == previous:
            if expect_contains is None or all(s in current for s in expect_contains):
                return current
        previous = current
        time.sleep(delay_s)
    reason = f"expected content {list(expect_contains)} never appeared in a stable dump" if expect_contains else \
        "no two consecutive dumps agreed"
    raise AdbCommandError(
        f"dump_ui_stable: {reason} within {max_attempts} attempts — "
        "refusing to return a possibly-stale dump for order-related use."
    )


# Portrait 1200x1920 tap coordinates for the five always-visible bottom-nav
# destinations — captured and validated in an earlier session, see
# docs/mobile_nav/kraken_app/NAV_TREE.md. Deliberately the only tap targets
# this module will ever accept.
_BOTTOM_NAV_TARGETS = {
    "Prop": (129, 1855),
    "Markets": (364, 1855),
    "Trade": (600, 1855),
    "Portfolio": (835, 1855),
    "Account": (1070, 1855),
}


def tap_bottom_nav(tab: str, serial: Optional[str] = None) -> None:
    """Taps one of the five bottom-navigation destinations. Navigation
    only — this never lands inside a trade/order flow, and raises before
    touching the device at all if `tab` isn't one of the five known,
    pre-validated names. This is the only function in execution/mobile/
    that changes device state rather than only reading it."""
    if tab not in _BOTTOM_NAV_TARGETS:
        raise ValueError(f"tap_bottom_nav only supports {sorted(_BOTTOM_NAV_TARGETS)}, got {tab!r}")
    require_mobile_observation_authorized()
    x, y = _BOTTOM_NAV_TARGETS[tab]
    _run([*_device_args(serial), "shell", "input", "tap", str(x), str(y)])


# Trade tab's Buy/Sell buttons — bounds captured LIVE in this session
# (2026-09-30) from a real uiautomator dump of the Trade tab (BTC/USD
# 10x): Buy [30,1747][519,1807] -> center (274,1777), Sell
# [531,1747][1020,1807] -> center (775,1777). Not guessed. Re-verify
# against a fresh dump if Kraken changes this layout — see
# tests/test_execution_mobile_trade_form.py for how to check bounds
# before trusting these again.
_TRADE_ACTION_TARGETS = {
    "Buy": (274, 1777),
    "Sell": (775, 1777),
}


def tap_trade_action(action: str, serial: Optional[str] = None) -> None:
    """Taps the Buy or Sell button on the Trade tab to OPEN the order
    entry form. This does not place an order — Kraken's own UI requires
    completing the form and its own separate confirmation step afterward
    (AGENT_ORCHESTRATION.md Section 14's two-phase submission; Phase 5's
    order-submission function, once it exists, is a distinct, more
    tightly gated capability from this one). Like tap_bottom_nav, this
    accepts only a named action, never raw coordinates."""
    if action not in _TRADE_ACTION_TARGETS:
        raise ValueError(f"tap_trade_action only supports {sorted(_TRADE_ACTION_TARGETS)}, got {action!r}")
    require_mobile_observation_authorized()
    x, y = _TRADE_ACTION_TARGETS[action]
    _run([*_device_args(serial), "shell", "input", "tap", str(x), str(y)])


# Trade tab's two unlabeled icon buttons, bottom-right of the order book
# panel, just above the bottom nav bar — bounds estimated visually from a
# real screenshot (2026-09-30), not from a uiautomator dump: these two
# nodes carry no text or content-desc in the accessibility tree at all,
# and the dump's own reported bounds for this region didn't reliably line
# up with screenshot pixels (see adb_client.py commit history — the same
# session that found the stale-dump bug also found this coordinate
# mismatch). Named by position, not function, because their function is
# not yet confirmed — see tap_trade_view_toggle's docstring.
_TRADE_VIEW_TOGGLE_TARGETS = {
    "left": (1067, 1939),
    "right": (1133, 1939),
}


def tap_trade_view_toggle(icon: str, serial: Optional[str] = None) -> None:
    """Taps one of the two unlabeled icon buttons bottom-right of the
    Trade tab's order book. Purpose unconfirmed as of writing — this
    function exists to observe what each one does, not because either is
    known to be safe/navigation-only the way tap_bottom_nav's targets
    are. Like the other tap functions, accepts only a named position,
    never raw coordinates."""
    if icon not in _TRADE_VIEW_TOGGLE_TARGETS:
        raise ValueError(f"tap_trade_view_toggle only supports {sorted(_TRADE_VIEW_TOGGLE_TARGETS)}, got {icon!r}")
    require_mobile_observation_authorized()
    x, y = _TRADE_VIEW_TOGGLE_TARGETS[icon]
    _run([*_device_args(serial), "shell", "input", "tap", str(x), str(y)])


# Order form's order-type dropdown row ("Market ⌄") — bounds estimated
# visually from a real screenshot (2026-09-30) of the open Buy form.
# Discovery-only: opens whatever selector Kraken shows (expected: Market/
# Limit options) so real coordinates for "Limit" can be captured before
# any function commits to tapping it.
_ORDER_TYPE_DROPDOWN_TARGET = (345, 218)


def tap_order_type_dropdown(serial: Optional[str] = None) -> None:
    """Taps the order form's order-type dropdown (shows "Market" by
    default) to open its option list. See tap_order_type_option() for the
    reviewed, real-coordinate function that selects from it."""
    require_mobile_observation_authorized()
    x, y = _ORDER_TYPE_DROPDOWN_TARGET
    _run([*_device_args(serial), "shell", "input", "tap", str(x), str(y)])


# Order-type dropdown's three options — bounds captured from a real
# screenshot (2026-09-30) of the open dropdown: Limit, Market (checked by
# default), Trigger, top to bottom, ~57px apart.
_ORDER_TYPE_OPTION_TARGETS = {
    "Limit": (155, 287),
    "Market": (155, 344),
    "Trigger": (155, 401),
}


def tap_order_type_option(option: str, serial: Optional[str] = None) -> None:
    """Selects one option from an ALREADY-OPEN order-type dropdown (call
    tap_order_type_dropdown() first). This system is limit-only by design
    (AGENT_ORCHESTRATION.md Section 16) — any order-form population
    function must call this with "Limit" explicitly; Kraken's own default
    is "Market", which nothing in this codebase should ever submit."""
    if option not in _ORDER_TYPE_OPTION_TARGETS:
        raise ValueError(f"tap_order_type_option only supports {sorted(_ORDER_TYPE_OPTION_TARGETS)}, got {option!r}")
    require_mobile_observation_authorized()
    x, y = _ORDER_TYPE_OPTION_TARGETS[option]
    _run([*_device_args(serial), "shell", "input", "tap", str(x), str(y)])


def screenshot(serial: Optional[str] = None) -> bytes:
    """`exec-out screencap -p` — raw PNG bytes, for a human to review
    alongside (or instead of) the parsed accessibility tree."""
    require_mobile_observation_authorized()
    try:
        result = subprocess.run(
            ["adb", *_device_args(serial), "exec-out", "screencap", "-p"],
            capture_output=True, timeout=ADB_TIMEOUT_S, check=False,
        )
    except FileNotFoundError as exc:
        raise AdbCommandError("adb is not on PATH — install Android platform-tools.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbCommandError(f"adb screencap timed out after {ADB_TIMEOUT_S}s") from exc
    if result.returncode != 0:
        raise AdbCommandError(f"adb screencap failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout
