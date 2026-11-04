"""Train the reliability model on synthetic histories and export coefficients.

    cd backend && python -m app.ml.reliability.train

Writes app/ml/reliability/coefficients.json (plain numbers, no pickle) so the
runtime scorer needs neither sklearn nor the training data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import sklearn  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import brier_score_loss, roc_auc_score  # type: ignore[import-untyped]
from sklearn.model_selection import GroupShuffleSplit  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from app.ml.reliability.scorer import MODEL_FEATURES, design_matrix
from app.ml.synthetic import SyntheticDataset, generate_histories

COEFFICIENTS_PATH = Path(__file__).with_name("coefficients.json")


def train(ds: SyntheticDataset, seed: int = 42) -> dict[str, Any]:
    """Fit scaler + logistic regression with a by-member 80/20 hold-out split."""
    split = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
    train_idx, test_idx = next(split.split(ds.X, ds.y, ds.member))
    X = design_matrix(ds.X)

    scaler = StandardScaler().fit(X[train_idx])
    model = LogisticRegression(max_iter=1000).fit(scaler.transform(X[train_idx]), ds.y[train_idx])

    p_test = model.predict_proba(scaler.transform(X[test_idx]))[:, 1]
    return {
        "feature_names": list(MODEL_FEATURES),
        "means": scaler.mean_.tolist(),
        "scales": scaler.scale_.tolist(),
        "coefs": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "metrics": {
            "holdout_roc_auc": round(float(roc_auc_score(ds.y[test_idx], p_test)), 4),
            "holdout_brier": round(float(brier_score_loss(ds.y[test_idx], p_test)), 4),
            "late_rate": round(float(np.mean(ds.y)), 4),
            "rows": len(ds.y),
            "members": int(ds.member.max()) + 1,
        },
        "seed": seed,
        "sklearn_version": sklearn.__version__,
        "data": "SIMULATED (see docs/ml-assumptions.md)",
    }


def main() -> None:
    artefact = train(generate_histories(n_members=10_000, seed=42))
    COEFFICIENTS_PATH.write_text(json.dumps(artefact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(artefact["metrics"], indent=2))
    for name, coef in zip(artefact["feature_names"], artefact["coefs"], strict=True):
        print(f"  {name:<20} {coef:+.3f}")


if __name__ == "__main__":
    main()
