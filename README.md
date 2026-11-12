# Gam'eya

**AI-run rotating savings circles on PayPal.**

A group saves together and one member takes the whole pot each cycle. PayPal moves the
money, AI helps run the circle, and humans approve anything that changes who gets paid.

Built for the **PayPal AI Hackathon 2026**. Everything runs on the **PayPal Sandbox**:
no real money moves.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/api-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/react-18-61dafb.svg)](https://react.dev/)

## Live demo

| Artifact | Link |
| --- | --- |
| Hosted app (Render, HTTPS) | `https://gameya-web.onrender.com` |
| API health | `https://gameya-api.onrender.com/api/v1/health` |
| Demo video (< 3:00) | `https://youtu.be/<id>` |

The hosted services are on Render's free tier, which sleeps when idle: **the first
request can take about a minute** while the API wakes up.

## For judges

1. Open the hosted app. It shows the **golden demo circle**: 6 members, $100 a month,
   starting 1 Nov 2026.
2. Use **Viewing as** to switch between the six demo people. Each switch logs in as that
   person with a signed demo token (no passwords: these are sandbox personas).
   - **Amina** is the organiser. She sees the cycle buttons, ledger, AI audit log,
     reserve stress test, payment reliability, and the Circle Agent chat.
   - **Omar, Sara, Youssef, Lina, Karim** are members. They see their own invoices
     with a **Pay with PayPal** link, the need form, and the payout order to approve.
     Members never see anyone's reliability score.
3. A full cycle, as Amina:
   1. Each member writes what they need the pot for (English or Egyptian Arabic).
   2. The **fair order** is proposed. Every member approves it; nothing is applied
      until all six have.
   3. **Collect** sends every member a real PayPal sandbox invoice.
   4. Members pay on PayPal's own invoice page (log in with the sandbox buyer account
      below). **Sync payments** reads the invoice status back from PayPal.
   5. **Chase**: Smart Chase reminds whoever is still unpaid after the grace period.
   6. **Close cycle**: the engine checks the ledger and pays the pot once, with PayPal
      Payouts, to that cycle's approved recipient.
4. **Ledger** shows every PayPal invoice id and payout batch id. **AI audit log** lists
   every AI decision and the PayPal calls it made; click a row for the detail.

### Sandbox buyer accounts

Members pay invoices as these PayPal sandbox personal accounts (see
[docs/sandbox.md](docs/sandbox.md)):

| Member | Sandbox email |
| --- | --- |
| Amina (organiser) | `amina-buyer@gameya-sandbox.dev` |
| Omar | `omar-buyer@gameya-sandbox.dev` |
| Sara | `sara-buyer@gameya-sandbox.dev` |
| Youssef | `youssef-buyer@gameya-sandbox.dev` |
| Lina | `lina-buyer@gameya-sandbox.dev` |
| Karim | `karim-buyer@gameya-sandbox.dev` |

The sandbox passwords are given in the Devpost submission, not in this repository.

## The problem

Rotating savings circles (*gam'eya* in Egypt, *tanda* in Mexico, *susu* in West Africa,
*chit fund* in India) are used by hundreds of millions of people. Each member pays a fixed
amount every cycle and one member receives the whole pot. Most circles run on WhatsApp and
trust: there are no records, no enforcement, and when one member pays late or disappears,
everyone else pays the price. Deciding who gets the pot first is often a fight.

## The solution

Gam'eya runs the circle end to end on PayPal:

- **Set up by chat.** The organiser describes the circle in plain words. The Circle Agent
  fills in a card; on **Confirm** it creates a real PayPal catalog product and billing
  plan. The safety reserve becomes the plan's one-time setup fee.
- **A fair payout order.** Members say what they need the pot for ("school fees in
  December", "فرح في مارس"). An optimiser (the Hungarian algorithm) gives each member the
  slot that best fits their need, and keeps members with a high chance of paying late
  out of the earliest slots. The tie-break seed is derived from the circle, so nobody can
  steer it. Every member approves the order before it applies.
- **Collection with real invoices.** The engine sends each member a PayPal invoice and
  reads its status back. Smart Chase reminds late members within a strict cap: it can
  never ask for more than the contribution.
- **One payout per cycle.** The engine pays the pot with PayPal Payouts only after the
  ledger balances.
- **Explainable risk.** A reliability model estimates each member's chance of paying
  late, with the top three reasons. Only the organiser sees it. A Monte Carlo stress
  test recommends the safety reserve.

## How it works

"AI understands and advises. Deterministic code moves the money. Humans approve
anything that changes who gets paid."

| Step | Who decides | Code |
| --- | --- | --- |
| Circle card from chat | Circle Agent (Gemini, regex fallback) | `backend/app/ai/circle_agent.py` |
| Create product + plan | Organiser presses Confirm | `POST /api/v1/circles` |
| Need from free text | Needs Extractor (Gemini, rules fallback) | `backend/app/ai/needs_extractor.py` |
| Payout order | Optimiser (no LLM) + every member approves | `backend/app/ai/fair_order.py` |
| Invoices + status | Engine | `backend/app/domain/collection.py` |
| Reminders | Smart Chase, capped, allow-listed tools | `backend/app/ai/smart_chase.py` |
| Payout | Engine, after invariants | `backend/app/domain/circle_engine.py` |

Closing a cycle checks, before any HTTP call: the pot is complete (settled contributions
equal amount × members); the cycle has no payout yet; the recipient is the approved slot
owner and has never been paid in this circle; the recipient has an email. The payout row
is written first, with a deterministic `sender_batch_id` (`gameya-{circle}-{cycle}-{stamp}`,
where the stamp changes when the demo is reset) that is also the `PayPal-Request-Id`, so a
retry cannot pay twice and a reset never replays an earlier run.

Smart Chase reminders and invoices are sent when the organiser presses **Chase**; there is
no separate approval for each message. The payout order, which decides who gets paid,
applies only after every member approves it, and it is fixed once collection starts.

## Architecture

```
React + AG Grid (Vite, static)  --HTTPS + Bearer token-->  FastAPI (Docker)
                                                              |-- AI agents (app/ai): Gemini chain,
                                                              |     guarded PayPal tool allow-lists
                                                              |-- Engine (app/domain): collection,
                                                              |     invariants, payouts
                                                              |-- ML (app/ml): reliability model,
                                                              |     stress test
                                                              |-- PayPal sandbox REST (OAuth cached)
                                                              '-- Postgres (Render) / SQLite (local)
```

No module under `app/ai` may import the payout or collection code; a test enforces it.

## PayPal APIs used

| API | Used for |
| --- | --- |
| OAuth 2.0 client credentials | Cached access token, refreshed early, single-flight |
| Catalog Products v1 | One product per circle (Circle Agent) |
| Billing Plans v1 | The circle's monthly plan; reserve as `setup_fee` |
| Invoicing v2 | Create, send and read each member's contribution invoice; reminders |
| Invoice QR codes | QR for a Smart Chase invoice |
| Payouts v1 | The cycle's pot to its recipient |

Every POST carries a deterministic `PayPal-Request-Id`.

## AI models

- **Google Gemini** (free tier) with a fallback chain across models, for the Circle Agent,
  Needs Extractor, Smart Chase wording, and order/stress-test explanations.
- Every AI feature has a **deterministic fallback** (regex, rules, templates), so the demo
  works with no API key and within free-tier quotas.
- AI text is checked before use: invented numbers or dates, links and contact details
  are rejected, and member text is wrapped as untrusted input.
- **Reliability model**: logistic regression trained on simulated payment histories (no
  demographic features), shipped as JSON coefficients. See
  [docs/ml-assumptions.md](docs/ml-assumptions.md).

## Tech stack

Python 3.11, FastAPI, SQLAlchemy 2, Pydantic 2, NumPy/SciPy, httpx · React 18, Vite,
TypeScript, AG Grid Community · Docker, Render (free tier), Postgres.

## Repository layout

```
backend/app/
  api/           routes (circles, cycles, agent chat, admin, auth, webhooks)
  ai/            agents, guardrails, Gemini chain, PayPal toolkit adapter
  domain/        collection, circle engine (payouts), engine clock
  ml/            reliability model, synthetic data, stress test
  services/      PayPal OAuth, invoicing, payouts; circle data; ledger view
frontend/src/    screens, AG Grid grids, API client
shared/fixtures/ golden demo data (mock mode)
docs/            sandbox setup, demo script, ML assumptions, production notes
```

## Getting started

### Prerequisites

Python 3.11, Node 20, a PayPal developer sandbox app (Invoicing and Payouts enabled), and
optionally a Gemini API key.

### Local development

```bash
cp .env.example backend/.env          # then fill in the PayPal sandbox credentials
cd backend
pip install -r requirements.txt
DEMO_MODE=1 uvicorn app.main:app --port 8000

cd ../frontend
npm install
VITE_USE_MOCKS=0 VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

With `DEMO_MODE=1`, a fresh database gets the golden circle automatically.
`VITE_USE_MOCKS=1` runs the UI on fixtures without a backend.

### Docker

```bash
docker compose up --build             # API on :8000, web on :5173
```

## Configuration

| Variable | Meaning |
| --- | --- |
| `PAYPAL_API_BASE_URL` | Must be `https://api-m.sandbox.paypal.com` (the app refuses anything else) |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | Sandbox app credentials |
| `PAYOUTS_ENABLED` | `1` (default). `0` if Payouts is not enabled on the app: closing records a **BLOCKED** payout and sends nothing |
| `GEMINI_API_KEY` | Optional; without it every AI feature uses its fallback |
| `DEMO_MODE` | `1` enables the demo personas, seeding, and admin reset/fast-forward |
| `SESSION_SECRET`, `ADMIN_TOKEN` | Required in production (Render generates them) |
| `DATABASE_URL` | Postgres on Render, SQLite locally |

Secrets live only in `backend/.env` (git-ignored) or Render environment variables.

## API reference

Interactive docs at `/docs` on the API. Main routes (all under `/api/v1`):

| Route | Who |
| --- | --- |
| `POST /auth/demo-login` | anyone (demo personas) |
| `POST /agent/circle/chat`, `POST /circles` | logged in |
| `GET /circles/{id}` | anyone (names, slots and cycle dates; no money or risk data) |
| `GET /circles/{id}/my-invoices` | member (own invoices only) |
| `POST /circles/{id}/needs`, `GET /circles/{id}/needs` | member (own need only) |
| `POST /circles/{id}/order/propose`, `POST /circles/{id}/order/approve` | member |
| `POST /circles/{id}/stress-test`, `GET /circles/{id}/reliability` | organiser |
| `GET /circles/{id}/ledger`, `GET /circles/{id}/audit` | organiser |
| `POST /cycles/{id}/collect`, `/sync`, `/chase`, `/close` | organiser |
| `POST /admin/reset-demo`, `POST /admin/cycles/{id}/fast-forward` | `DEMO_MODE` + `X-Admin-Token` |

## Testing

```bash
cd backend && pytest                  # ~850 tests: unit, integration, property-based
cd backend && ruff check app tests && mypy
cd frontend && npx tsc --noEmit && npx vitest run
```

## What is not built yet

Honest scope for the hackathon:

- Members pay by **invoice**; the billing plan is created but members do not subscribe
  to it yet.
- The PayPal **webhook** receiver refuses events until signature verification is wired;
  payment state comes from reading invoices back from PayPal.
- **Copilot, swaps, joining a circle, pay-now (Orders) and mediation approval** still
  serve the golden demo data (marked with an `X-Demo-Fixture` response header).
- The safety reserve is shown and set as the plan's setup fee, but not yet used to
  cover a missed payment.
- No KYC, no real money, USD only. See
  [docs/production-considerations.md](docs/production-considerations.md).

## Security & financial invariants

- Money is `Decimal`, quantised to 0.01 with `ROUND_HALF_UP`, and sent as strings.
- Only PayPal's own responses change money state; browser and LLM output never do.
- Each AI agent can call only its allow-listed PayPal tools; none can reach Payouts.
- Organiser-only data (reliability, ledger, audit, stress test) is refused to members on
  the server, not just hidden in the UI.
- Sandbox-only startup guard; secrets only in the environment.

## Deployment (Render)

`render.yaml` is a Blueprint for the free tier: the API (Docker), the static web app, and
Postgres. In the Render dashboard: **New → Blueprint**, pick this repo, then set
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_API_BASE_URL` and (optionally)
`GEMINI_API_KEY`. After the first deploy, check that the service URLs Render assigned
match `CORS_ORIGINS` (API) and `VITE_API_BASE_URL` (web) in `render.yaml`; if Render added
a suffix, update both and redeploy the web app (its `VITE_*` values are fixed at build
time). Free Postgres expires after 30 days: recreate the database from the Blueprint, and
the API seeds the golden circle on the fresh database at startup. Columns added in later
versions are added to an existing database automatically at startup.

## License

Released under the [MIT License](LICENSE).

## Acknowledgements

Built for the **PayPal AI Hackathon 2026**. Thanks to the PayPal Developer team for the
Sandbox APIs and AI tooling.
