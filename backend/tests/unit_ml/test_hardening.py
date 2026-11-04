"""Regression tests for the pre-commit security/bug review of the P3 ML code."""

from datetime import date
from decimal import Decimal

import numpy as np
import pytest
from app.ml.reliability.scorer import p_late_matrix, score_reliability
from app.ml.stress_test import MAX_RESERVE_POINTS, MAX_SIMS, run_stress_test
from app.schemas.contracts import CircleSnapshot, ReliabilityFeatures, ReliabilityScore

CIRCLE = CircleSnapshot(
    circle_id="c1",
    name="Hardening",
    contribution_amount=Decimal(100),
    cycle_unit="MONTH",
    size_n=3,
    start_date=date(2026, 11, 1),
)
SCORES = [ReliabilityScore(member_id=f"m{i}", p_late=0.2, top_factors=[]) for i in range(3)]


# ---------- finding 1: extreme features must not crash the scorer ----------
@pytest.mark.parametrize(
    "overrides",
    [
        {"cycles_completed": 10**9},
        {"avg_days_late": 1e9, "invoices_needed": 10**9},
        {"account_age_days": 10**12},
        {"cycles_completed": 10**400},  # review: int too large for float
        {"avg_days_late": float("inf")},  # review: round(inf) in the label
    ],
)
def test_extreme_features_give_a_probability_not_a_crash(
    overrides: dict[str, object],
) -> None:
    base: dict[str, object] = {
        "on_time_ratio": 0.5,
        "avg_days_late": 3.0,
        "cycles_completed": 10,
        "failed_payments": 0,
        "invoices_needed": 1,
        "circles_completed": 0,
        "account_age_days": 100,
    }
    score = score_reliability(ReliabilityFeatures.model_validate({**base, **overrides}), "m")
    assert 0.0 <= score.p_late <= 1.0


def test_matrix_scorer_handles_extreme_rows_without_overflow() -> None:
    rows = np.array([[0.0, 1e9, 1e9, 0, 1e9, 0, 0], [1.0, 0, 1e9, 0, 0, 1e9, 1e12]])
    with np.errstate(over="raise"):
        p = p_late_matrix(rows)
    assert ((p >= 0) & (p <= 1)).all()


# ---------- findings 2 and 8: bounded, validated stress-test inputs ----------
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"n_sims": MAX_SIMS + 1}, "n_sims"),
        ({"n_sims": 0}, "n_sims"),
        ({"reserve_pcts": []}, "reserve_pcts"),
        (
            {"reserve_pcts": [Decimal(i) for i in range(MAX_RESERVE_POINTS + 1)]},
            "reserve_pcts",
        ),
        ({"reserve_pcts": [Decimal("NaN")]}, "finite"),
        ({"reserve_pcts": [Decimal("Infinity")]}, "finite"),
        ({"reserve_pcts": [Decimal(101)]}, "between 0 and 100"),
    ],
)
def test_stress_test_rejects_unbounded_or_invalid_inputs(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_stress_test(CIRCLE, SCORES, **kwargs)  # type: ignore[arg-type]


def test_stress_test_accepts_the_largest_allowed_request() -> None:
    result = run_stress_test(CIRCLE, SCORES, n_sims=MAX_SIMS)
    assert result.n_sims == MAX_SIMS
