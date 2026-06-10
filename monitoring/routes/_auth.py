"""Shared bearer-token auth decorator for dashboard Blueprint routes."""

from __future__ import annotations

import functools
import hmac

from flask import current_app, jsonify, request


def require_token(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        token = current_app.config.get("API_TOKEN")
        if token:
            provided = request.headers.get("Authorization", "")
            if not hmac.compare_digest(provided, f"Bearer {token}"):
                return jsonify({"error": "unauthorized"}), 401
        return view(*args, **kwargs)
    return wrapped
