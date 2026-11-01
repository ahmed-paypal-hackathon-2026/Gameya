// Mock-mode needs for the order-approval card.
//
// shared/fixtures only has one extracted need (Omar, circle-needs.json), so the rest
// are written here to match the stories in circle-order-propose.json. Replace this
// file with a shared fixture once Person 5 adds one for the needs list.
import omarNeed from "../../../shared/fixtures/json/circle-needs.json";
import type { NeedExtraction } from "../types/contracts";

export const MOCK_NEEDS: NeedExtraction[] = [
  omarNeed as NeedExtraction,
  {
    member_id: "mem-sara",
    category: "HOUSING",
    target_date: null,
    amount_needed: null,
    urgency: 3,
    flexibility: "high",
    confidence: 0.88,
    raw_month_hint: null,
  },
  {
    member_id: "mem-lina",
    category: "WEDDING",
    target_date: "2027-03-01",
    amount_needed: null,
    urgency: 4,
    flexibility: "low",
    confidence: 0.91,
    raw_month_hint: "March",
  },
  {
    member_id: "mem-youssef",
    category: "BUSINESS",
    target_date: null,
    amount_needed: null,
    urgency: 2,
    flexibility: "high",
    confidence: 0.8,
    raw_month_hint: null,
  },
  // Amina and Karim declared no need.
];
