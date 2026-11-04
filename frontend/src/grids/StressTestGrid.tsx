// Stress-test grid: one row per reserve level, recommended reserve highlighted (issue #5).
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import type { ColDef, RowClassRules } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";
import type { ReservePoint, StressTestResult } from "../types/contracts";
import { formatPercentString, formatProbability, formatUsd, sameDecimal } from "../lib/format";

const simsFormat = new Intl.NumberFormat("en-US");

export function StressTestGrid({ result }: { result: StressTestResult }) {
  const recommended = result.recommended_reserve_pct;

  const rows = useMemo(
    () => [...result.points].sort((a, b) => Number(a.reserve_pct) - Number(b.reserve_pct)),
    [result.points],
  );

  const columns = useMemo<ColDef<ReservePoint>[]>(
    () => [
      {
        colId: "reserve_pct",
        headerName: "Upfront reserve (% of pot)",
        valueGetter: (p) => (p.data ? Number(p.data.reserve_pct) : undefined),
        valueFormatter: (p) => (p.data ? formatPercentString(p.data.reserve_pct) : ""),
        cellClass: "num",
        flex: 1,
        minWidth: 180,
      },
      {
        colId: "completion_probability",
        headerName: "Chance every pot is paid",
        valueGetter: (p) => p.data?.completion_probability,
        valueFormatter: (p) => (typeof p.value === "number" ? formatProbability(p.value) : ""),
        cellClass: "num",
        flex: 1,
        minWidth: 180,
      },
      {
        colId: "expected_shortfall",
        headerName: "Expected shortfall",
        valueGetter: (p) => (p.data ? Number(p.data.expected_shortfall) : undefined),
        valueFormatter: (p) => (p.data ? formatUsd(p.data.expected_shortfall) : ""),
        cellClass: "num",
        flex: 1,
        minWidth: 160,
      },
      {
        colId: "recommended",
        headerName: "Recommendation",
        // Text as well as colour, so the pick is clear without seeing the highlight.
        valueGetter: (p) =>
          p.data && sameDecimal(p.data.reserve_pct, recommended) ? "Recommended" : "",
        sortable: false,
        flex: 1,
        minWidth: 150,
      },
    ],
    [recommended],
  );

  const rowClassRules = useMemo<RowClassRules<ReservePoint>>(
    () => ({
      "row--recommended": (p) => !!p.data && sameDecimal(p.data.reserve_pct, recommended),
    }),
    [recommended],
  );

  return (
    <div className="stack">
      {/* Plain text from the engine; React escapes it. */}
      {result.explanation ? (
        <p className="explanation" dir="auto" data-testid="stress-explanation">
          {result.explanation}
        </p>
      ) : null}
      <p className="muted small">
        Recommended upfront reserve: {formatPercentString(recommended)} of the pot. Based on{" "}
        {simsFormat.format(result.n_sims)} simulated circles.
      </p>
      <div className="ag-theme-quartz grid grid--fit">
        <AgGridReact<ReservePoint>
          rowData={rows}
          columnDefs={columns}
          rowClassRules={rowClassRules}
          domLayout="autoHeight"
          suppressColumnVirtualisation
        />
      </div>
    </div>
  );
}
