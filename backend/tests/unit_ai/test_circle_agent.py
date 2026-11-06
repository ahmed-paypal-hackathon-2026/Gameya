from __future__ import annotations

from datetime import date

import pytest
from app.ai.circle_agent import CircleSpecDraft, extract_draft_regex, missing_fields
from app.schemas.contracts import ChatMessage, ChatRole

TODAY = date(2026, 10, 28)


def _user(*texts: str) -> list[ChatMessage]:
    return [ChatMessage(role=ChatRole.USER, content=t) for t in texts]


def test_regex_golden_one() -> None:
    draft = extract_draft_regex(_user("6 friends, $100 a month, starting 1 November"), TODAY)
    assert draft.member_count == 6
    assert draft.contribution_amount == "100"
    assert draft.interval == "MONTH"
    assert draft.start_date == "2026-11-01"
    assert draft.name == "6-member circle"
    assert missing_fields(draft) == []


def test_regex_partial_lists_missing_in_order() -> None:
    draft = extract_draft_regex(_user("we are 5 people"), TODAY)
    assert draft.member_count == 5
    assert missing_fields(draft) == ["contribution_amount", "interval", "start_date"]


def test_regex_latest_user_message_wins() -> None:
    draft = extract_draft_regex(
        _user("6 friends $100 monthly from 2026-12-01", "actually make it 8 friends"), TODAY
    )
    assert draft.member_count == 8
    assert draft.start_date == "2026-12-01"
    assert draft.name == "8-member circle"


def test_regex_weekly_and_iso_date() -> None:
    draft = extract_draft_regex(_user("$50 weekly, 4 members, starting 2026-11-05"), TODAY)
    assert (draft.interval, draft.member_count, draft.contribution_amount) == ("WEEK", 4, "50")
    assert draft.start_date == "2026-11-05"


def test_regex_ignores_assistant_messages() -> None:
    history = [
        ChatMessage(role=ChatRole.ASSISTANT, content="How about 9 friends at $5 weekly?"),
        *_user("we are 5 people"),
    ]
    draft = extract_draft_regex(history, TODAY)
    assert draft.member_count == 5
    assert draft.contribution_amount is None


def test_day_month_in_the_past_rolls_to_next_year() -> None:
    draft = extract_draft_regex(_user("1 October please"), TODAY)
    assert draft.start_date == "2027-10-01"
    same_day = extract_draft_regex(_user("on 28 October"), TODAY)
    assert same_day.start_date == "2026-10-28"


def test_missing_fields_empty_draft() -> None:
    assert missing_fields(CircleSpecDraft()) == [
        "member_count",
        "contribution_amount",
        "interval",
        "start_date",
    ]


@pytest.mark.parametrize("bad", ["31 February", "no date at all"])
def test_unparseable_date_is_left_missing(bad: str) -> None:
    assert extract_draft_regex(_user(bad), TODAY).start_date is None
