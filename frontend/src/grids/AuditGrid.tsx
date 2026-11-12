// Organiser-only audit log: one row per AI decision. Clicking a row opens its detail
// (rationale and the PayPal tool calls it made) below the grid, as plain text.
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useState } from "react";
import type { AgentDecision } from "../types/contracts";

const AGENT_TEXT: Record<string, string> = {
  circle_agent: "Circle Agent",
  smart_chase: "Smart Chase",
  mediation: "Mediation",
  copilot: "Copilot",
  swap_broker: "Swap Broker",
  needs_extractor: "Needs Extractor",
  fair_order: "Fair Order",
  stress_test: "Stress Test",
  leash: "Leash",
};

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().replace("T", " ").slice(0, 19);
}

const columns: ColDef<AgentDecision>[] = [
  { headerName: "When (UTC)", valueGetter: (p) => when(p.data?.created_at ?? null), width: 180 },
  {
    headerName: "Agent",
    valueGetter: (p) => (p.data ? (AGENT_TEXT[p.data.agent] ?? p.data.agent) : ""),
    width: 150,
  },
  { field: "model", headerName: "Model", width: 170 },
  {
    headerName: "PayPal calls",
    valueGetter: (p) => p.data?.tool_calls.map((t) => t.name).join(", ") ?? "",
    minWidth: 180,
    flex: 1,
  },
  { field: "rationale", headerName: "Why", minWidth: 240, flex: 2 },
];

export function AuditGrid({ decisions }: { decisions: AgentDecision[] }) {
  const [open, setOpen] = useState<AgentDecision | null>(null);
  return (
    <>
      <div className="ag-theme-quartz grid" data-testid="audit-grid">
        <AgGridReact<AgentDecision>
          rowData={decisions}
          columnDefs={columns}
          getRowId={(p) => p.data.decision_id}
          onRowClicked={(e) => setOpen(e.data ?? null)}
          domLayout="autoHeight"
          suppressColumnVirtualisation
        />
      </div>
      {open ? (
        <div className="detail" role="region" aria-label="Decision detail" data-testid="audit-detail">
          <h3>{AGENT_TEXT[open.agent] ?? open.agent}</h3>
          <p dir="auto">{open.rationale}</p>
          {open.tool_calls.length > 0 ? (
            <ul>
              {open.tool_calls.map((t, i) => (
                <li key={`${t.name}-${i}`}>
                  <span className="mono">{t.name}</span>
                  {t.result_summary ? ` → ${t.result_summary}` : ""}
                  {Object.keys(t.arguments).length > 0
                    ? ` (${Object.entries(t.arguments)
                        .map(([k, v]) => `${k}: ${String(v)}`)
                        .join(", ")})`
                    : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No PayPal calls.</p>
          )}
          <button type="button" onClick={() => setOpen(null)}>
            Close detail
          </button>
        </div>
      ) : null}
    </>
  );
}
