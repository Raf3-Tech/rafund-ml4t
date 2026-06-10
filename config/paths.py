"""Canonical filesystem paths for the project.

Import from here instead of recomputing Path(__file__).parent chains in
multiple modules.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROP_CONFIG_PATH = PROJECT_ROOT / "config" / "prop_challenge.json"
DECISIONS_LOG_PATH = PROJECT_ROOT / "tmp" / "research_decisions.jsonl"
