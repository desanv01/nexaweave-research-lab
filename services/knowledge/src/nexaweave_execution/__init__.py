"""Opt-in execution exports, loaded only when a caller requests them.

Temporal's workflow sandbox imports this package before its pure workflow.
Eager bridge imports here would load Graphiti and NumPy inside that sandbox.
"""
from __future__ import annotations

__all__ = ["BudgetBusy", "BudgetConflict", "BudgetDenied", "BudgetError",
           "BudgetLedger", "BudgetStatus", "BudgetUncertain", "BudgetUnavailable",
           "BudgetedIngestion", "InvalidBudget", "MigrationMismatch",
           "Reservation", "ReservationState", "migrate"]

_BUDGET_EXPORTS = frozenset(__all__) - {"BudgetedIngestion"}


def __getattr__(name: str):
    if name in _BUDGET_EXPORTS:
        from . import budget
        return getattr(budget, name)
    if name == "BudgetedIngestion":
        from .budgeted_ingestion import BudgetedIngestion
        return BudgetedIngestion
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
