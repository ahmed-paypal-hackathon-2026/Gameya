"""Build ReliabilityFeatures from raw ledger counts (called by Person 1).

Definitions match app/ml/synthetic.py exactly; see docs/ml-assumptions.md.
"""

from __future__ import annotations

from datetime import date

from app.schemas.contracts import ReliabilityFeatures


def build_features(
    *,
    payments_made: int,
    late_payments: int,
    total_days_late: float,
    failed_payments: int,
    invoices_needed: int,
    circles_completed: int,
    account_created: date,
    today: date,
) -> ReliabilityFeatures:
    """`total_days_late` is summed over late payments only; counts are lifetime in Gam'eya."""
    if late_payments > payments_made:
        raise ValueError("late_payments cannot exceed payments_made")
    return ReliabilityFeatures(
        on_time_ratio=(payments_made - late_payments) / payments_made if payments_made else 1.0,
        avg_days_late=total_days_late / late_payments if late_payments else 0.0,
        cycles_completed=payments_made,
        failed_payments=failed_payments,
        invoices_needed=invoices_needed,
        circles_completed=circles_completed,
        account_age_days=max((today - account_created).days, 0),
    )
