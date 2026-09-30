"""Tests for execution/mobile/adb_client.py. Mocks subprocess.run entirely
— no real `adb` invocation, no real device required."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from execution.mobile.adb_client import AdbCommandError, AdbDevice, dump_ui, dump_ui_stable, list_devices, screenshot


def _completed(stdout="", stderr="", returncode=0):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)


def test_list_devices_does_not_require_authorization(monkeypatch):
    """Listing devices reveals nothing account-specific — no gate check."""
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with patch("subprocess.run", return_value=_completed(
        stdout="List of devices attached\nR9TX700HKGM\tdevice usb:1-1 product:gts9lwifi\n",
    )):
        devices = list_devices()
    assert devices == [AdbDevice(serial="R9TX700HKGM", state="device")]


def test_list_devices_empty_when_none_attached():
    with patch("subprocess.run", return_value=_completed(stdout="List of devices attached\n\n")):
        assert list_devices() == []


def test_dump_ui_requires_mobile_observation_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        dump_ui()


def test_dump_ui_runs_dump_then_cat_when_authorized(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[-1] == "cat" or "cat" in args:
            return _completed(stdout="<hierarchy></hierarchy>")
        return _completed(stdout="")

    with patch("subprocess.run", side_effect=fake_run):
        result = dump_ui()

    assert result == "<hierarchy></hierarchy>"
    assert any("uiautomator" in c and "dump" in c for c in calls)
    assert any("cat" in c for c in calls)


def test_screenshot_requires_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):
        screenshot()


def test_screenshot_returns_raw_bytes(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    fake_png = b"\x89PNG\r\n\x1a\nfakepngbytes"
    with patch("subprocess.run", return_value=MagicMock(stdout=fake_png, stderr=b"", returncode=0)):
        result = screenshot()
    assert result == fake_png


def test_run_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=_completed(stderr="device not found", returncode=1)):
        with pytest.raises(AdbCommandError, match="device not found"):
            dump_ui()


def test_run_raises_on_missing_adb_binary(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(AdbCommandError, match="platform-tools"):
            dump_ui()


def test_run_raises_on_timeout(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="adb", timeout=15)):
        with pytest.raises(AdbCommandError, match="timed out"):
            dump_ui()


# ── dump_ui_stable ───────────────────────────────────────────────────────
# Added after confirming empirically (2026-09-30) that a single dump_ui()
# call can return stale content right after a real tap on the device —
# see adb_client.py's dump_ui docstring.

def test_dump_ui_stable_returns_immediately_when_first_two_dumps_agree(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("execution.mobile.adb_client.dump_ui", side_effect=["<a/>", "<a/>", "<a/>"]) as mock_dump, \
         patch("time.sleep"):
        result = dump_ui_stable()
    assert result == "<a/>"
    assert mock_dump.call_count == 2


def test_dump_ui_stable_retries_past_a_stale_first_read(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    # Simulates exactly what was observed live: a stale dump, then two
    # agreeing fresh ones.
    with patch("execution.mobile.adb_client.dump_ui", side_effect=["<stale/>", "<fresh/>", "<fresh/>"]), \
         patch("time.sleep"):
        result = dump_ui_stable()
    assert result == "<fresh/>"


def test_dump_ui_stable_raises_if_never_stabilizes(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("execution.mobile.adb_client.dump_ui", side_effect=["<a/>", "<b/>", "<c/>", "<d/>", "<e/>"]), \
         patch("time.sleep"):
        with pytest.raises(AdbCommandError, match="refusing to return a possibly-stale dump"):
            dump_ui_stable(max_attempts=5)


def test_dump_ui_stable_sleeps_between_attempts(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("execution.mobile.adb_client.dump_ui", side_effect=["<a/>", "<a/>"]), \
         patch("time.sleep") as mock_sleep:
        dump_ui_stable(delay_s=0.7)
    mock_sleep.assert_called_once_with(0.7)
