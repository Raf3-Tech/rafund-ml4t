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
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional

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
    NAV_TREE.md's own notes: this can silently return a stale or
    wrong-app dump — callers should cross-check current_focus() (and
    ideally expected on-screen text) before trusting the result for
    anything that matters."""
    require_mobile_observation_authorized()
    _run([*_device_args(serial), "shell", "uiautomator", "dump", DEVICE_UI_DUMP_PATH])
    return _run([*_device_args(serial), "shell", "cat", DEVICE_UI_DUMP_PATH])


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
