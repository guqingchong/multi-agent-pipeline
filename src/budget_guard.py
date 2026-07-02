"""src/budget_guard.py — 3-Level Budget Enforcement Guard (Q05)

Implements:
- 3-level budget enforcement: warning (80%), soft_cap (100%), hard_cap (150%)
- Per-feature token limit tracking
- Per-project USD cost limit tracking
- Pre-flight budget checks before any agent call
- Integration with existing TokenBudget from task_decomposer

Design:
  BudgetGuard is the central gatekeeper. Before any agent operation, it checks:
    1. Per-feature token budget (via TokenBudget with feature-level limits)
    2. Per-project USD budget (cumulative cost across all features)
  At 80% → WARNING (log + notify, operation proceeds)
  At 100% → SOFT_CAP (pause new tasks, require user confirmation)
  At 150% → HARD_CAP (force-stop, reject all new operations)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

try:
    from task_decomposer import TokenBudget
except ImportError:
    from src.task_decomposer import TokenBudget

T = TypeVar("T")

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────
# Budget Level Enum
# ───────────────────────────────────────────────────────────────

class BudgetLevel(Enum):
    """Budget enforcement level after a charge or check."""
    OK = "ok"               # Under 80% — normal operation
    WARNING = "warning"     # ≥ 80% — notify, but proceed
    SOFT_CAP = "soft_cap"   # ≥ 100% — pause new, require confirmation
    HARD_CAP = "hard_cap"   # ≥ 150% — force-stop, reject all
    UNLIMITED = "unlimited" # No limit set


# ───────────────────────────────────────────────────────────────
# Budget Guard Exception
# ───────────────────────────────────────────────────────────────

class BudgetExceededError(Exception):
    """Raised when an operation is blocked by budget enforcement."""
    def __init__(
        self,
        level: BudgetLevel,
        message: str,
        feature_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.level = level
        self.feature_id = feature_id
        self.project_id = project_id
        super().__init__(message)


class BudgetWarning(Exception):
    """Non-fatal warning: operation proceeds but budget threshold crossed."""
    def __init__(
        self,
        level: BudgetLevel,
        message: str,
        feature_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.level = level
        self.feature_id = feature_id
        self.project_id = project_id
        super().__init__(message)


# ───────────────────────────────────────────────────────────────
# USD Budget Tracker
# ───────────────────────────────────────────────────────────────

@dataclass
class USDBudget:
    """Per-project USD cost budget tracker.

    Attributes:
        limit_usd: Maximum USD allowed for the project (0 = unlimited).
        spent_usd: Cumulative USD spent so far.
    """

    limit_usd: float = 0.0
    spent_usd: float = 0.0

    WARNING_RATIO: float = field(default=0.8, repr=False)
    SOFT_CAP_RATIO: float = field(default=1.0, repr=False)
    HARD_CAP_RATIO: float = field(default=1.5, repr=False)

    @property
    def remaining(self) -> float:
        """Remaining USD before hitting the soft cap."""
        if self.limit_usd <= 0:
            return -1.0  # unlimited
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def usage_ratio(self) -> float:
        """Ratio of spent / limit (0.0-∞)."""
        if self.limit_usd <= 0:
            return 0.0
        return self.spent_usd / self.limit_usd

    def check(self) -> BudgetLevel:
        """Check current USD budget status."""
        if self.limit_usd <= 0:
            return BudgetLevel.UNLIMITED
        ratio = self.usage_ratio
        if ratio >= self.HARD_CAP_RATIO:
            return BudgetLevel.HARD_CAP
        if ratio >= self.SOFT_CAP_RATIO:
            return BudgetLevel.SOFT_CAP
        if ratio >= self.WARNING_RATIO:
            return BudgetLevel.WARNING
        return BudgetLevel.OK

    def can_allocate(self, cost_usd: float) -> bool:
        """Check whether cost_usd can be added without exceeding HARD_CAP."""
        if self.limit_usd <= 0:
            return True
        return (self.spent_usd + cost_usd) <= (self.limit_usd * self.HARD_CAP_RATIO)

    def charge(self, cost_usd: float) -> BudgetLevel:
        """Charge cost_usd to the budget. Returns new level."""
        self.spent_usd += cost_usd
        return self.check()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limit_usd": self.limit_usd,
            "spent_usd": round(self.spent_usd, 4),
            "remaining": round(self.remaining, 4) if self.remaining >= 0 else -1,
            "usage_ratio": round(self.usage_ratio, 4),
            "status": self.check().value,
        }


# ───────────────────────────────────────────────────────────────
# Feature Budget Record
# ───────────────────────────────────────────────────────────────

@dataclass
class FeatureBudget:
    """Per-feature budget tracking combining token + USD dimensions.

    Attributes:
        feature_id: Unique feature identifier.
        token_budget: TokenBudget instance for this feature.
        token_spent: Total tokens consumed by this feature.
        cost_usd: Total USD cost for this feature.
    """

    feature_id: str
    token_budget: TokenBudget = field(default_factory=TokenBudget)
    token_spent: int = 0
    cost_usd: float = 0.0

    def charge_tokens(self, tokens: int, cost_usd: float = 0.0) -> BudgetLevel:
        """Charge tokens and optional USD cost to this feature."""
        self.token_spent += tokens
        self.token_budget.spent = self.token_spent
        if cost_usd:
            self.cost_usd += cost_usd
        return BudgetLevel(self.token_budget.check())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "token_limit": self.token_budget.limit,
            "token_spent": self.token_spent,
            "token_remaining": self.token_budget.remaining,
            "cost_usd": round(self.cost_usd, 4),
            "status": self.token_budget.check(),
        }


# ───────────────────────────────────────────────────────────────
# Budget Guard — Central Gatekeeper
# ───────────────────────────────────────────────────────────────

@dataclass
class BudgetGuard:
    """Central budget enforcement guard.

    Tracks per-feature token budgets and a per-project USD budget.
    Before any agent call, checks both dimensions and raises
    BudgetExceededError (hard_cap) or BudgetWarning (warning/soft_cap).

    Usage:
        guard = BudgetGuard(
            project_usd_limit=50.0,
            default_feature_token_limit=200_000,
        )
        guard.add_feature("feat-001", token_limit=100_000)

        # Pre-flight check
        try:
            guard.guard("feat-001", estimated_tokens=5000, estimated_cost=0.02)
        except BudgetExceededError:
            # Operation blocked
            return
        except BudgetWarning:
            # Proceed with caution, log warning

        # After operation
        guard.charge("feat-001", tokens=4800, cost_usd=0.018)
    """

    # Per-project USD budget
    project_usd_limit: float = 0.0
    usd_budget: USDBudget = field(default_factory=USDBudget)

    # Per-feature token budgets
    default_feature_token_limit: int = 0
    features: Dict[str, FeatureBudget] = field(default_factory=dict)

    # Accumulated totals
    _total_tokens_spent: int = field(default=0, repr=False)
    _total_usd_spent: float = field(default=0.0, repr=False)

    # Callbacks for notification
    on_warning: Optional[Callable[[str, Dict[str, Any]], None]] = None
    on_soft_cap: Optional[Callable[[str, Dict[str, Any]], None]] = None
    on_hard_cap: Optional[Callable[[str, Dict[str, Any]], None]] = None

    def __post_init__(self) -> None:
        self.usd_budget = USDBudget(limit_usd=self.project_usd_limit)

    # ── Feature management ──────────────────────────────────

    def add_feature(
        self,
        feature_id: str,
        token_limit: Optional[int] = None,
    ) -> FeatureBudget:
        """Register a feature for budget tracking.

        Args:
            feature_id: Unique feature identifier.
            token_limit: Max tokens for this feature (defaults to default_feature_token_limit).

        Returns:
            The created or existing FeatureBudget.
        """
        if feature_id in self.features:
            return self.features[feature_id]

        limit = token_limit if token_limit is not None else self.default_feature_token_limit
        tb = TokenBudget(limit=limit)
        fb = FeatureBudget(feature_id=feature_id, token_budget=tb)
        self.features[feature_id] = fb
        return fb

    def get_feature(self, feature_id: str) -> Optional[FeatureBudget]:
        """Get a feature's budget record."""
        return self.features.get(feature_id)

    def remove_feature(self, feature_id: str) -> None:
        """Remove a feature from tracking."""
        self.features.pop(feature_id, None)

    # ── Budget checks ───────────────────────────────────────

    def check_feature(self, feature_id: str) -> BudgetLevel:
        """Check a single feature's token budget status."""
        fb = self.features.get(feature_id)
        if fb is None:
            return BudgetLevel.UNLIMITED
        return BudgetLevel(fb.token_budget.check())

    def check_project(self) -> BudgetLevel:
        """Check the project-level USD budget status."""
        return self.usd_budget.check()

    def check_all(self, feature_id: Optional[str] = None) -> Dict[str, BudgetLevel]:
        """Check both feature and project budgets.

        Returns dict with 'feature' and 'project' keys.
        """
        return {
            "feature": self.check_feature(feature_id) if feature_id else BudgetLevel.OK,
            "project": self.check_project(),
        }

    def can_proceed(
        self,
        feature_id: Optional[str] = None,
        estimated_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> Tuple[bool, BudgetLevel, str]:
        """Pre-flight check: can this operation proceed?

        Returns:
            Tuple of (allowed, worst_level, reason).
            allowed=False means HARD_CAP — operation must be rejected.
            allowed=True with worst_level=WARNING/SOFT_CAP means proceed with caution.
        """
        worst_level = BudgetLevel.OK
        reasons: List[str] = []

        # Check feature token budget
        if feature_id and feature_id in self.features:
            fb = self.features[feature_id]
            if estimated_tokens > 0:
                projected = fb.token_spent + estimated_tokens
                fb.token_budget.spent = projected
                flevel = BudgetLevel(fb.token_budget.check())
                fb.token_budget.spent = fb.token_spent  # restore
            else:
                flevel = BudgetLevel(fb.token_budget.check())

            if flevel == BudgetLevel.HARD_CAP:
                return (
                    False,
                    BudgetLevel.HARD_CAP,
                    f"Feature '{feature_id}' token budget exceeded hard cap "
                    f"({fb.token_spent}/{fb.token_budget.limit} tokens)",
                )
            if flevel.value > worst_level.value:
                worst_level = flevel
            if flevel != BudgetLevel.OK and flevel != BudgetLevel.UNLIMITED:
                reasons.append(f"Feature '{feature_id}' token: {flevel.value}")

        # Check project USD budget
        if estimated_cost > 0:
            projected_usd = self._total_usd_spent + estimated_cost
            temp_budget = USDBudget(
                limit_usd=self.usd_budget.limit_usd,
                spent_usd=projected_usd,
            )
            plevel = temp_budget.check()
        else:
            plevel = self.usd_budget.check()

        if plevel == BudgetLevel.HARD_CAP:
            return (
                False,
                BudgetLevel.HARD_CAP,
                f"Project USD budget exceeded hard cap "
                f"(${self._total_usd_spent:.2f}/${self.usd_budget.limit_usd:.2f})",
            )
        if plevel.value > worst_level.value:
            worst_level = plevel
        if plevel != BudgetLevel.OK and plevel != BudgetLevel.UNLIMITED:
            reasons.append(f"Project USD: {plevel.value}")

        reason = "; ".join(reasons) if reasons else "ok"

        return True, worst_level, reason

    def guard(
        self,
        feature_id: Optional[str] = None,
        estimated_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> None:
        """Guard an operation: raise if blocked, warn if approaching limits.

        Raises:
            BudgetExceededError: if HARD_CAP — operation must be rejected.
            BudgetWarning: if WARNING or SOFT_CAP — operation may proceed with caution.
        """
        allowed, level, reason = self.can_proceed(
            feature_id=feature_id,
            estimated_tokens=estimated_tokens,
            estimated_cost=estimated_cost,
        )

        if not allowed:
            self._notify_hard_cap(reason)
            raise BudgetExceededError(
                level=level,
                message=reason,
                feature_id=feature_id,
            )

        if level == BudgetLevel.SOFT_CAP:
            self._notify_soft_cap(reason)
            raise BudgetWarning(
                level=level,
                message=reason,
                feature_id=feature_id,
            )

        if level == BudgetLevel.WARNING:
            self._notify_warning(reason)
            raise BudgetWarning(
                level=level,
                message=reason,
                feature_id=feature_id,
            )

    # ── Charging ────────────────────────────────────────────

    def charge(
        self,
        feature_id: Optional[str] = None,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> BudgetLevel:
        """Charge tokens and cost to tracked budgets.

        Returns the worst BudgetLevel across all tracked dimensions.
        """
        feature_level = BudgetLevel.OK
        project_level = BudgetLevel.OK

        if feature_id and feature_id in self.features:
            fb = self.features[feature_id]
            feature_level = fb.charge_tokens(tokens, cost_usd)

        if cost_usd > 0:
            project_level = self.usd_budget.charge(cost_usd)
            self._total_usd_spent += cost_usd

        self._total_tokens_spent += tokens

        # Determine worst level
        levels = [feature_level, project_level]
        worst = max(levels, key=lambda l: (0 if l == BudgetLevel.UNLIMITED else
                                            1 if l == BudgetLevel.OK else
                                            2 if l == BudgetLevel.WARNING else
                                            3 if l == BudgetLevel.SOFT_CAP else 4))

        if worst == BudgetLevel.HARD_CAP:
            self._notify_hard_cap(f"Budget hard cap reached after charge")
        elif worst == BudgetLevel.SOFT_CAP:
            self._notify_soft_cap(f"Budget soft cap reached after charge")
        elif worst == BudgetLevel.WARNING:
            self._notify_warning(f"Budget warning threshold crossed after charge")

        return worst

    # ── Queries ─────────────────────────────────────────────

    @property
    def total_tokens_spent(self) -> int:
        return self._total_tokens_spent

    @property
    def total_usd_spent(self) -> float:
        return self._total_usd_spent

    def feature_summary(self, feature_id: str) -> Optional[Dict[str, Any]]:
        """Get a summary for a specific feature."""
        fb = self.features.get(feature_id)
        if fb is None:
            return None
        return fb.to_dict()

    def project_summary(self) -> Dict[str, Any]:
        """Get full project budget summary."""
        return {
            "usd_budget": self.usd_budget.to_dict(),
            "total_tokens_spent": self._total_tokens_spent,
            "total_usd_spent": round(self._total_usd_spent, 4),
            "features": {
                fid: fb.to_dict() for fid, fb in self.features.items()
            },
            "feature_count": len(self.features),
        }

    # ── Notification helpers ────────────────────────────────

    def _notify_warning(self, message: str) -> None:
        logger.warning("BudgetGuard WARNING: %s", message)
        if self.on_warning:
            try:
                self.on_warning(message, self.project_summary())
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                pass

    def _notify_soft_cap(self, message: str) -> None:
        logger.warning("BudgetGuard SOFT_CAP: %s", message)
        if self.on_soft_cap:
            try:
                self.on_soft_cap(message, self.project_summary())
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                pass

    def _notify_hard_cap(self, message: str) -> None:
        logger.error("BudgetGuard HARD_CAP: %s", message)
        if self.on_hard_cap:
            try:
                self.on_hard_cap(message, self.project_summary())
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                pass

    # ── Serialization ───────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_usd_limit": self.project_usd_limit,
            "default_feature_token_limit": self.default_feature_token_limit,
            "usd_budget": self.usd_budget.to_dict(),
            "total_tokens_spent": self._total_tokens_spent,
            "total_usd_spent": round(self._total_usd_spent, 4),
            "features": {
                fid: fb.to_dict() for fid, fb in self.features.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BudgetGuard":
        guard = cls(
            project_usd_limit=data.get("project_usd_limit", 0.0),
            default_feature_token_limit=data.get("default_feature_token_limit", 0),
        )
        guard._total_tokens_spent = data.get("total_tokens_spent", 0)
        guard._total_usd_spent = data.get("total_usd_spent", 0.0)

        usd_data = data.get("usd_budget", {})
        guard.usd_budget = USDBudget(
            limit_usd=usd_data.get("limit_usd", guard.project_usd_limit),
            spent_usd=usd_data.get("spent_usd", guard._total_usd_spent),
        )

        for fid, fdata in data.get("features", {}).items():
            tb = TokenBudget(
                limit=fdata.get("token_limit", 0),
                spent=fdata.get("token_spent", 0),
            )
            fb = FeatureBudget(
                feature_id=fid,
                token_budget=tb,
                token_spent=fdata.get("token_spent", 0),
                cost_usd=fdata.get("cost_usd", 0.0),
            )
            guard.features[fid] = fb

        return guard

    def reset(self) -> None:
        """Reset all budgets to zero."""
        self._total_tokens_spent = 0
        self._total_usd_spent = 0.0
        self.usd_budget.spent_usd = 0.0
        self.features.clear()


# ───────────────────────────────────────────────────────────────
# Convenience: Guard a function call
# ───────────────────────────────────────────────────────────────

class GuardedCall:
    """Context manager / decorator that wraps a call with budget guarding.

    Usage as context manager:
        with GuardedCall(guard, "feat-001", estimated_tokens=5000):
            result = do_agent_work()

    Usage as decorator:
        @GuardedCall.decorate(guard)
        def agent_call(feature_id, estimated_tokens=0, estimated_cost=0.0):
            ...
    """

    def __init__(
        self,
        guard: BudgetGuard,
        feature_id: Optional[str] = None,
        estimated_tokens: int = 0,
        estimated_cost: float = 0.0,
        auto_charge: bool = True,
    ):
        self.guard = guard
        self.feature_id = feature_id
        self.estimated_tokens = estimated_tokens
        self.estimated_cost = estimated_cost
        self.auto_charge = auto_charge
        self._actual_tokens: int = 0
        self._actual_cost: float = 0.0

    def __enter__(self) -> "GuardedCall":
        self.guard.guard(
            feature_id=self.feature_id,
            estimated_tokens=self.estimated_tokens,
            estimated_cost=self.estimated_cost,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None and self.auto_charge:
            self.guard.charge(
                feature_id=self.feature_id,
                tokens=self._actual_tokens or self.estimated_tokens,
                cost_usd=self._actual_cost or self.estimated_cost,
            )
        return False  # don't suppress exceptions

    def set_actual(self, tokens: int, cost_usd: float = 0.0) -> None:
        """Report actual usage after the call completes."""
        self._actual_tokens = tokens
        self._actual_cost = cost_usd

    @staticmethod
    def decorate(guard: BudgetGuard):
        """Create a decorator factory."""
        def wrapper(feature_id=None, estimated_tokens=0, estimated_cost=0.0):
            return GuardedCall(
                guard=guard,
                feature_id=feature_id,
                estimated_tokens=estimated_tokens,
                estimated_cost=estimated_cost,
            )
        return wrapper
