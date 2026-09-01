"""Tests for the Kraken API research helper module."""

import json
from unittest.mock import MagicMock, patch

from kraken_api_research import KrakenAPIInspector


def test_kraken_api_inspector_builds_public_url():
    inspector = KrakenAPIInspector()
    url = inspector._build_url("/0/public/Time", {"foo": "bar"})
    assert url.startswith("https://api.kraken.com/0/public/Time")
    assert "foo=bar" in url


def test_kraken_api_inspector_inspect_handles_public_failure(monkeypatch):
    response = MagicMock()
    response.read.return_value = json.dumps({"error": ["EGeneral:Invalid key"], "result": {}}).encode("utf-8")
    response.__enter__.return_value = response
    with patch("urllib.request.urlopen", return_value=response):
        inspector = KrakenAPIInspector()
        info = inspector.inspect(symbol_limit=1)
        assert "api_keys_present" in info
        assert "public_pairs" in info
        assert isinstance(info["public_pairs"], list)
