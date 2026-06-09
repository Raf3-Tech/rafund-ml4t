"""Strategy registry — single source of truth for all strategies.

Agents and tooling query this registry to discover available strategies,
their metadata, and param grids without parsing main.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Type

from strategies.base import BaseStrategy


@dataclass
class StrategyEntry:
    cls: Type[BaseStrategy]
    name: str
    description: str
    tier_hints: List[str]
    tags: List[str]


class StrategyRegistry:
    """Registry of all known strategies, populated via @StrategyRegistry.register."""

    _entries: Dict[str, StrategyEntry] = {}

    @classmethod
    def register(
        cls,
        *,
        description: str = "",
        tier_hints: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> Callable:
        """Class decorator that registers a BaseStrategy subclass."""
        def decorator(strategy_cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
            cls._entries[strategy_cls.name] = StrategyEntry(
                cls=strategy_cls,
                name=strategy_cls.name,
                description=description,
                tier_hints=tier_hints or [],
                tags=tags or [],
            )
            return strategy_cls
        return decorator

    @classmethod
    def get(cls, name: str) -> Optional[StrategyEntry]:
        return cls._entries.get(name)

    @classmethod
    def list(cls) -> List[StrategyEntry]:
        return list(cls._entries.values())

    @classmethod
    def names(cls) -> List[str]:
        return list(cls._entries.keys())

    @classmethod
    def instantiate_all(cls) -> List[BaseStrategy]:
        """Return one fresh instance of every registered strategy."""
        return [entry.cls() for entry in cls._entries.values()]

    @classmethod
    def instantiate(cls, name: str) -> BaseStrategy:
        entry = cls._entries.get(name)
        if entry is None:
            raise KeyError(f"Strategy '{name}' not registered. Known: {cls.names()}")
        return entry.cls()

    @classmethod
    def as_dict(cls) -> List[Dict]:
        """Serialisable summary safe for agent consumption."""
        return [
            {
                "name": e.name,
                "description": e.description,
                "tier_hints": e.tier_hints,
                "tags": e.tags,
                "param_grid": e.cls.param_grid,
                "min_bars": e.cls.min_bars,
            }
            for e in cls._entries.values()
        ]
