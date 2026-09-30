"""Parses a raw `uiautomator dump` XML string from the Kraken Pro Android
app's Prop tab into execution/mobile/models.py's dataclasses.

Derived from a REAL accessibility-tree capture (`com.kraken.trade`
v5.70.1, see docs/mobile_nav/kraken_app/NAV_TREE.md — gitignored, not
committed) rather than guessed selectors, unlike execution/browser/
locators.py. Two real structural facts this parser depends on, both
confirmed in that capture:

1. The app assigns almost no `resource-id` values (a couple of literal
   "undefined-positions"/"undefined-orders" ids are the exception) — nodes
   are identified by their `content-desc` (for Buttons/Views) or `text`
   (for TextViews), not stable ids. This is normal for a lot of React
   Native/Flutter-style apps and means this parser is inherently more
   fragile to app updates than a resource-id-based one would be; there was
   no more stable handle available to use.

2. A stat's on-screen VALUE is a TextView that is a *sibling*, not a
   child, of the label node (e.g. "Total equity"'s value "5,000.00 USD" is
   not nested inside the "Total equity" Button/TextView at all) — so this
   parser flattens every TextView's `text` in document order and pairs
   each known label with the fixed number of value-TextViews that follow
   it, rather than walking parent/child relationships.

Only the empty-positions/empty-orders state has ever actually been
observed (the captured account had no open positions or orders) — a
populated positions/orders table's row structure is UNKNOWN and NOT parsed
here. ObservedPositionsPanel.positions/.orders will stay empty even when
the real app shows real rows until someone captures that state and this
parser is extended to match it. This is a documented gap, not a silent one.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from execution.mobile.models import ObservedFavorite, ObservedPositionsPanel, ObservedPropAccountScreen

# Values-per-label, in on-screen order, exactly as captured — see module
# docstring point 2. "Total equity" has two trailing values (the amount,
# then the "+0.00 USD (+0.00%)" change line); the rest have exactly one.
_STAT_LABEL_VALUE_COUNTS = {
    "Total equity": 2,
    "Target equity": 1,
    "Daily loss limit": 1,
    "Drawdown limit": 1,
}

_FAVORITE_DESC_RE = re.compile(
    r"^(?P<symbol>[A-Za-z0-9]+),\s*(?P<leverage>\d+x),\s*(?P<price>[\d,.]+),\s*"
    r"(?P<name>.+?),\s*(?P<pct>-?[\d.]+)\s*%$"
)

_EMPTY_STATE_MARKERS = ("No position entries available", "No open orders")


def _norm(text: Optional[str]) -> str:
    """uiautomator dumps use \xa0 (non-breaking space) before units/%
    signs — normalize to a plain space so regexes and Decimal parsing
    don't have to special-case it."""
    return (text or "").replace("\xa0", " ").strip()


def _parse_money(text: Optional[str]) -> Optional[Decimal]:
    cleaned = _norm(text).replace(",", "").replace("USD", "").replace("$", "").strip()
    if cleaned in ("", "--", "-"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _all_nodes(root: ET.Element) -> List[ET.Element]:
    return list(root.iter())


def _text_view_texts(root: ET.Element) -> List[str]:
    """Every TextView's `text`, in document (pre-order) order — the same
    order the app renders them top-to-bottom on this screen."""
    return [
        _norm(node.get("text"))
        for node in _all_nodes(root)
        if node.get("class", "").endswith("TextView") and _norm(node.get("text"))
    ]


def _pair_account_stats(texts: List[str]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    i, n = 0, len(texts)
    while i < n:
        label = texts[i]
        count = _STAT_LABEL_VALUE_COUNTS.get(label)
        if count is not None:
            result[label] = texts[i + 1 : i + 1 + count]
            i += 1 + count
        else:
            i += 1
    return result


def _account_label(root: ET.Element) -> str:
    """The top-of-screen account selector button ("Prop • Advanced Eval
    1") is the only Button-class content-desc containing the bullet
    separator "•" in the captured screen — a specific, real signal, not a
    guess."""
    for node in _all_nodes(root):
        desc = _norm(node.get("content-desc"))
        if node.get("class", "").endswith("Button") and "•" in desc:
            return desc
    return ""


def _parse_favorites(root: ET.Element) -> List[ObservedFavorite]:
    favorites = []
    for node in _all_nodes(root):
        if not node.get("class", "").endswith("Button"):
            continue
        match = _FAVORITE_DESC_RE.match(_norm(node.get("content-desc")))
        if not match:
            continue
        favorites.append(ObservedFavorite(
            symbol=match.group("symbol"),
            leverage=match.group("leverage"),
            price=_parse_money(match.group("price")),
            name=match.group("name"),
            change_pct=_parse_money(match.group("pct")),
        ))
    return favorites


def _parse_positions_panel(texts: List[str]) -> ObservedPositionsPanel:
    active_tab = None
    if "Positions" in texts:
        active_tab = "Positions"
    elif "Orders" in texts:
        active_tab = "Orders"

    empty_message = next((t for t in texts if t in _EMPTY_STATE_MARKERS), None)
    return ObservedPositionsPanel(active_tab=active_tab, empty_message=empty_message)


def parse_prop_account_screen(xml_text: str) -> ObservedPropAccountScreen:
    root = ET.fromstring(xml_text)
    texts = _text_view_texts(root)
    stats = _pair_account_stats(texts)

    total_equity_values = stats.get("Total equity", [])
    total_equity = _parse_money(total_equity_values[0]) if total_equity_values else None
    total_equity_change = total_equity_values[1] if len(total_equity_values) > 1 else None

    target_equity_values = stats.get("Target equity", [])
    daily_loss_values = stats.get("Daily loss limit", [])
    drawdown_values = stats.get("Drawdown limit", [])

    return ObservedPropAccountScreen(
        account_label=_account_label(root),
        total_equity=total_equity,
        total_equity_change=total_equity_change,
        target_equity=_parse_money(target_equity_values[0]) if target_equity_values else None,
        daily_loss_limit=_parse_money(daily_loss_values[0]) if daily_loss_values else None,
        drawdown_limit=_parse_money(drawdown_values[0]) if drawdown_values else None,
        favorites=_parse_favorites(root),
        positions_panel=_parse_positions_panel(texts),
        observed_at=datetime.now(timezone.utc),
    )
