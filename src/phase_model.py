"""src/phase_model.py — Registry-driven Phase handle.

Replaces the legacy enum-based Phase model with a registry-backed class.
Phase order for each pipeline mode is read from config.py so the registry
remains the single source of truth for available phases.
"""

from __future__ import annotations

from typing import List, Optional

try:
    from registry import REGISTRY
except ImportError:
    from src.registry import REGISTRY


def get_phase_order(mode: str = "greenfield") -> List[str]:
    """Return ordered phase names for a pipeline mode from config/registry."""
    try:
        from config import get_config
    except ImportError:
        from src.config import get_config

    cfg = get_config()
    if mode == "greenfield":
        return [p for p in cfg.greenfield_phase_order if p in REGISTRY.phases]
    if mode == "brownfield":
        return [p for p in cfg.brownfield_phase_order if p in REGISTRY.phases]
    raise ValueError(f"Unknown pipeline mode: {mode!r}")


def phase_names() -> List[str]:
    """Return the current ordered list of greenfield phase names."""
    return get_phase_order("greenfield")


class Phase:
    """Registry-driven phase handle. Immutable and comparable by name."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        n = name.lower()
        if n not in REGISTRY.phases:
            raise ValueError(f"Unknown phase: {name!r}")
        self.name = n

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"Phase({self.name!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Phase) and self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    # Convenience methods for enum-era compatibility
    def is_init(self) -> bool:
        return self.name == "init"

    def is_start(self, mode: str = "greenfield") -> bool:
        order = get_phase_order(mode)
        return bool(order) and self.name == order[0]

    def is_terminal(self, mode: str = "greenfield") -> bool:
        order = get_phase_order(mode)
        return bool(order) and self.name == order[-1]

    @classmethod
    def list_all(cls) -> List["Phase"]:
        return [cls(n) for n in REGISTRY.list_phases()]

    @classmethod
    def from_name(cls, name: str) -> "Phase":
        return cls(name)

    def next(self, pipeline_mode: str = "greenfield") -> Optional["Phase"]:
        order = get_phase_order(pipeline_mode)
        if self.name not in order:
            return None
        idx = order.index(self.name)
        return Phase(order[idx + 1]) if idx + 1 < len(order) else None

    def prev(self, pipeline_mode: str = "greenfield") -> Optional["Phase"]:
        order = get_phase_order(pipeline_mode)
        if self.name not in order:
            return None
        idx = order.index(self.name)
        return Phase(order[idx - 1]) if idx > 0 else None
