# AGENTS.md — Gam'eya working rules

Guidance for any AI assistant (OpenCode, Claude Code, Cursor, Copilot) working in this
repository, and for teammates. Aligned with the PayPal AI Hackathon 2026 engineering plan
(code-complete Fri 30 Oct 22:00 Cairo; video + submission Sat 31 Oct 22:00 Cairo).

## Golden rule

"AI understands and advises. Deterministic code moves the money. Humans approve anything
that changes who gets paid."

No LLM code may reach the Payouts API. Payouts live in `backend/app/domain/circle_engine.py`
(Person 1) and are triggered only after ledger invariants pass, with deterministic
`sender_batch_id` and `PayPal-Request-Id` on every POST.

## Lane ownership (enforced by CODEOWNERS)

| Lane | Owner | Write scope |
| --- | --- | --- |
| P1 Backend & PayPal | Person 1 | `backend/app/{main,config}.py`, `db/`, `api/`, `domain/`, `services/paypal/`, `requirements.txt`, `tests/unit_paypal/` |
| P2 AI agents | Person 2 | `backend/app/ai/{llm,guardrails,toolkit_adapter,circle_agent,smart_chase,mediation,copilot,swap_broker}.py`, `ai/prompts/` |
| P3 Fair-order & ML | Person 3 | `backend/app/ai/{needs_extractor,fair_order}.py`, `backend/app/ml/**`, `tests/unit_ml/` |
| P4 Frontend | Person 4 | `frontend/**` except `src/types/contracts.ts` |
| P5 Integration/QA/Security/Submission | Person 5 | contracts (both languages), `shared/fixtures/`, root files, `.github/`, `docs/`, `scripts/`, `tests/integration/` |

Contract files (`backend/app/schemas/contracts.py`, `frontend/src/types/contracts.ts`) are
frozen. Any change needs a PR approved by Person 5 **and** every affected consumer lane.

## Hard rules

- Money is `Decimal` quantised to 0.01 with `ROUND_HALF_UP`. Floats are banned on
  financial paths, both sides of the wire (money is serialized as strings in JSON).
- Only verified webhooks and synchronous PayPal responses change money state. The browser
  and LLM responses are never trusted for money truth.
- Secrets only in `.env` (git-ignored). Only `.env.example` is committed.
- `PAYPAL_API_BASE_URL` must contain `sandbox.paypal.com` (app refuses to start otherwise,
  except `ENV=local_test`).
- Invoicing pitfall: `/send` ignores subject/note — write AI reminder text into
  `detail.note` and `items[].description` at `create_invoice` time.
- Affects: untrusted member text is wrapped in `<untrusted_member_text>` blocks.

## Commands

```bash
# backend
conda run -n S-dev ruff check app tests      # from backend/
conda run -n S-dev ruff format --check app tests
conda run -n S-dev mypy                      # strict on schemas/, ai/, ml/
conda run -n S-dev pytest
# frontend
npx tsc --noEmit                             # from frontend/
```

CI: gitleaks → ruff → mypy → pytest → tsc, on every PR and push to `main`.

## Working agreements

- Trunk-based on protected `main`; branches `feat/p1-payouts`, `feat/p2-circle-agent`,
  `feat/p3-fair-order`, `feat/p4-ag-grid`, `chore/p5-render`.
- Conventional Commits: `feat(paypal): idempotent payouts by sender_batch_id`.
- PRs: CI green + 1 review; review SLA 2 hours. Blockers > 30 min escalate to Person 5.
- Stand-ups 09:00 and 18:00 Cairo.
- Rollback tags: `v0.1-m1`, `v0.2-m2`, `v1.0-mvp`.

## Demo data

The golden demo story (6 members, $100/month, from 1 Nov 2026) is defined once, in
`shared/fixtures/json/`. Persons 1–4 build against those fixtures; mock/live parity is
checked by `backend/tests/integration/`. Personas: Amina (organiser), Omar, Sara, Youssef,
Lina, Karim — see `docs/sandbox.md`.
