"""Reliability histories for the six demo personas (plan §4.4, golden story §5.1).

Omar is the high-risk member flagged in cycle 3 (p_late must stay > 0.5 so the
Leash high_risk_p_late threshold and Smart Chase fire). Person 5 can turn
`persona_scores()` output into shared fixtures.

    cd backend && python -m app.ml.reliability.personas
"""

from __future__ import annotations

from app.ml.reliability.scorer import score_reliability
from app.schemas.contracts import ReliabilityFeatures, ReliabilityScore

PERSONA_FEATURES: dict[str, ReliabilityFeatures] = {
    "amina": ReliabilityFeatures(
        on_time_ratio=1.0,
        avg_days_late=0.0,
        cycles_completed=24,
        failed_payments=0,
        invoices_needed=0,
        circles_completed=3,
        account_age_days=760,
    ),
    "omar": ReliabilityFeatures(
        on_time_ratio=0.5,
        avg_days_late=9.0,
        cycles_completed=8,
        failed_payments=3,
        invoices_needed=4,
        circles_completed=0,
        account_age_days=260,
    ),
    "sara": ReliabilityFeatures(
        on_time_ratio=0.95,
        avg_days_late=2.0,
        cycles_completed=20,
        failed_payments=0,
        invoices_needed=1,
        circles_completed=2,
        account_age_days=640,
    ),
    "youssef": ReliabilityFeatures(
        on_time_ratio=0.9,
        avg_days_late=3.0,
        cycles_completed=10,
        failed_payments=1,
        invoices_needed=1,
        circles_completed=1,
        account_age_days=330,
    ),
    "lina": ReliabilityFeatures(
        on_time_ratio=1.0,
        avg_days_late=0.0,
        cycles_completed=0,
        failed_payments=0,
        invoices_needed=0,
        circles_completed=0,
        account_age_days=14,
    ),
    "karim": ReliabilityFeatures(
        on_time_ratio=0.8,
        avg_days_late=4.0,
        cycles_completed=15,
        failed_payments=1,
        invoices_needed=2,
        circles_completed=1,
        account_age_days=480,
    ),
}


def persona_scores() -> dict[str, ReliabilityScore]:
    return {pid: score_reliability(f, pid) for pid, f in PERSONA_FEATURES.items()}


if __name__ == "__main__":
    for pid, s in persona_scores().items():
        factors = "; ".join(f"{f.label} ({f.contribution:+.2f})" for f in s.top_factors)
        print(f"{pid:<8} p_late={s.p_late:.2f}  {factors}")
