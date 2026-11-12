// Organiser-only ledger: every contribution with its real PayPal reference, a pinned
// totals row, and the cycle payouts with their PayPal batch ids.
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import type { LedgerResponse, LedgerRow, MemberId } from "../types/contracts";
import { formatUsd } from "../lib/format";

type GridRow = Pick<LedgerRow, "row_id" | "member_name" | "amount" | "paypal_reference"> & {
  cycle: string;
  status: string;
  source: string;
};

const STATUS_TEXT: Record<string, string> = {
  pending: "Pending",
  invoiced: "Invoiced",
  paid: "Paid",
  covered: "Covered by reserve",
  failed: "Failed",
};

const columns: ColDef<GridRow>[] = [
  { field: "cycle", headerName: "Cycle", width: 90 },
  { field: "member_name", headerName: "Member", minWidth: 120, flex: 1 },
  {
    field: "amount",
    headerName: "Amount",
    valueFormatter: (p) => (p.value ? formatUsd(String(p.value)) : ""),
    cellClass: "num",
    width: 120,
  },
  { field: "status", headerName: "Status", width: 150 },
  { field: "source", headerName: "Via", width: 110 },
  {
    field: "paypal_reference",
    headerName: "PayPal reference",
    minWidth: 200,
    flex: 1,
    cellClass: "mono",
  },
];

function toRows(data: LedgerResponse): GridRow[] {
  return data.rows.map((r) => ({
    row_id: r.row_id,
    cycle: String(r.cycle_index + 1),
    member_name: r.member_name,
    amount: r.amount,
    status: STATUS_TEXT[r.status] ?? r.status,
    source: r.source,
    paypal_reference: r.paypal_reference,
  }));
}

export function LedgerGrid({
  data,
  names,
}: {
  data: LedgerResponse;
  names: Partial<Record<MemberId, string>>;
}) {
  const totals: GridRow[] = [
    {
      row_id: "totals",
      cycle: "Total",
      member_name: "Collected",
      amount: data.contributions_total,
      status: "",
      source: "",
      paypal_reference: `Paid out ${formatUsd(data.paid_out_total)}`,
    },
  ];
  return (
    <>
      {data.rows.length === 0 ? (
        <p className="muted">No contributions yet. Press Collect to send this cycle's invoices.</p>
      ) : null}
      <div className="ag-theme-quartz grid" data-testid="ledger-grid">
        <AgGridReact<GridRow>
          rowData={toRows(data)}
          columnDefs={columns}
          pinnedBottomRowData={totals}
          getRowId={(p) => p.data.row_id}
          domLayout="autoHeight"
          suppressColumnVirtualisation
        />
      </div>
      <h3>Payouts</h3>
      {data.payouts.length === 0 ? (
        <p className="muted">No payouts yet.</p>
      ) : (
        <ul className="payout-list">
          {data.payouts.map((p) => (
            <li key={p.payout_id}>
              Cycle {p.cycle_index + 1}: {formatUsd(p.amount)} to{" "}
              <span dir="auto">{names[p.recipient_id] ?? p.recipient_id}</span>, PayPal batch{" "}
              <span className="mono">{p.batch_id}</span> ({p.status.replace("_", " ")})
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
