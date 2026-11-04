"""Test doubles for the Leash service: a dict-backed facts provider and a recording engine."""

from typing import Any

from app.leash.facts import BaseFacts
from app.schemas.contracts import GuardAction
from sqlalchemy.orm import Session


class FakeFactsProvider:
    def __init__(self, facts: dict[Any, tuple[str, BaseFacts]]) -> None:
        # Keys are subject ids; an (action, subject_id) key takes precedence over a bare one.
        self.facts = facts

    def build(self, db: Session, action: GuardAction, subject_id: str) -> tuple[str, BaseFacts]:
        subject_type, base = self.facts.get((action, subject_id)) or self.facts[subject_id]
        # Hand out a copy so the service mutating approved_rule_ids never leaks into the fixture.
        return subject_type, base.model_copy(deep=True)


class FakeEngine:
    def __init__(self, raise_on_resume: Exception | None = None) -> None:
        self.raise_on_resume = raise_on_resume
        self.resumed: list[tuple[GuardAction, str, str]] = []
        self.cancelled: list[tuple[GuardAction, str, str]] = []

    def resume(
        self, db: Session, action: GuardAction, subject_id: str, approved_decision_id: str
    ) -> dict[str, Any]:
        if self.raise_on_resume is not None:
            raise self.raise_on_resume
        self.resumed.append((action, subject_id, approved_decision_id))
        return {"status": "resumed", "subject_id": subject_id}

    def cancel(self, db: Session, action: GuardAction, subject_id: str, decision_id: str) -> None:
        self.cancelled.append((action, subject_id, decision_id))
