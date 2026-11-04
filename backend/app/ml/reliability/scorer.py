"""Runtime reliability scorer (REQ-AI-04): P(late next cycle) + top-3 factors.

Pure Python over the exported coefficients.json; no sklearn at runtime.
Framed as reliability *inside Gam'eya*, never as a credit score.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.schemas.contracts import (
    MemberId,
    ReliabilityFactor,
    ReliabilityFeatures,
    ReliabilityScore,
)

COEFFICIENTS_PATH = Path(__file__).with_name("coefficients.json")
TOP_K = 3
# Values beyond these carry no extra signal; clamping also keeps corrupted inputs
# (inf, 10**400) from overflowing float maths or the label formatting.
FEATURE_CAPS: dict[str, float] = {
    "avg_days_late": 365.0,
    "cycles_completed": 10_000,
    "failed_payments": 10_000,
    "invoices_needed": 10_000,
    "circles_completed": 1_000,
    "account_age_days": 36_500,
}
# With no payments yet these features are defaults, not evidence; report them as one factor.
HISTORY_FACTOR = "payment_history"
HISTORY_FEATURES = frozenset(
    {
        "on_time_ratio",
        "avg_days_late",
        "cycles_completed",
        "failed_payments",
        "invoices_needed",
        "no_history",
    }
)
# Model-internal inputs derived from the contract features. `no_history` lets the
# linear model separate "no payments yet" from "perfect record" (cold start).
CONTRACT_FEATURES: tuple[str, ...] = tuple(ReliabilityFeatures.model_fields)
MODEL_FEATURES: tuple[str, ...] = (*CONTRACT_FEATURES, "no_history")
_CYCLES_IDX = CONTRACT_FEATURES.index("cycles_completed")


def design_matrix(X: NDArray[np.float64]) -> NDArray[np.float64]:
    """Contract-ordered feature rows -> model inputs (adds derived columns)."""
    no_history = (X[:, _CYCLES_IDX] == 0).astype(np.float64)
    out: NDArray[np.float64] = np.column_stack([X, no_history])
    return out


@dataclass(frozen=True)
class _Model:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefs: tuple[float, ...]
    intercept: float


@cache
def _load(path: Path = COEFFICIENTS_PATH) -> _Model:
    raw = json.loads(path.read_text(encoding="utf-8"))
    names = tuple(raw["feature_names"])
    if names != MODEL_FEATURES:
        raise RuntimeError("coefficients.json does not match ReliabilityFeatures; retrain")
    return _Model(
        feature_names=names,
        means=tuple(raw["means"]),
        scales=tuple(raw["scales"]),
        coefs=tuple(raw["coefs"]),
        intercept=float(raw["intercept"]),
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _label(name: str, f: ReliabilityFeatures) -> str:
    """Human-readable fact behind a factor; numbers come straight from the features."""
    if name == HISTORY_FACTOR:
        return "No payment history yet"
    if name == "on_time_ratio":
        return f"Paid {round(f.on_time_ratio * 100)}% of contributions on time"
    if name == "avg_days_late":
        if f.avg_days_late == 0:
            return "Never paid late"
        return f"Late payments average {_plural(round(f.avg_days_late), 'day')}"
    if name == "cycles_completed":
        return f"{_plural(f.cycles_completed, 'contribution')} made"
    if name == "failed_payments":
        if f.failed_payments == 0:
            return "No failed PayPal charges"
        return _plural(f.failed_payments, "failed PayPal charge")
    if name == "invoices_needed":
        if f.invoices_needed == 0:
            return "Never needed a payment invoice"
        return f"Needed {_plural(f.invoices_needed, 'payment invoice')}"
    if name == "circles_completed":
        if f.circles_completed == 0:
            return "No circles completed yet"
        return f"Completed {_plural(f.circles_completed, 'circle')}"
    if name == "account_age_days":
        months = f.account_age_days // 30
        if months == 0:
            return "Joined Gam'eya this month"
        return f"Member for {_plural(months, 'month')}"
    raise KeyError(name)


def _sigmoid(x: float) -> float:
    """Overflow-safe logistic: extreme (e.g. corrupted) feature values give 0 or 1, not a crash."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _clamp(f: ReliabilityFeatures) -> ReliabilityFeatures:
    over = {k: cap for k, cap in FEATURE_CAPS.items() if not getattr(f, k) <= cap}
    if not over:
        return f
    data = f.model_dump()
    for k, cap in over.items():
        data[k] = int(cap) if isinstance(data[k], int) else float(cap)
    return ReliabilityFeatures.model_validate(data)


def score_reliability(features: ReliabilityFeatures, member_id: MemberId) -> ReliabilityScore:
    """P(late next cycle) with the 3 features that moved it most.

    `contribution` is coef x standardised value on the logit scale:
    positive raises the risk, negative lowers it.
    """
    m = _load()
    features = _clamp(features)
    values = features.model_dump()
    values["no_history"] = float(features.cycles_completed == 0)
    contributions = [
        (name, c * (float(values[name]) - mu) / s)
        for name, mu, s, c in zip(m.feature_names, m.means, m.scales, m.coefs, strict=True)
    ]
    logit = m.intercept + sum(v for _, v in contributions)
    if features.cycles_completed == 0:
        history = sum(v for name, v in contributions if name in HISTORY_FEATURES)
        contributions = [nv for nv in contributions if nv[0] not in HISTORY_FEATURES]
        contributions.append((HISTORY_FACTOR, history))
    else:
        # The indicator is off; it still counts in the logit but is not a fact to show.
        contributions = [nv for nv in contributions if nv[0] != "no_history"]
    top = sorted(contributions, key=lambda nv: (-abs(nv[1]), nv[0]))[:TOP_K]
    return ReliabilityScore(
        member_id=member_id,
        p_late=_sigmoid(logit),
        top_factors=[
            ReliabilityFactor(feature=name, contribution=round(v, 4), label=_label(name, features))
            for name, v in top
        ],
    )


def p_late_matrix(X: NDArray[np.float64]) -> NDArray[np.float64]:
    """Vectorised P(late) for rows in ReliabilityFeatures order (eval/simulation use)."""
    m = _load()
    caps = np.array([FEATURE_CAPS.get(name, np.inf) for name in CONTRACT_FEATURES])
    X = np.minimum(np.nan_to_num(X, nan=0.0), caps)
    z = (design_matrix(X) - np.asarray(m.means)) / np.asarray(m.scales)
    logit = m.intercept + z @ np.asarray(m.coefs)
    out: NDArray[np.float64] = 0.5 * (1.0 + np.tanh(0.5 * logit))  # overflow-safe sigmoid
    return out
