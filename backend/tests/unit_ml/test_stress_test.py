import asyncio
import time
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from app.ml import stress_test
from app.ml.reliability.personas import persona_scores
from app.ml.stress_test import (
    TARGET_COMPLETION,
    explain_stress_test,
    run_stress_test,
    template_explanation,
)
from app.schemas.contracts import CircleSnapshot, ReliabilityScore, StressTestResult

PAYOUT_ORDER = ["amina", "sara", "youssef", "karim", "omar", "lina"]


def circle(n: int = 6, amount: str = "100") -> CircleSnapshot:
    return CircleSnapshot(
        circle_id="c1",
        name="Test circle",
        contribution_amount=Decimal(amount),
        cycle_unit="MONTH",
        size_n=n,
        start_date=date(2026, 11, 1),
    )


def scores(p_lates: list[float]) -> list[ReliabilityScore]:
    return [
        ReliabilityScore(member_id=f"m{i}", p_late=p, top_factors=[]) for i, p in enumerate(p_lates)
    ]


def persona_result() -> StressTestResult:
    s = persona_scores()
    return run_stress_test(circle(), [s[k] for k in PAYOUT_ORDER])


# ---------- TST-14 ----------
def test_same_seed_is_deterministic_and_seed_matters() -> None:
    s = scores([0.1, 0.3, 0.5, 0.2, 0.6, 0.1])
    assert run_stress_test(circle(), s, seed=42) == run_stress_test(circle(), s, seed=42)
    assert run_stress_test(circle(), s, seed=42) != run_stress_test(circle(), s, seed=43)


def test_twelve_members_ten_thousand_runs_under_budget() -> None:
    s = scores([0.05 + 0.03 * i for i in range(12)])
    run_stress_test(circle(12), s)  # warm-up
    start = time.perf_counter()
    result = run_stress_test(circle(12), s, n_sims=10_000)
    assert time.perf_counter() - start < 1.5
    assert result.n_sims == 10_000


def test_more_reserve_never_hurts() -> None:
    result = persona_result()
    completion = [p.completion_probability for p in result.points]
    shortfall = [p.expected_shortfall for p in result.points]
    assert completion == sorted(completion)
    assert shortfall == sorted(shortfall, reverse=True)


def test_default_sweep_is_zero_to_ten_percent_with_money_shortfall() -> None:
    result = persona_result()
    assert [p.reserve_pct for p in result.points] == [Decimal(i) for i in range(11)]
    for p in result.points:
        assert isinstance(p.expected_shortfall, Decimal)
        assert p.expected_shortfall == p.expected_shortfall.quantize(Decimal("0.01"))


# ---------- model behaviour ----------
def test_perfectly_reliable_circle_needs_no_reserve() -> None:
    result = run_stress_test(circle(), scores([0.0] * 6))
    assert all(p.completion_probability == 1.0 for p in result.points)
    assert result.recommended_reserve_pct == Decimal(0)


def test_riskier_members_lower_the_odds() -> None:
    safe = run_stress_test(circle(), scores([0.03] * 6))
    risky = run_stress_test(circle(), scores([0.6] * 6))
    assert risky.points[0].completion_probability < safe.points[0].completion_probability


def test_reliable_circle_reaches_target_with_small_reserve() -> None:
    result = run_stress_test(circle(), scores([0.03] * 6))
    assert result.recommended_reserve_pct <= Decimal(5)
    rec = next(p for p in result.points if p.reserve_pct == result.recommended_reserve_pct)
    assert rec.completion_probability >= TARGET_COMPLETION


def test_upfront_deposit_covers_a_missed_payment_from_cycle_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Member 0 misses every cycle and catches up the next; nobody drops out.
    monkeypatch.setattr(stress_test, "MISS_PER_P_LATE", 1.0)
    monkeypatch.setattr(stress_test, "DROPOUT_PER_P_LATE", 0.0)
    s = scores([1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    result = run_stress_test(circle(), s, reserve_pcts=[Decimal(2), Decimal(3)], n_sims=50)
    by_pct = {p.reserve_pct: p.completion_probability for p in result.points}
    # Pool = 6 members x pct% x $600 pot: 2% = $72 < $100 missed; 3% = $108 covers it.
    assert by_pct[Decimal(2)] == 0.0
    assert by_pct[Decimal(3)] == 1.0


def test_unreachable_target_recommends_best_option() -> None:
    result = persona_result()
    best = max(p.completion_probability for p in result.points)
    rec = next(p for p in result.points if p.reserve_pct == result.recommended_reserve_pct)
    if best < TARGET_COMPLETION:
        assert rec.completion_probability == best
        assert rec.reserve_pct == min(
            p.reserve_pct for p in result.points if p.completion_probability == best
        )


@pytest.mark.parametrize(
    ("n_scores", "pcts", "message"),
    [
        (5, None, "scores for a circle of 6"),
        (6, [Decimal(-1)], "between 0 and 100"),
    ],
)
def test_invalid_inputs_are_rejected(
    n_scores: int, pcts: list[Decimal] | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_stress_test(circle(), scores([0.1] * n_scores), reserve_pcts=pcts)


# ---------- explanation ----------
def _rec(result: StressTestResult) -> Any:
    return next(p for p in result.points if p.reserve_pct == result.recommended_reserve_pct)


def test_templates_state_exactly_the_engine_numbers() -> None:
    for result in (
        persona_result(),
        run_stress_test(circle(), scores([0.03] * 6)),
        run_stress_test(circle(), scores([0.0] * 6)),
    ):
        facts = stress_test.explanation_facts(result)
        for locale in ("en", "ar-EG"):
            text = template_explanation(result, locale)
            assert f"{facts['base_pct']}%" in text
            assert f"{facts['rec_pct']}%" in text or facts["rec_reserve"] == facts["base_reserve"]
            assert "E+" not in text


def test_reliable_circle_explanation_reads_as_expected() -> None:
    result = run_stress_test(circle(), scores([0.03] * 6))
    text = template_explanation(result)
    assert text.startswith("In 10,000 simulated runs, 82% of circles")
    assert (
        f"A {stress_test._fmt_reserve(_rec(result).reserve_pct)}% reserve raises that to 97%."
        in text
    )


@pytest.mark.parametrize(
    ("probability", "shown"),
    [(0.29, "29"), (0.57, "57"), (0.999999, "99"), (0.95, "95"), (1.0, "100")],
)
def test_percent_is_exact_and_never_rounded_up(probability: float, shown: str) -> None:
    # Review finding 4: int(0.29 * 100) used to give 28.
    assert stress_test._pct(probability) == shown


def test_explanation_states_the_dollar_deposit_per_member() -> None:
    result = run_stress_test(circle(), scores([0.03] * 6))
    deposit = stress_test.deposit_per_member(circle(), _rec(result).reserve_pct)
    assert deposit == Decimal("18.00")  # 3% of a 6 x $100 pot
    assert "$18.00 deposit from each member" in template_explanation(result, circle=circle())
    assert "$" not in template_explanation(result)  # no circle -> no deposit sentence


def test_int_reserve_pct_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="Decimal"):
        run_stress_test(circle(), scores([0.1] * 6), reserve_pcts=[5])  # type: ignore[list-item]


class FakeLLM:
    model_name = "fake-llm"

    def __init__(self, text: Any = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def generate_text(self, prompt: str) -> Any:
        if self.error:
            raise self.error
        return self.text


def test_llm_may_only_add_a_number_free_sentence() -> None:
    result = run_stress_test(circle(), scores([0.03] * 6))
    engine = template_explanation(result)
    context = "A small shared cushion lets the circle pay on time even when someone slips."
    assert asyncio.run(explain_stress_test(result, FakeLLM(context))) == f"{engine} {context}"

    # Review finding 3: reshuffled engine digits used to pass the set-based check.
    misleading = "In 10 runs, 97% of circles paid every pot with a 0% reserve."
    assert asyncio.run(explain_stress_test(result, FakeLLM(misleading))) == engine
    assert asyncio.run(explain_stress_test(result, FakeLLM(["not", "text"]))) == engine


def test_llm_failure_and_no_llm_give_the_template() -> None:
    result = persona_result()
    expected = template_explanation(result, "ar-EG")
    assert (
        asyncio.run(explain_stress_test(result, FakeLLM(error=RuntimeError()), "ar-EG")) == expected
    )
    assert asyncio.run(explain_stress_test(result, locale="ar-EG")) == expected


def test_llm_sentence_may_not_contradict_the_recommendation() -> None:
    result = run_stress_test(circle(), scores([0.03] * 6))
    engine = template_explanation(result)
    for contradiction in (
        "Honestly this circle could skip the cushion entirely.",
        "A cushion is unnecessary for a group of friends like this.",
    ):
        assert asyncio.run(explain_stress_test(result, FakeLLM(contradiction))) == engine


# ---------- PayPal alignment: reserve -> billing plan setup_fee ----------
PAYPAL_MONEY_VALUE = r"^((-?[0-9]+)|(-?([0-9]+)?[.][0-9]+))$"  # Subscriptions v1 Money.value


@pytest.mark.parametrize(
    ("amount", "n", "pct", "value"),
    [
        ("100", 6, "3", "18.00"),
        ("100", 6, "9", "54.00"),
        ("33.33", 7, "2.5", "5.83"),
        ("5000", 24, "10", "12000.00"),
    ],
)
def test_reserve_setup_fee_is_paypal_money(amount: str, n: int, pct: str, value: str) -> None:
    import re

    c = circle(n, amount)
    fee = stress_test.reserve_setup_fee(c, Decimal(pct))
    assert fee == {"currency_code": "USD", "value": value}
    assert re.fullmatch(PAYPAL_MONEY_VALUE, fee["value"])
    assert "E" not in fee["value"] and len(fee["value"]) <= 32


def test_zero_reserve_means_no_setup_fee() -> None:
    assert stress_test.reserve_setup_fee(circle(), Decimal(0)) is None
