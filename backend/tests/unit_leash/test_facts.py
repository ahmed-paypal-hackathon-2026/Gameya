from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.leash.facts import FACTS_MODEL, ChaseInvoiceFacts, PayoutFacts
from app.schemas.contracts import GuardAction
from pydantic import ValidationError


def _payout(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "now": "2026-10-28T12:00:00Z",
        "circle_id": "c1",
        "payout_id": "p1",
        "amount": "100",
        "recipient_member_id": "m1",
        "recipient_name": "Amina",
        "recipient_payout_email": " Amina@X.com ",
        "payout_email_changed_at": None,
        "payer_emails": [" A@X.com", "b@x.com "],
        "reserve_cover_used": False,
        "slot_swapped_at": None,
        "recipient_is_organiser": False,
        "recipient_has_unpaid": False,
    }
    base.update(over)
    return base


def test_every_action_has_a_model() -> None:
    assert set(FACTS_MODEL) == set(GuardAction)


def test_naive_datetime_becomes_utc() -> None:
    f = PayoutFacts.model_validate(_payout(now=datetime(2026, 1, 1, 0, 0)))
    assert f.now.tzinfo is not None
    assert f.now == datetime(2026, 1, 1, tzinfo=UTC)


def test_naive_iso_string_becomes_utc() -> None:
    f = PayoutFacts.model_validate(_payout(payout_email_changed_at="2026-10-27T12:00:00"))
    assert f.payout_email_changed_at == datetime(2026, 10, 27, 12, tzinfo=UTC)


def test_money_quantised_half_up() -> None:
    assert PayoutFacts.model_validate(_payout(amount="10.005")).amount == Decimal("10.01")
    assert PayoutFacts.model_validate(_payout(amount=7)).amount == Decimal("7.00")


def test_float_money_rejected() -> None:
    with pytest.raises(ValidationError):
        PayoutFacts.model_validate(_payout(amount=1.5))


def test_emails_normalised() -> None:
    f = PayoutFacts.model_validate(_payout())
    assert f.recipient_payout_email == "amina@x.com"
    assert f.payer_emails == ["a@x.com", "b@x.com"]


def test_chase_money_fields_quantised() -> None:
    f = ChaseInvoiceFacts.model_validate(
        {
            "now": "2026-10-28T12:00:00Z",
            "circle_id": "c1",
            "member_id": "m",
            "member_name": "M",
            "invoice_amount": "100",
            "unpaid_amount": "100.004",
            "due_at": "2026-10-20T00:00:00Z",
            "open_invoice_count": 0,
            "last_invoice_sent_at": None,
            "no_auto_chase": False,
        }
    )
    assert f.invoice_amount == f.unpaid_amount == Decimal("100.00")
