# Sandbox setup — accounts, personas & webhooks (Person 5)

Everything in this demo uses the **PayPal Sandbox** — no real money moves.

## 1. Create the app

1. Log in at <https://developer.paypal.com> → **Apps & Credentials** → Sandbox.
2. Create app **"Gam'eya Platform"** (a Business account is the platform itself).
3. Copy `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` into `.env` (never committed).
4. Enable features: **Payouts**, **Subscriptions/Vault**, **Invoicing**.
5. Confirm `PAYPAL_API_BASE_URL=https://api-m.sandbox.paypal.com` — the app refuses
   to start against any non-sandbox URL (startup guard), except `ENV=local_test`.

## 2. Fund six personal accounts (demo personas)

The golden story (see `shared/fixtures/json/`) uses six funded sandbox personal
accounts. Create them in **Sandbox accounts → Create account** and add ≥ $700
balance each (6 × $100 + headroom for catch-up fees):

Create each account with **exactly** the email below: the app invoices and pays these
addresses (`backend/app/db/seed.py`, `buyer_email`).

| Persona id    | Name    | Role      | Sandbox account email              |
| ------------- | ------- | --------- | ---------------------------------- |
| `mem-amina`   | Amina   | Organiser | `amina-buyer@gameya-sandbox.dev`   |
| `mem-omar`    | Omar    | Member    | `omar-buyer@gameya-sandbox.dev`    |
| `mem-sara`    | Sara    | Member    | `sara-buyer@gameya-sandbox.dev`    |
| `mem-youssef` | Youssef | Member    | `youssef-buyer@gameya-sandbox.dev` |
| `mem-lina`    | Lina    | Member    | `lina-buyer@gameya-sandbox.dev`    |
| `mem-karim`   | Karim   | Member    | `karim-buyer@gameya-sandbox.dev`   |

The payout order is not fixed in advance: the fair-order optimiser decides it from the
needs the members submit, and every member approves it. With the demo needs, Amina
(urgent surgery in January) gets slot 1, Sara (school fees in December) slot 2, and Omar
(high risk) is kept out of the early slots.

## 3. Register the webhook

1. Dashboard → **Webhooks** → Add webhook.
2. URL: `https://<your-deploy-host>/api/v1/webhooks/paypal`
   (local dev: a `cloudflared`/`ngrok` tunnel to the FastAPI port).
3. Subscribe to: `BILLING.SUBSCRIPTION.ACTIVATED`, `PAYMENT.SALE.COMPLETED`,
   `BILLING.SUBSCRIPTION.PAYMENT.FAILED`, `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`,
   `PAYMENT.PAYOUTS-ITEM.FAILED`, `PAYMENT.PAYOUTS-ITEM.UNCLAIMED`.
4. Copy the webhook id into `.env` as `PAYPAL_WEBHOOK_ID`.

Verification is non-negotiable: forged events must 400 and duplicate
`event_id`s must no-op (TST-06).

## 4. Reset the demo

```bash
python scripts/seed_demo.py --check      # validate fixtures, print story
python scripts/seed_demo.py --reset      # re-seed golden story (needs backend DB, DEMO_MODE)
# hosted: POST /api/v1/admin/reset-demo    (DEMO_MODE only)
```

Reset must restore the full golden story in < 30 s (TST-20 / G7).
