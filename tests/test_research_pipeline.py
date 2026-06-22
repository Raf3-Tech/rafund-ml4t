"""Tests for research.pipeline — acceptance gate and decision logging.

All tests are offline: no database, no engine run.
"""

from __future__ import annotations

import json

from research.pipeline import _gate, print_research_summary


# ---------------------------------------------------------------------------
# _gate — acceptance logic
# ---------------------------------------------------------------------------

def _row(**kwargs):
    defaults = {
        "strategy_name": "Test Strategy",
        "symbol": "BTC/USDT",
        "avg_sharpe": 0.0,
        "avg_max_dd_pct": 5.0,
        "avg_win_rate_pct": 50.0,
        "pass_ratio": 0.0,
        "qualifies": False,
        "n_windows": 10,
        "total_num_trades": 150,
        "params": {"fast": 20},
    }
    return {**defaults, **kwargs}


def test_gate_accepts_when_qualifying_and_sharpe_above_floor():
    d = _gate(_row(pass_ratio=0.70, qualifies=True, avg_sharpe=0.5))
    assert d.accepted is True


def test_gate_rejects_when_sharpe_below_floor():
    d = _gate(_row(pass_ratio=0.80, qualifies=True, avg_sharpe=0.1))
    assert d.accepted is False


def test_gate_rejects_when_not_qualifying():
    d = _gate(_row(pass_ratio=0.30, qualifies=False, avg_sharpe=1.0))
    assert d.accepted is False


def test_gate_rejects_when_trades_below_floor():
    d = _gate(_row(pass_ratio=0.80, qualifies=True, avg_sharpe=0.8, total_num_trades=10))
    assert d.accepted is False
    assert "10" in d.reason


def test_gate_decision_fields_populated():
    d = _gate(_row(pass_ratio=0.70, qualifies=True, avg_sharpe=0.8, n_windows=15, symbol="ETH/USDT"))
    assert d.strategy_name == "Test Strategy"
    assert d.symbol == "ETH/USDT"
    assert d.n_windows == 15
    assert d.total_num_trades == 150
    assert isinstance(d.timestamp, str)
    assert isinstance(d.reason, str) and len(d.reason) > 0


# ---------------------------------------------------------------------------
# print_research_summary — smoke test (no crash)
# ---------------------------------------------------------------------------

def test_print_summary_empty(capsys):
    print_research_summary([])
    out = capsys.readouterr().out
    assert "No candidates" in out


def test_print_summary_mixed(capsys):
    decisions = [
        _gate(_row(pass_ratio=0.70, qualifies=True, avg_sharpe=0.6, strategy_name="Good")),
        _gate(_row(pass_ratio=0.10, qualifies=False, avg_sharpe=0.1, strategy_name="Bad")),
    ]
    print_research_summary(decisions)
    out = capsys.readouterr().out
    assert "ACCEPTED" in out
    assert "REJECTED" in out
    assert "Good" in out
    assert "Bad" in out


# ---------------------------------------------------------------------------
# _log_decision — writes valid JSONL
# ---------------------------------------------------------------------------

def test_log_decision_writes_jsonl(tmp_path, monkeypatch):
    import research.pipeline as rp
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(rp, "_DECISIONS_LOG", log_path)

    from research.pipeline import _log_decision
    decision = _gate(_row(pass_ratio=0.70, qualifies=True, avg_sharpe=0.6))
    _log_decision(decision)

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["accepted"] is True
    assert "timestamp" in data


def test_log_decision_appends(tmp_path, monkeypatch):
    import research.pipeline as rp
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(rp, "_DECISIONS_LOG", log_path)

    from research.pipeline import _log_decision
    _log_decision(_gate(_row(strategy_name="A", pass_ratio=0.70, qualifies=True, avg_sharpe=0.5)))
    _log_decision(_gate(_row(strategy_name="B", pass_ratio=0.70, qualifies=True, avg_sharpe=0.5)))

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["strategy_name"] == "A"
    assert json.loads(lines[1])["strategy_name"] == "B"
