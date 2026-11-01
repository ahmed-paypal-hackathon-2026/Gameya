// Organiser-only reliability grid: p_late plus the top-3 factors (issue #5).
// Mount it only behind <OrganiserOnly>; the data call is also organiser-gated.
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import type { ColDef } from "ag-grid-community";
import { AgGridReact, type CustomCellRendererProps } from "ag-grid-react";
import type { ReliabilityFactor, ReliabilityRow } from "../types/contracts";
import { formatContribution, formatProbability, riskDirection } from "../lib/format";

const DIRECTION_TEXT = {
  raises: "raises risk",
  lowers: "lowers risk",
  neutral: "no effect",
} as const;

/** Largest absolute impact first, at most three. Does not mutate the input. */
export function topFactors(factors: readonly ReliabilityFactor[]): ReliabilityFactor[] {
  return [...factors]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .slice(0, 3);
}

function FactorsCell({ data }: CustomCellRendererProps<ReliabilityRow>) {
  const factors = data ? topFactors(data.score.top_factors) : [];
  if (factors.length === 0) return <span className="muted">No factors available</span>;
  return (
    <ul className="factor-list">
      {factors.map((f, i) => {
        const direction = riskDirection(f.contribution);
        return (
          <li key={`${f.feature}-${i}`} className={`factor factor--${direction}`}>
            <span className="factor__value">{formatContribution(f.contribution)}</span>
            {/* Labels are plain text: React escapes them, never parsed as HTML. */}
            <span className="factor__label" dir="auto">
              {f.label}
            </span>
            <span className="visually-hidden"> ({DIRECTION_TEXT[direction]})</span>
          </li>
        );
      })}
    </ul>
  );
}

const columns: ColDef<ReliabilityRow>[] = [
  {
    colId: "member",
    headerName: "Member",
    valueGetter: (p) => p.data?.display_name,
    minWidth: 140,
  },
  {
    colId: "p_late",
    headerName: "Chance of paying late",
    valueGetter: (p) => p.data?.score.p_late,
    valueFormatter: (p) => (typeof p.value === "number" ? formatProbability(p.value) : ""),
    sort: "desc",
    cellClass: "num",
    width: 220,
  },
  {
    colId: "factors",
    headerName: "Top factors",
    cellRenderer: FactorsCell,
    sortable: false,
    autoHeight: true,
    flex: 1,
    minWidth: 320,
  },
];

export function ReliabilityGrid({ rows }: { rows: ReliabilityRow[] }) {
  return (
    <div className="ag-theme-quartz grid">
      <AgGridReact<ReliabilityRow>
        rowData={rows}
        columnDefs={columns}
        getRowId={(p) => p.data.member_id}
        domLayout="autoHeight"
        suppressColumnVirtualisation
      />
    </div>
  );
}
