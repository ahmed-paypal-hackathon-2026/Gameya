// What every member (the organiser too) does: pay their own PayPal invoice, tell the
// circle what they need the pot for, and approve the payout order.
// Shown to members: imports nothing that can load or show reliability data.
import { type FormEvent, useState } from "react";
import { approveOrder, getMyInvoices, submitNeed } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import type { Locale, Viewer } from "../types/contracts";
import { formatUsd, NEED_CATEGORY_LABEL } from "../lib/format";
import { AsyncBlock } from "./AsyncBlock";

export function MyInvoicesCard({ viewer }: { viewer: Viewer }) {
  const state = useAsync(() => getMyInvoices(viewer.circle_id), [viewer.viewer_id, viewer.circle_id]);
  return (
    <section className="card" aria-labelledby="pay-title">
      <h2 id="pay-title">Your contributions</h2>
      <AsyncBlock state={state} loadingText="Loading your invoices...">
        {(invoices) =>
          invoices.length === 0 ? (
            <p className="muted">No invoice yet. The organiser sends them when a cycle opens.</p>
          ) : (
            <ul className="invoice-list">
              {invoices.map((inv) => (
                <li key={inv.paypal_invoice_id}>
                  Cycle {inv.cycle_index + 1}: {formatUsd(inv.amount)}{" "}
                  {inv.paid ? (
                    <span className="tag tag--ok">Paid</span>
                  ) : (
                    <a
                      className="paypal-button"
                      href={inv.pay_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Pay with PayPal
                    </a>
                  )}
                  <span className="muted mono"> {inv.paypal_invoice_id}</span>
                </li>
              ))}
            </ul>
          )
        }
      </AsyncBlock>
    </section>
  );
}

export function NeedForm({ viewer }: { viewer: Viewer }) {
  const [text, setText] = useState("");
  const [locale, setLocale] = useState<Locale>("en");
  const [result, setResult] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const need = await submitNeed(viewer.circle_id, viewer.viewer_id, text.trim(), locale);
      const category = NEED_CATEGORY_LABEL[need.category] ?? need.category;
      setResult(
        need.target_date
          ? `Understood: ${category}, needed by ${need.target_date}.`
          : `Understood: ${category}, no date given.`,
      );
      setFailed(false);
    } catch (err) {
      setResult(err instanceof Error ? err.message : String(err));
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="need-title">
      <h2 id="need-title">What do you need the pot for?</h2>
      <p className="muted">
        Write it your way, in English or Arabic. The fair order uses it to choose your slot.
      </p>
      <form className="need-form" onSubmit={submit}>
        <label className="visually-hidden" htmlFor="need-text">
          Your need
        </label>
        <textarea
          id="need-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={1000}
          rows={2}
          dir="auto"
          placeholder="e.g. School fees in December, about $600"
        />
        <label htmlFor="need-locale">Language</label>
        <select
          id="need-locale"
          value={locale}
          onChange={(e) => setLocale(e.target.value as Locale)}
        >
          <option value="en">English</option>
          <option value="ar-EG">العربية (مصر)</option>
        </select>
        <button type="submit" disabled={busy || !text.trim()}>
          {busy ? "Reading..." : "Submit need"}
        </button>
      </form>
      {result ? (
        <p className={failed ? "error" : "notice"} role={failed ? "alert" : "status"} dir="auto">
          {result}
        </p>
      ) : null}
    </section>
  );
}

export function ApproveOrderButton({ viewer }: { viewer: Viewer }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function approve() {
    setState("busy");
    try {
      await approveOrder(viewer.circle_id, viewer.viewer_id);
      setState("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState("error");
    }
  }

  return (
    <div className="actions">
      <button
        type="button"
        className="primary"
        disabled={state === "busy" || state === "done"}
        onClick={approve}
      >
        {state === "done" ? "You approved this order" : "Approve this order"}
      </button>
      {state === "error" ? (
        <span className="error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
