// Display formatters. Every function returns plain text; nothing here produces markup.
import type { NeedCategory } from "../types/contracts";

const percentFormat = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

const usdFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const plainNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

const dateFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

export const NOT_AVAILABLE = "n/a";

/** A probability in [0, 1], shown as a percentage: 0.45 -> "45%". */
export function formatProbability(p: number): string {
  return Number.isFinite(p) ? percentFormat.format(p) : NOT_AVAILABLE;
}

/** A contract Money string ("108.00") shown in dollars: "$108.00". */
export function formatUsd(amount: string): string {
  const n = Number(amount);
  return amount.trim() !== "" && Number.isFinite(n) ? usdFormat.format(n) : NOT_AVAILABLE;
}

/** A contract Decimal percentage string ("3" means 3%): "3%". */
export function formatPercentString(pct: string): string {
  const n = Number(pct);
  return pct.trim() !== "" && Number.isFinite(n) ? `${plainNumber.format(n)}%` : NOT_AVAILABLE;
}

/** Two Decimal strings that may differ in trailing zeros ("3" vs "3.00"). */
export function sameDecimal(a: string, b: string): boolean {
  const x = Number(a);
  const y = Number(b);
  return a.trim() !== "" && b.trim() !== "" && Number.isFinite(x) && x === y;
}

/**
 * An ISO date ("2026-12-15", or the date part of a datetime) as "15 Dec 2026".
 * Parsed as a calendar date, so it never shifts a day with the viewer's time zone.
 */
export function formatIsoDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const d = new Date(Date.UTC(year, month - 1, day));
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) {
    return iso;
  }
  return dateFormat.format(d);
}

export type RiskDirection = "raises" | "lowers" | "neutral";

/** Sign convention from P3: a positive contribution raises the late-payment risk. */
export function riskDirection(contribution: number): RiskDirection {
  if (contribution > 0) return "raises";
  if (contribution < 0) return "lowers";
  return "neutral";
}

/** A signed factor contribution: "+0.62", "\u22120.20", "0.00". */
export function formatContribution(contribution: number): string {
  if (!Number.isFinite(contribution)) return NOT_AVAILABLE;
  const abs = Math.abs(contribution).toFixed(2);
  if (abs === "0.00") return "0.00";
  return contribution > 0 ? `+${abs}` : `\u2212${abs}`;
}

export function formatCost(cost: number): string {
  return Number.isFinite(cost) ? cost.toFixed(2) : NOT_AVAILABLE;
}

export const NEED_CATEGORY_LABEL: Record<NeedCategory, string> = {
  WEDDING: "Wedding",
  EDUCATION: "Education",
  MEDICAL: "Medical",
  HOUSING: "Housing",
  BUSINESS: "Business",
  TRAVEL: "Travel",
  OTHER: "Other",
};
