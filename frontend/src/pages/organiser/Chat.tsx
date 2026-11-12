// Organiser chat with the Circle Agent. The agent fills the circle card from plain
// language; only the Confirm button calls POST /circles, which creates the real
// PayPal product and billing plan.
import { type FormEvent, useState } from "react";
import { chatWithCircleAgent, createCircle } from "../../api/client";
import { CircleSpecCard } from "../../components/CircleSpecCard";
import type { ChatMessage, CircleCreateResponse, CircleSpec } from "../../types/contracts";

export function Chat() {
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [spec, setSpec] = useState<CircleSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CircleCreateResponse | null>(null);

  async function send(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    const next: ChatMessage[] = [...history, { role: "user", content: text, created_at: null }];
    setHistory(next);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const res = await chatWithCircleAgent(next, sessionId);
      setSessionId(res.session_id);
      setHistory([...next, { role: "assistant", content: res.turn.reply, created_at: null }]);
      setSpec(res.turn.needs_confirmation ? res.turn.spec : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(card: CircleSpec, reservePct: string) {
    setBusy(true);
    setError(null);
    try {
      setCreated(await createCircle(card, reservePct));
      setSpec(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="chat-title">
      <h2 id="chat-title">Start a circle with the Circle Agent</h2>
      <p className="muted">
        Describe your circle in plain words, in English or Arabic. The agent fills in the card;
        nothing is created on PayPal until you confirm it.
      </p>
      <ol className="chat" aria-live="polite">
        {history.map((m, i) => (
          <li key={i} className={`chat__msg chat__msg--${m.role}`} dir="auto">
            {m.content}
          </li>
        ))}
      </ol>
      {spec ? <CircleSpecCard spec={spec} busy={busy} onConfirm={confirm} /> : null}
      {created ? (
        <p className="notice" role="status" data-testid="created">
          Created on PayPal: product <span className="mono">{created.paypal.product_id}</span>,
          billing plan <span className="mono">{created.paypal.plan_id}</span>.
        </p>
      ) : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <form className="chat__form" onSubmit={send}>
        <label className="visually-hidden" htmlFor="chat-input">
          Message to the Circle Agent
        </label>
        <input
          id="chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. 6 friends, $100 a month, starting 1 December"
          dir="auto"
          maxLength={4000}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
