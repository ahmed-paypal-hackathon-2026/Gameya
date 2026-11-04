"""Circle Stress-Test (REQ-AI-07): odds the circle pays every pot, per reserve %.

Monte Carlo over the members' reliability scores, run before launch so the
organiser can pick a reserve. Assumptions (docs/ml-assumptions.md):

- `scores` are in payout order: scores[k] receives the pot in cycle k.
- Most late payments are recovered in the grace period and cost nothing. Two
  events do leave the pot short:
  * a one-off missed payment (a late payment grace did not recover), per-cycle
    probability MISS_PER_P_LATE x p_late. The member catches up the next cycle
    (Smart Chase invoice / Orders v2 pay-now), which refills the reserve; a miss
    in the last cycle is never repaid inside the circle. This is the gap
    Mediation's RESERVE_COVER option bridges.
  * a dropout: the member stops paying for every remaining cycle; per-cycle
    hazard DROPOUT_PER_P_LATE x p_late, multiplied by POST_PAYOUT_MULTIPLIER once
    the member has received their own pot.
- Reserve = an upfront deposit: each member deposits reserve_pct% of the pot
  (contribution x N) when joining, so the pool exists from cycle 1. A pool built
  per payment cannot cover an early missed payment (5% of $100 is $5 a cycle).
- A run completes if every pot is paid in full and on time: no cycle's
  shortfall is ever larger than the pool.

Money is simulated in integer cents (no floats on financial values). Every
reserve level reuses the same random draws, so more reserve never lowers the
completion probability.
"""

from __future__ import annotations

import asyncio
import math
import re
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from numpy.typing import NDArray

from app.ai._llm_port import LLM_TIMEOUT_S, LLMClient
from app.ai.prose_guard import prose_problem
from app.schemas.contracts import (
    CircleSnapshot,
    Locale,
    ReliabilityScore,
    ReservePoint,
    StressTestResult,
)

MISS_PER_P_LATE = 0.15  # share of late payments that grace does not recover
DROPOUT_PER_P_LATE = 0.02  # average member: ~3.5% chance of dropping out per 6-cycle circle
POST_PAYOUT_MULTIPLIER = 2.0
TARGET_COMPLETION = 0.95
DEFAULT_RESERVE_PCTS = tuple(Decimal(p) for p in range(11))  # 0% .. 10%
# Bounds so a caller cannot ask for gigabytes: 3 float arrays of sims x N x N.
MAX_SIMS = 20_000
MAX_RESERVE_POINTS = 21
CENT = Decimal("0.01")


def _cents(amount: Decimal) -> int:
    return int((amount / CENT).to_integral_value(rounding=ROUND_HALF_UP))


def _draw_events(
    p_late: list[float], n_sims: int, seed: int
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """(dropped, missed) as bool[sims, cycles, members]; missed excludes dropped members."""
    n = len(p_late)
    p = np.asarray(p_late, dtype=np.float64)
    received = np.arange(n)[None, :] < np.arange(n)[:, None]  # [cycle, member]: paid out earlier
    dropout = DROPOUT_PER_P_LATE * p[None, :] * np.where(received, POST_PAYOUT_MULTIPLIER, 1.0)
    miss = np.broadcast_to(MISS_PER_P_LATE * p[None, :], (n, n))
    rng = np.random.default_rng(seed)
    dropped = np.logical_or.accumulate(
        rng.random((n_sims, n, n)) < np.clip(dropout, 0.0, 1.0), axis=1
    )
    missed: NDArray[np.bool_] = (rng.random((n_sims, n, n)) < np.clip(miss, 0.0, 1.0)) & ~dropped
    return dropped, missed


def run_stress_test(
    circle: CircleSnapshot,
    scores: list[ReliabilityScore],
    reserve_pcts: list[Decimal] | None = None,
    n_sims: int = 10_000,
    seed: int = 42,
) -> StressTestResult:
    """Completion probability and expected uncovered shortfall per reserve %."""
    if len(scores) != circle.size_n:
        raise ValueError(f"{len(scores)} scores for a circle of {circle.size_n}")
    if not 1 <= n_sims <= MAX_SIMS:
        raise ValueError(f"n_sims must be between 1 and {MAX_SIMS}")
    requested = DEFAULT_RESERVE_PCTS if reserve_pcts is None else tuple(reserve_pcts)
    if not requested or len(requested) > MAX_RESERVE_POINTS:
        raise ValueError(f"give between 1 and {MAX_RESERVE_POINTS} reserve_pcts")
    if not all(isinstance(p, Decimal) for p in requested):
        raise ValueError("reserve_pcts must be Decimal values")
    if not all(p.is_finite() and 0 <= p <= 100 for p in requested):
        raise ValueError("reserve_pcts must be finite and between 0 and 100")
    pcts = sorted(set(requested))

    n = circle.size_n
    amount_cents = _cents(circle.contribution_amount)
    dropped, missed = _draw_events([s.p_late for s in scores], n_sims, seed)
    shortfall = (dropped | missed).sum(axis=2, dtype=np.int64) * amount_cents  # [sims, cycles]
    # Last cycle's missers who are still in the circle catch up this cycle.
    repaid = np.zeros_like(shortfall)
    repaid[:, 1:] = (missed[:, :-1, :] & ~dropped[:, 1:, :]).sum(axis=2) * amount_cents

    points = []
    for pct in pcts:
        deposit = _cents(circle.contribution_amount * n * pct / 100)
        pool = np.full(n_sims, deposit * n, dtype=np.int64)
        uncovered = np.zeros(n_sims, dtype=np.int64)
        for k in range(n):
            pool += repaid[:, k]
            covered = np.minimum(pool, shortfall[:, k])
            pool -= covered
            uncovered += shortfall[:, k] - covered
        completion = float(np.mean(uncovered == 0))
        mean_uncovered = Decimal(int(uncovered.sum())) / n_sims * CENT
        points.append(
            ReservePoint(
                reserve_pct=pct,
                completion_probability=completion,
                expected_shortfall=mean_uncovered.quantize(CENT, rounding=ROUND_HALF_UP),
            )
        )

    return StressTestResult(
        n_sims=n_sims,
        seed=seed,
        points=points,
        recommended_reserve_pct=_recommend(points),
    )


def _recommend(points: list[ReservePoint]) -> Decimal:
    """Smallest reserve reaching the target; otherwise the best (smallest on ties)."""
    for point in points:  # sorted ascending by reserve_pct
        if point.completion_probability >= TARGET_COMPLETION:
            return point.reserve_pct
    best = max(points, key=lambda p: (p.completion_probability, -p.reserve_pct))
    return best.reserve_pct


# ---------- explanation ----------
# The engine writes every number. An LLM may add one sentence of plain context,
# which must contain no digits at all (prose_guard).


def _pct(probability: float) -> str:
    """Whole percent, rounded down so we never overstate the odds.

    The inner round() removes float noise first: int(0.29 * 100) would give 28.
    """
    return str(math.floor(round(probability * 100, 6)))


def _point(result: StressTestResult, pct: Decimal) -> ReservePoint:
    return next(p for p in result.points if p.reserve_pct == pct)


def _fmt_reserve(pct: Decimal) -> str:
    return format(pct.normalize(), "f")


def deposit_per_member(circle: CircleSnapshot, reserve_pct: Decimal) -> Decimal:
    """Upfront reserve deposit each member pays: reserve% of the pot."""
    pot = circle.contribution_amount * circle.size_n
    return (pot * reserve_pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def reserve_setup_fee(circle: CircleSnapshot, reserve_pct: Decimal) -> dict[str, str] | None:
    """The reserve deposit as a PayPal Money object for a billing plan's setup_fee.

    PayPal collects `payment_preferences.setup_fee` once, as the initial payment
    when a member activates their subscription, which is exactly the upfront
    deposit this model assumes. Pass it to the Agent Toolkit's
    `create_subscription_plan` (value is a string with 2 decimals, never a float).
    Returns None for a 0% reserve: omit setup_fee rather than send "0.00".
    """
    deposit = deposit_per_member(circle, reserve_pct)
    if deposit <= 0:
        return None
    return {"currency_code": circle.currency, "value": format(deposit, "f")}


def explanation_facts(
    result: StressTestResult, circle: CircleSnapshot | None = None
) -> dict[str, str]:
    base = result.points[0]
    rec = _point(result, result.recommended_reserve_pct)
    facts = {
        "runs": f"{result.n_sims:,}",
        "base_reserve": _fmt_reserve(base.reserve_pct),
        "base_pct": _pct(base.completion_probability),
        "rec_reserve": _fmt_reserve(rec.reserve_pct),
        "rec_pct": _pct(rec.completion_probability),
        "target_pct": _pct(TARGET_COMPLETION),
    }
    if circle is not None:
        facts["deposit"] = f"{deposit_per_member(circle, rec.reserve_pct):,.2f}"
    return facts


_TEMPLATES: dict[Locale, dict[str, str]] = {
    "en": {
        "no_reserve_needed": "In {runs} simulated runs, {base_pct}% of circles paid every pot "
        "with a {base_reserve}% reserve, so no extra reserve is needed.",
        "reserve_helps": "In {runs} simulated runs, {base_pct}% of circles paid every pot "
        "with a {base_reserve}% reserve. A {rec_reserve}% reserve raises that to {rec_pct}%.",
        "below_target": "In {runs} simulated runs, {base_pct}% of circles paid every pot "
        "with a {base_reserve}% reserve. The best option tested, a {rec_reserve}% reserve, "
        "reaches {rec_pct}%, still below {target_pct}%; consider fewer members or a smaller "
        "contribution.",
        "deposit": " That reserve is a ${deposit} deposit from each member, paid upfront.",
    },
    "ar-EG": {
        "no_reserve_needed": "في {runs} محاكاة، {base_pct}% من الجمعيات قبضت كل الأدوار "
        "باحتياطي {base_reserve}%، يعني مش محتاجين احتياطي زيادة.",
        "reserve_helps": "في {runs} محاكاة، {base_pct}% من الجمعيات قبضت كل الأدوار "
        "باحتياطي {base_reserve}%. احتياطي {rec_reserve}% بيرفعها لـ {rec_pct}%.",
        "below_target": "في {runs} محاكاة، {base_pct}% من الجمعيات قبضت كل الأدوار "
        "باحتياطي {base_reserve}%. أحسن اختيار، احتياطي {rec_reserve}%، بيوصل لـ {rec_pct}% "
        "وده أقل من {target_pct}%؛ فكّر في عدد أقل أو قسط أصغر.",
        "deposit": " الاحتياطي ده تأمين {deposit} دولار من كل عضو بيتدفع مقدم.",
    },
}


def _case(result: StressTestResult) -> str:
    base = result.points[0]
    rec = _point(result, result.recommended_reserve_pct)
    if rec.completion_probability < TARGET_COMPLETION:
        return "below_target"
    if rec.reserve_pct == base.reserve_pct:
        return "no_reserve_needed"
    return "reserve_helps"


def template_explanation(
    result: StressTestResult,
    locale: Locale = "en",
    circle: CircleSnapshot | None = None,
) -> str:
    facts = explanation_facts(result, circle)
    text = _TEMPLATES[locale][_case(result)].format(**facts)
    if circle is not None and result.recommended_reserve_pct > 0:
        text += _TEMPLATES[locale]["deposit"].format(**facts)
    return text


# The engine decides whether a reserve is needed; the LLM sentence may not argue.
_CONTRADICTS_RESERVE = re.compile(
    r"no reserve|not need|unnecessary|isn't needed|don't need|without (?:a )?reserve|"
    r"skip|waste|مش محتاج|مش لازم|ملوش لازمه|من غير احتياطي"
)


_PROMPT = """A savings-circle organiser just saw the simulated odds that their circle pays
every pot on time, and the reserve we recommend. Write ONE short, plain sentence in
{language} that helps them understand why a reserve protects members.
Do NOT write any number, percentage, amount, date, month or name. Do not tell them to do
anything. Return only the sentence."""


async def explain_stress_test(
    result: StressTestResult,
    llm: LLMClient | None = None,
    locale: Locale = "en",
    circle: CircleSnapshot | None = None,
) -> str:
    """Interface 1 (plan §7.4). Engine numbers, plus one LLM sentence if it is number-free.

    Pass `circle` to include the dollar deposit per member.
    """
    text = template_explanation(result, locale, circle)
    if llm is None:
        return text
    language = "Egyptian Arabic" if locale == "ar-EG" else "English"
    try:
        extra = await asyncio.wait_for(
            llm.generate_text(_PROMPT.format(language=language)), LLM_TIMEOUT_S
        )
    except Exception:  # noqa: BLE001 - any LLM failure keeps the engine text
        return text
    if not isinstance(extra, str) or prose_problem(extra, []) is not None:
        return text
    if _CONTRADICTS_RESERVE.search(extra.lower()):
        return text
    return f"{text} {extra.strip()}"
