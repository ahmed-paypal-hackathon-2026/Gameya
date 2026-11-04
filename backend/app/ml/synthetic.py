"""Synthetic member-history generator for the reliability model (REQ-ML-01).

We have no real circle data, so we simulate members with a hidden payment
discipline and train on what the ledger would have recorded. Every distribution
and coefficient lives in `SyntheticParams` and is documented in
docs/ml-assumptions.md. Output is SIMULATED and must be labelled as such.

Each row is one (member, cycle) snapshot: the 7 ReliabilityFeatures computed
from the member's history *before* that cycle, and label 1 if the member paid
that cycle late. No demographic attribute is generated or used.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.schemas.contracts import ReliabilityFeatures

FEATURE_NAMES: tuple[str, ...] = tuple(ReliabilityFeatures.model_fields)
DAYS_PER_CYCLE = 30


@dataclass(frozen=True)
class SyntheticParams:
    # Hidden traits per member
    discipline_beta: tuple[float, float] = (2.0, 1.0)  # mean ~0.67, wide spread
    shock_rate_beta: tuple[float, float] = (1.5, 12.0)  # mean ~0.11 per cycle
    shock_persistence: float = 0.6  # P(shock continues into next cycle)
    account_age_days: tuple[int, int] = (30, 1500)
    # Circle shape
    circles_per_member: tuple[int, int] = (1, 4)
    cycles_per_circle: tuple[int, int] = (6, 12)
    # P(late) = sigmoid(intercept - discipline_w*discipline
    #                    + shock_w*shock + post_payout_w*after_payout)
    intercept: float = 2.5
    discipline_w: float = 8.0
    shock_w: float = 2.2
    post_payout_w: float = 0.8
    # What a late payment looks like in the ledger
    base_days_late: float = 2.0
    extra_days_late_w: float = 8.0  # scaled by (1 - discipline)
    p_failed_given_late: float = 0.5  # subscription charge failed first
    invoice_after_days: int = 2  # Smart Chase invoices at due+1d


DEFAULT_PARAMS = SyntheticParams()


@dataclass(frozen=True)
class SyntheticDataset:
    X: NDArray[np.float64]  # (rows, 7) in FEATURE_NAMES order
    y: NDArray[np.int8]  # 1 = paid this cycle late
    member: NDArray[np.int32]  # member index, for group-aware splits
    feature_names: tuple[str, ...] = FEATURE_NAMES


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def generate_histories(
    n_members: int = 10_000,
    seed: int = 42,
    params: SyntheticParams = DEFAULT_PARAMS,
) -> SyntheticDataset:
    """Simulate `n_members` histories; same seed gives identical output."""
    p = params
    rng = np.random.default_rng(seed)
    rows: list[tuple[float, ...]] = []
    labels: list[int] = []
    members: list[int] = []

    for m in range(n_members):
        discipline = float(rng.beta(*p.discipline_beta))
        shock_rate = float(rng.beta(*p.shock_rate_beta))
        age = int(rng.integers(p.account_age_days[0], p.account_age_days[1] + 1))
        n_circles = int(rng.integers(p.circles_per_member[0], p.circles_per_member[1] + 1))

        paid = late = failed = invoices = 0
        days_late_sum = 0.0
        shock = False

        for circles_done in range(n_circles):
            length = int(rng.integers(p.cycles_per_circle[0], p.cycles_per_circle[1] + 1))
            payout_slot = int(rng.integers(0, length))
            u_shock, u_late, u_failed = rng.random((3, length))
            days_draw = rng.poisson(p.extra_days_late_w * (1.0 - discipline), length)

            for t in range(length):
                rows.append(
                    (
                        (paid - late) / paid if paid else 1.0,
                        days_late_sum / late if late else 0.0,
                        float(paid),
                        float(failed),
                        float(invoices),
                        float(circles_done),
                        float(age),
                    )
                )
                members.append(m)

                shock = bool(u_shock[t] < (p.shock_persistence if shock else shock_rate))
                logit = (
                    p.intercept
                    - p.discipline_w * discipline
                    + p.shock_w * shock
                    + p.post_payout_w * (t > payout_slot)
                )
                is_late = bool(u_late[t] < _sigmoid(logit))
                labels.append(int(is_late))

                paid += 1
                age += DAYS_PER_CYCLE
                if is_late:
                    late += 1
                    days = p.base_days_late + float(days_draw[t])
                    days_late_sum += days
                    failed += int(u_failed[t] < p.p_failed_given_late)
                    invoices += int(days >= p.invoice_after_days)

    return SyntheticDataset(
        X=np.asarray(rows, dtype=np.float64),
        y=np.asarray(labels, dtype=np.int8),
        member=np.asarray(members, dtype=np.int32),
    )


def write_csv(ds: SyntheticDataset, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["member", *ds.feature_names, "late_next_cycle"])
        for member, x, y in zip(ds.member, ds.X, ds.y, strict=True):
            w.writerow([int(member), *(round(float(v), 4) for v in x), int(y)])
