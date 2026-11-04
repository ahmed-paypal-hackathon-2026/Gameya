# Production considerations

Answers for judges asking what a real deployment would need (RSK-10 / OQ-06:
"do you hold money?" and friends).

## Holding the pot

In the hackathon demo nothing accumulates: members pay the platform's sandbox
account under a real PayPal plan, and the engine pays it out within the cycle.
In production, holding contributed funds changes the regulatory picture:

- We would either operate with a **licensed money-transmission partner** or use
  PayPal's multiparty flows (payable items / platform payouts with facilitator
  commitments) so the platform never sits on customer funds.
- Circle rules, fees and reserves would be disclosed in-product before join.

## What we deliberately did not build in the demo

- **No KYC / AML**: sandbox personas stand in for verified users.
- **No credit reporting**: the reliability score is an in-app payment-reliability
  estimate from circle behaviour only — no demographic features, never shared
  with bureaus, never called a credit score (REQ-AI-04 / RSK-09).
- **Real money**: all movement is sandbox-only. The startup guard refuses any
  non-sandbox PayPal base URL.
- **Multi-currency**: USD only (EGP is not a PayPal-issued currency).
- **Native mobile / WhatsApp bot**: out of MVP scope.

## Security posture

- Money truth only from verified webhooks (signature-verified, idempotent by
  `paypal_event_id`) and synchronous PayPal responses; browser and LLM output are
  never trusted for money states.
- Payouts unreachable from any LLM path (static import check G3); every AI
  decision is written to the `agent_decisions` audit table.
- Deterministic `sender_batch_id` (`gameya-{circle_id}-{cycle_index}`) and
  `PayPal-Request-Id` on every post make payouts idempotent against retries.
- Secrets live only in the environment; `.env` is git-ignored and CI enforces a
  gitleaks scan of full history.
