"""Golden-story ledger invariants (offline port of TST-05 / G2).

Validates the seeded demo dataset against the four ledger invariants from the
engineering plan Section 7.3:
  1. pot = sum of PAID/COVERED contributions = amount * N before READY
  2. exactly one payout per cycle
  3. each member receives the pot exactly once
  4. hook data still reconciles (totals, recipients, capped invoice, 3% reserve)
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.schemas import contracts as c

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "shared" / "fixtures" / "json"


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _ledger() -> c.LedgerResponse:
    return c.LedgerResponse.model_validate(_load("circle-ledger.json"))


def _circle() -> c.CircleDetail:
    return c.CircleDetail.model_validate(_load("circle-get.json"))


def test_pot_equals_paid_contributions_each_closed_cycle() -> None:
    ledger = _ledger()
    circle = _circle()
    expected_pot = circle.spec.contribution_amount * circle.spec.member_count
    closed = [cyc for cyc in circle.cycles if cyc.status is c.CycleStatus.PAID_OUT]
    assert closed, "golden story must contain closed cycles"
    for cycle in closed:
        paid = [
            row
            for row in ledger.rows
            if row.cycle_index == cycle.cycle_index
            and row.status in {c.ContributionStatus.PAID, c.ContributionStatus.COVERED}
        ]
        pot = sum((row.amount for row in paid), Decimal("0.00"))
        assert pot == expected_pot
        assert pot == cycle.pot_total


def test_exactly_one_payout_per_closed_cycle() -> None:
    ledger = _ledger()
    closed_indexes = {
        cyc.cycle_index for cyc in _circle().cycles if cyc.status is c.CycleStatus.PAID_OUT
    }
    paid_out_cycles = [p.cycle_index for p in ledger.payouts if p.status is c.PayoutStatus.PAID_OUT]
    assert sorted(paid_out_cycles) == sorted(closed_indexes)
    assert len(paid_out_cycles) == len(set(paid_out_cycles)), "duplicate payout in a cycle"
    for payout in ledger.payouts:
        assert payout.batch_id == (f"gameya-{_circle().circle_id}-{payout.cycle_index}")


def test_organiser_receives_the_first_pot() -> None:
    circle = _circle()
    assert circle.spec.organiser_id == "mem-amina"
    first_slot = circle.cycles[0]
    assert first_slot.recipient_id == "mem-amina", "slot 1 must go to Amina"


def test_each_member_receives_the_pot_exactly_once() -> None:
    circle = _circle()
    closed = c.CycleStatus.PAID_OUT
    assignments = [cyc.recipient_id for cyc in circle.cycles if cyc.status is closed]
    members = {m.member_id for m in circle.members}
    assert all(a in members for a in assignments)
    assert len(assignments) == len(set(assignments)), "member received the pot twice"


def test_ledger_totals_reconcile() -> None:
    ledger = _ledger()
    contributions = sum((row.amount for row in ledger.rows), Decimal("0.00"))
    payouts = sum((p.amount for p in ledger.payouts), Decimal("0.00"))
    assert ledger.contributions_total == contributions
    assert ledger.paid_out_total == payouts


def test_chase_invoice_never_exceeds_contribution() -> None:
    audit = c.AuditResponse.model_validate(_load("circle-audit.json"))
    circle = _circle()
    invoice_decisions = [
        d
        for d in audit.decisions
        if d.agent is c.AgentName.SMART_CHASE
        and any(t.name is c.ToolName.CREATE_INVOICE for t in d.tool_calls)
    ]
    assert invoice_decisions, "golden story must show Smart Chase invoice"
    for decision in invoice_decisions:
        for call in decision.tool_calls:
            if call.name is c.ToolName.CREATE_INVOICE:
                amount = Decimal(str(call.arguments["amount"]))
                assert c.quantize_amount(amount) <= c.quantize_amount(
                    circle.spec.contribution_amount
                )


def test_swap_only_after_two_approvals_and_on_unpaid_slots() -> None:
    swap = c.SwapProposal.model_validate(_load("circle-swaps.json"))
    assert swap.swap_request.requester_id != swap.counterpart_id
    assert swap.status is c.SwapStatus.APPLIED
    assert swap.agent_decision is not None and swap.agent_decision.human_approved
    assert swap.invariants_ok
    paid_slots = {
        cyc.cycle_index for cyc in _circle().cycles if cyc.status is c.CycleStatus.PAID_OUT
    }
    assert swap.swap_request.target_slot_index not in paid_slots
    assert swap.swap_request.requester_slot_index not in paid_slots


def test_copilot_is_engine_facts_only_and_read_only() -> None:
    answer = c.CopilotAnswer.model_validate(_load("circle-copilot.json"))
    assert answer.engine_facts_only is True
    decision = answer.agent_decision
    if decision is not None:
        assert all(call.name is not c.ToolName.PAY_ORDER for call in decision.tool_calls)


def test_stress_test_moves_completion_up_with_reserve() -> None:
    result = c.StressTestResult.model_validate(_load("circles-stress-test.json"))
    probabilities = [p.completion_probability for p in result.points]
    reserves = [p.reserve_pct for p in result.points]
    assert reserves == sorted(reserves)
    assert probabilities == sorted(probabilities)
    assert result.recommended_reserve_pct == Decimal("3")
    shortfall = [p.expected_shortfall for p in result.points]
    assert shortfall == sorted(shortfall, reverse=True)
    assert result.explanation and "97%" in result.explanation


def test_open_order_proposal_covers_every_member_uniquely() -> None:
    proposal = c.OrderProposal.model_validate(_load("circle-order-propose.json"))
    circle = _circle()
    members = {m.member_id for m in circle.members}
    slots = [a.slot_index for a in proposal.assignments]
    assert sorted(slots) == list(range(len(slots))), "every slot assigned exactly once"
    assert {a.member_id for a in proposal.assignments} == members
    first_slot = next(a for a in proposal.assignments if a.slot_index == 0)
    assert first_slot.member_id == circle.spec.organiser_id
