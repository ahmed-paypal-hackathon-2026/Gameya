"""Regression tests for the pre-commit security/bug review of the P3 AI code."""

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from app.ai.fair_order import (
    build_facts,
    explain_order_detailed,
    propose_payout_order,
)
from app.ai.needs_extractor import (
    MAX_NEED_AMOUNT,
    MAX_TEXT_CHARS,
    extract_need_detailed,
    extract_need_rules,
    usd_amounts,
)
from app.ai.prose_guard import prose_problem
from app.schemas.contracts import (
    CircleSnapshot,
    MemberProfile,
    MemberSnapshot,
    OrderProposal,
    SlotAssignment,
    SlotDate,
)
from hypothesis import given, settings
from hypothesis import strategies as st

CIRCLE = CircleSnapshot(
    circle_id="c1",
    name="Hardening",
    contribution_amount=Decimal(100),
    cycle_unit="MONTH",
    size_n=2,
    start_date=date(2026, 11, 1),
)


class FakeLLM:
    model_name = "fake-llm"

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        self.prompts.append(prompt)
        return self.reply

    async def generate_text(self, prompt: str) -> str:
        raise NotImplementedError


def _two_members() -> tuple[OrderProposal, list[MemberProfile]]:
    members = [
        MemberProfile(
            member=MemberSnapshot(member_id=mid, display_name=mid.title()),
            need=None,
            reliability_p_late=0.1,
        )
        for mid in ("sara", "omar")
    ]
    slots = [
        SlotDate(slot_index=1, payout_date=date(2026, 11, 1)),
        SlotDate(slot_index=2, payout_date=date(2026, 12, 1)),
    ]
    return propose_payout_order(members, slots, seed=1), members


# ---------- finding 3: past dates must not buy slot 1 ----------
@pytest.mark.parametrize(
    "text", ["wedding in March 2020", "surgery on 5/3/2019", "trip in May 2031"]
)
def test_dates_outside_the_circle_horizon_are_dropped(text: str) -> None:
    need = extract_need_rules(text, CIRCLE)
    assert need.target_date is None
    assert need.raw_month_hint is None


def test_llm_past_date_is_dropped_too() -> None:
    reply = {
        "category": "WEDDING",
        "target_date": "2026-10-01",  # one month before the circle starts
        "amount_needed": None,
        "urgency": 3,
        "flexibility": "low",
        "confidence": 0.9,
    }
    result = asyncio.run(extract_need_detailed("wedding", "en", CIRCLE, FakeLLM(reply)))
    assert result.need.target_date is None


# ---------- findings 6 and 7: amounts and text length ----------
def test_absurd_and_sub_cent_amounts_are_ignored() -> None:
    assert usd_amounts("$99999999999999") == []
    assert usd_amounts("$0.001") == []
    assert usd_amounts(f"${MAX_NEED_AMOUNT}") == [MAX_NEED_AMOUNT]


def test_text_past_the_limit_is_ignored_by_both_paths() -> None:
    padding = "x" * MAX_TEXT_CHARS
    assert extract_need_rules(padding + " $500", CIRCLE).amount_needed is None
    reply = {
        "category": "OTHER",
        "target_date": None,
        "amount_needed": "500",
        "urgency": 2,
        "flexibility": "high",
        "confidence": 0.5,
    }
    llm = FakeLLM(reply)
    result = asyncio.run(extract_need_detailed(padding + " $500", "en", CIRCLE, llm))
    assert result.need.amount_needed is None  # the LLM never saw "$500"
    assert "$500" not in llm.prompts[0]


# ---------- findings 4 and 5: explanation safety ----------
@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Confirm at http://pay-me.example now", "link"),
        ("Email us at support@example.com", "link"),
        ("Nice <script>x()</script>", "link"),
        ("Because your reliability is high.", "private"),
        ("Since your risk is low.", "private"),
        ("عشان سجلك كويس", "private"),
        ("ابعت الباسورد عشان نأكد", "instruction"),
    ],
)
def test_llm_sentence_blocks_links_markup_private_words_and_instructions(
    text: str, problem: str
) -> None:
    reason = prose_problem(text, ["Omar"])
    assert reason is not None
    assert problem in reason


@pytest.mark.parametrize("reply", [["not", "a", "dict"], "text", None, 42])
def test_non_object_llm_reply_falls_back_to_templates(reply: Any) -> None:
    proposal, members = _two_members()
    result = asyncio.run(explain_order_detailed(proposal, members, FakeLLM(reply)))
    assert set(result.sources.values()) == {"template"}
    assert len(result.texts) == 2


# ---------- finding 9: clear errors ----------
def test_empty_member_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="no members"):
        propose_payout_order([], [], seed=1)


def test_proposal_with_unknown_member_is_rejected() -> None:
    _, members = _two_members()
    stray = OrderProposal(
        assignments=[
            SlotAssignment(member_id="ghost", slot_index=1, payout_date=date(2026, 11, 1), cost=0.0)
        ],
        seed=1,
    )
    with pytest.raises(ValueError, match="ghost"):
        build_facts(stray, members)


# ---------- fuzz: member text can never crash the parsers ----------
@settings(max_examples=300, deadline=None)
@given(text=st.text(max_size=300))
def test_rules_never_raise_on_arbitrary_text(text: str) -> None:
    need = extract_need_rules(text, CIRCLE)
    if need.target_date is not None:
        assert need.target_date >= CIRCLE.start_date
    if need.amount_needed is not None:
        assert Decimal(0) < need.amount_needed <= MAX_NEED_AMOUNT


@settings(max_examples=300, deadline=None)
@given(text=st.text(max_size=500))
def test_prose_guard_never_raises_on_arbitrary_text(text: str) -> None:
    prose_problem(text, ["Omar", "Sara"])


# ---------- review round 2: negation, stray years, leap days ----------
@pytest.mark.parametrize(
    "text",
    [
        "this is not an emergency, wedding in June",
        "it isn't urgent, wedding in June",
        "I don't have to get it in June",
        "مش طوارئ، فرح في يونيو",
    ],
)
def test_negated_urgency_is_not_a_boost(text: str) -> None:
    assert extract_need_rules(text, CIRCLE).urgency <= 3


def test_real_urgency_still_counts() -> None:
    assert extract_need_rules("emergency, need it now", CIRCLE).urgency == 5


def test_a_year_elsewhere_in_the_text_does_not_move_the_date() -> None:
    need = extract_need_rules("wedding in March, been saving since 2024", CIRCLE)
    assert need.target_date == date(2027, 3, 1)
    assert extract_need_rules("wedding on March 15, 2027", CIRCLE).target_date == date(2027, 3, 15)


def test_leap_day_rolls_to_the_next_leap_year() -> None:
    circle = CIRCLE.model_copy(update={"start_date": date(2027, 3, 1)})
    assert extract_need_rules("29 February", circle).target_date == date(2028, 2, 29)


@pytest.mark.parametrize("amount", ["1e30", "-100", "0", "NaN", "Infinity"])
def test_contract_rejects_bad_contribution_amounts_as_validation_errors(
    amount: str,
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CircleSnapshot.model_validate({**CIRCLE.model_dump(), "contribution_amount": amount})


# ---------- review round 3 (re-verification follow-ups) ----------
@pytest.mark.parametrize(
    "sentence",
    [
        "With ten runs and no reserve, nearly all circles paid.",
        "Half of the circles paid on time.",
        "خمسه من الجمعيات قبضت.",
        "Your slot is on hold until the organiser receives your account details.",
        "Please share your account details with the organiser.",
        "Your slot was cancelled, ask the organiser for a refund.",
        "اتلغى دورك، اسأل المنظم",
    ],
)
def test_number_words_and_social_engineering_are_rejected(sentence: str) -> None:
    assert prose_problem(sentence, ["Sara"]) is not None


@pytest.mark.parametrize(
    "sentence",
    [
        "Your school costs come first, so the circle put you early.",
        "ميعادك قريب فاتقدمت.",
    ],
)
def test_ordinary_why_sentences_still_pass(sentence: str) -> None:
    assert prose_problem(sentence, ["Sara"]) is None


def test_sub_cent_contribution_is_rejected_before_rounding() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CircleSnapshot.model_validate({**CIRCLE.model_dump(), "contribution_amount": "0.001"})
    ok = CircleSnapshot.model_validate({**CIRCLE.model_dump(), "contribution_amount": "0.01"})
    assert ok.contribution_amount == Decimal("0.01")


@pytest.mark.parametrize(
    ("text", "flexibility"),
    [
        ("it's urgent, not flexible", "low"),
        ("the date is inflexible, wedding in June", "low"),
        ("مش مرن، فرح في يونيو", "low"),
        ("wedding in June, I'm flexible", "high"),
    ],
)
def test_negated_flexibility(text: str, flexibility: str) -> None:
    assert extract_need_rules(text, CIRCLE).flexibility == flexibility
