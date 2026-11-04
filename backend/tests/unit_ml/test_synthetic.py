import numpy as np
from app.ml.synthetic import FEATURE_NAMES, generate_histories
from app.schemas.contracts import ReliabilityFeatures

DEMOGRAPHIC_WORDS = (
    "age_years",
    "gender",
    "sex",
    "religion",
    "nationality",
    "ethnic",
    "income",
)


def test_same_seed_gives_identical_output() -> None:
    a = generate_histories(200, seed=7)
    b = generate_histories(200, seed=7)
    assert np.array_equal(a.X, b.X)
    assert np.array_equal(a.y, b.y)
    assert np.array_equal(a.member, b.member)


def test_different_seed_gives_different_output() -> None:
    a = generate_histories(200, seed=7)
    b = generate_histories(200, seed=8)
    assert a.X.shape != b.X.shape or not np.array_equal(a.X, b.X)


def test_features_match_contract_and_have_no_demographics() -> None:
    ds = generate_histories(50)
    assert ds.feature_names == tuple(ReliabilityFeatures.model_fields)
    assert ds.X.shape[1] == len(FEATURE_NAMES)
    for name in ds.feature_names:
        assert not any(word in name for word in DEMOGRAPHIC_WORDS)


def test_rows_are_valid_reliability_features() -> None:
    ds = generate_histories(100)
    int_fields = {n for n, f in ReliabilityFeatures.model_fields.items() if f.annotation is int}
    for row in ds.X[::17]:
        values = {
            n: int(v) if n in int_fields else float(v)
            for n, v in zip(ds.feature_names, row, strict=True)
        }
        ReliabilityFeatures(**values)


def test_late_rate_is_plausible() -> None:
    ds = generate_histories(1000)
    assert 0.1 < float(ds.y.mean()) < 0.35
