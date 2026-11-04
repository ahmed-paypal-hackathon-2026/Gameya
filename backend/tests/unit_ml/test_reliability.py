import json
import time
from datetime import date

import numpy as np
import pytest
from app.ml.reliability.features import build_features
from app.ml.reliability.personas import PERSONA_FEATURES, persona_scores
from app.ml.reliability.scorer import (
    COEFFICIENTS_PATH,
    HISTORY_FACTOR,
    MODEL_FEATURES,
    p_late_matrix,
    score_reliability,
)
from app.ml.reliability.train import train
from app.ml.synthetic import generate_histories
from app.schemas.contracts import ReliabilityFeatures
from sklearn.metrics import roc_auc_score

LEASH_HIGH_RISK_P_LATE = 0.5  # P2 Leash default threshold


def _features(**overrides: object) -> ReliabilityFeatures:
    base: dict[str, object] = {
        "on_time_ratio": 0.9,
        "avg_days_late": 3.0,
        "cycles_completed": 10,
        "failed_payments": 1,
        "invoices_needed": 1,
        "circles_completed": 1,
        "account_age_days": 300,
    }
    return ReliabilityFeatures.model_validate({**base, **overrides})


def test_shipped_model_meets_auc_on_unseen_members() -> None:
    # Fresh seed = members the model never saw.
    ds = generate_histories(3000, seed=2026)
    auc = roc_auc_score(ds.y, p_late_matrix(ds.X))
    assert auc >= 0.75


def test_shipped_artefact_matches_contract_and_records_metrics() -> None:
    raw = json.loads(COEFFICIENTS_PATH.read_text(encoding="utf-8"))
    assert tuple(raw["feature_names"]) == MODEL_FEATURES
    assert raw["metrics"]["holdout_roc_auc"] >= 0.75
    assert "SIMULATED" in raw["data"]


def test_training_is_deterministic() -> None:
    ds = generate_histories(500, seed=1)
    assert train(ds, seed=3) == train(ds, seed=3)


def test_score_is_probability_with_three_labelled_factors() -> None:
    s = score_reliability(_features(), "m1")
    assert s.member_id == "m1"
    assert 0.0 <= s.p_late <= 1.0
    assert len(s.top_factors) == 3
    assert all(f.label for f in s.top_factors)
    assert len({f.feature for f in s.top_factors}) == 3


def test_factors_are_sorted_by_impact() -> None:
    s = score_reliability(_features(), "m1")
    impacts = [abs(f.contribution) for f in s.top_factors]
    assert impacts == sorted(impacts, reverse=True)


def test_scoring_is_fast() -> None:
    f = _features()
    score_reliability(f, "warmup")
    start = time.perf_counter()
    for _ in range(200):
        score_reliability(f, "m1")
    assert (time.perf_counter() - start) / 200 < 0.005


def test_messy_history_scores_riskier_than_clean_history() -> None:
    clean = score_reliability(
        _features(on_time_ratio=1.0, avg_days_late=0.0, invoices_needed=0), "a"
    )
    messy = score_reliability(
        _features(on_time_ratio=0.5, avg_days_late=10.0, invoices_needed=5), "b"
    )
    assert messy.p_late > clean.p_late


def test_new_member_history_is_one_factor() -> None:
    s = score_reliability(
        _features(
            on_time_ratio=1.0,
            avg_days_late=0.0,
            cycles_completed=0,
            failed_payments=0,
            invoices_needed=0,
            circles_completed=0,
            account_age_days=5,
        ),
        "new",
    )
    names = {f.feature for f in s.top_factors}
    assert HISTORY_FACTOR in names
    assert "on_time_ratio" not in names
    assert "Never paid late" not in {f.label for f in s.top_factors}


def test_no_history_indicator_is_never_shown_for_existing_members() -> None:
    for f in PERSONA_FEATURES.values():
        if f.cycles_completed:
            labels = {x.feature for x in score_reliability(f, "x").top_factors}
            assert "no_history" not in labels


def test_personas_omar_is_high_risk_and_others_are_not() -> None:
    scores = persona_scores()
    assert scores["omar"].p_late > LEASH_HIGH_RISK_P_LATE
    for pid, s in scores.items():
        if pid != "omar":
            assert s.p_late < LEASH_HIGH_RISK_P_LATE, pid


def test_build_features_matches_synthetic_definitions() -> None:
    f = build_features(
        payments_made=10,
        late_payments=2,
        total_days_late=9.0,
        failed_payments=1,
        invoices_needed=2,
        circles_completed=1,
        account_created=date(2026, 1, 1),
        today=date(2026, 3, 2),
    )
    assert f.on_time_ratio == pytest.approx(0.8)
    assert f.avg_days_late == pytest.approx(4.5)  # per late payment, not per cycle
    assert f.account_age_days == 60


def test_build_features_for_new_member() -> None:
    f = build_features(
        payments_made=0,
        late_payments=0,
        total_days_late=0.0,
        failed_payments=0,
        invoices_needed=0,
        circles_completed=0,
        account_created=date(2026, 10, 1),
        today=date(2026, 10, 1),
    )
    assert f.on_time_ratio == 1.0
    assert f.avg_days_late == 0.0


def test_build_features_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError, match="late_payments"):
        build_features(
            payments_made=1,
            late_payments=2,
            total_days_late=1.0,
            failed_payments=0,
            invoices_needed=0,
            circles_completed=0,
            account_created=date(2026, 1, 1),
            today=date(2026, 1, 2),
        )


def test_matrix_and_single_scorer_agree() -> None:
    f = _features()
    row = np.array([[float(v) for v in f.model_dump().values()]])
    assert p_late_matrix(row)[0] == pytest.approx(score_reliability(f, "m").p_late)
