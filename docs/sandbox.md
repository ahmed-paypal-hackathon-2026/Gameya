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

| Persona id    | Name   | Role      | Story note |
| ------------- | ------ | --------- | ---------- |
| `mem-amina`   | Amina  | Organiser | Takes the last slot 6 on purpose |
| `mem-omar`    | Omar   | Member    | High-risk; school fees December; late in cycle 3 |
| `mem-sara`    | Sara   | Member    | Receives the cycle-1 pot on 1 Nov 2026 |
| `mem-youssef` | Youssef| Member    | Asks the Copilot in Arabic; swaps with Karim |
| `mem-lina`    | Lina   | Member    | Wedding in March; receives pot early (slot 3) |
| `mem-karim`   | Karim  | Member    | Flexible; approves swap |

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
