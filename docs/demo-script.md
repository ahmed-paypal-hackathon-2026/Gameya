# Demo script — target runtime 2:50 (170 s)

Recording owner: Person 5 (Sat 31 Oct, 15:00–18:00 Cairo). Public YouTube link
required, no copyrighted music, real sandbox IDs visible on screen. Rehearse twice
before recording (TST-20 style clean run twice in a row).

| Time | Visual on screen | Voiceover |
| --- | --- | --- |
| 0:00–0:15 | WhatsApp-style group chat full of "who hasn't paid?" messages. | "Hundreds of millions of people save in circles like this — on WhatsApp and trust. One late payment and everyone suffers." |
| 0:15–0:40 | Organiser chats with Circle Agent → confirm card → PayPal plan ID. | "Our Circle Agent turns one sentence into a real PayPal product and billing plan — only after the organiser confirms." |
| 0:40–0:55 | Stress-Test grid: completion odds by reserve %. | "Before launch, a Monte Carlo stress-test shows a reliable circle completing 82% of runs at zero reserve — and 97% with a small 3% deposit. The engine recommends 3%." |
| 0:55–1:25 | Members join via PayPal subscription; needs typed in English and Arabic; fair-order cards. | "Members describe what they need. AI understands, a deterministic solver decides the fairest order, and AI explains it to each member." |
| 1:25–1:50 | Cycle 1: webhooks fill the pot → Payout appears in Amina's sandbox account. | "Members join with real subscriptions; contributions arrive through verified webhooks; when the ledger balances, code — never the AI — sends one idempotent PayPal Payout. The organiser takes cycle 1 on purpose: she put up the circle and takes the last-mile integrity risk on camera." |
| 1:50–2:20 | Omar flagged → nudge → PayPal invoice → Mediation options → paid. | "Our reliability model flags risk early; Smart Chase sends a friendly nudge, then a PayPal invoice, and mediation offers options a human approves." |
| 2:20–2:35 | Copilot answers in Arabic; swap approved. | "Members ask the Copilot anything in Egyptian Arabic, and can swap slots safely." |
| 2:35–2:50 | AG Grid audit log, guardrails slide, GitHub URL. | "Every AI decision is audited. AI advises, code moves money, humans approve. This is Gam'eya." |

Fallbacks on recording day (from the plan's contingency register):

- C3 — LLM quota exhausted: `DEMO_MODE=1` replays recorded golden responses
  (real model name logged); deterministic templates for prose.
- C4 — webhook delays: synchronous responses drive the UI; webhooks converge idempotently.
- If Stress-Test or Swap-Broker is red after Sat 14:00 → feature-flag them off and
  adjust the script (no dead air, no half-built screens).
