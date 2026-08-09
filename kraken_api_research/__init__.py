"""Kraken API research helpers for bridge-style inspection.

This module provides a lightweight, broker-agnostic way to inspect Kraken's
public API capabilities, server status, and symbol metadata without requiring
full order routing logic.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional


class KrakenAPIInspector:
    BASE_URL = "https://api.kraken.com"

    def __init__(self) -> None:
        self.api_key = os.environ.get("KRAKEN_API_KEY", "").strip()
        self.api_secret = os.environ.get("KRAKEN_API_SECRET", "").strip()

    def inspect(self, symbol_limit: int = 10) -> Dict[str, Any]:
        return {
            "api_keys_present": bool(self.api_key and self.api_secret),
            "server_time": self._fetch_public("/0/public/Time", {}).get("unixtime"),
            "system_status": self._fetch_public("/0/public/SystemStatus", {}).get("status"),
            "public_pairs": self._load_asset_pairs(symbol_limit),
            "notes": "Private order-capability checks require authenticated Kraken keys and are not probed by this endpoint.",
        }

    def _fetch_public(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        url = self._build_url(path, params)
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                payload = response.read().decode("utf-8")
                data = json.loads(payload)
                if data.get("error"):
                    return {"error": data.get("error")}
                return data.get("result", {}) or {}
        except Exception as exc:
            return {"error": str(exc)}

    def _load_asset_pairs(self, symbol_limit: int) -> List[Dict[str, Any]]:
        result = self._fetch_public("/0/public/AssetPairs", {})
        if not isinstance(result, dict):
            return [{"error": "unexpected asset pairs response"}]

        if result.get("error"):
            return [{"error": result.get("error")}]

        pairs: List[Dict[str, Any]] = []
        for pair_name, pair_meta in list(result.items())[:symbol_limit]:
            if not isinstance(pair_meta, dict):
                pairs.append({
                    "pair": pair_name,
                    "error": "unexpected pair metadata",
                })
                continue

            pairs.append({
                "pair": pair_name,
                "base": pair_meta.get("base"),
                "quote": pair_meta.get("quote"),
                "lot": pair_meta.get("lot"),
                "leverage": pair_meta.get("leverage"),
            })
        return pairs

    def _build_url(self, path: str, params: Dict[str, str]) -> str:
        query = "".join(f"{k}={urllib.request.quote(str(v))}&" for k, v in params.items())
        if query.endswith("&"):
            query = query[:-1]
        return f"{self.BASE_URL}{path}{'?' + query if query else ''}"
