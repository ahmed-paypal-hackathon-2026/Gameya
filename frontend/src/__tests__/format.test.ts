import { describe, expect, it } from "vitest";
import { apiBase, parseUseMocks } from "../api/config";
import {
  formatContribution,
  formatIsoDate,
  formatPercentString,
  formatProbability,
  formatUsd,
  riskDirection,
  sameDecimal,
} from "../lib/format";

describe("formatters", () => {
  it("formats probabilities as percentages", () => {
    expect(formatProbability(0.45)).toBe("45%");
    expect(formatProbability(0.825)).toBe("82.5%");
    expect(formatProbability(Number.NaN)).toBe("n/a");
  });

  it("formats Money strings as dollars", () => {
    expect(formatUsd("108.00")).toBe("$108.00");
    expect(formatUsd("1234.5")).toBe("$1,234.50");
    expect(formatUsd("")).toBe("n/a");
    expect(formatUsd("abc")).toBe("n/a");
  });

  it("formats Decimal percentages", () => {
    expect(formatPercentString("3")).toBe("3%");
    expect(formatPercentString("3.50")).toBe("3.5%");
  });

  it("compares Decimal strings by value", () => {
    expect(sameDecimal("3", "3.00")).toBe(true);
    expect(sameDecimal("3", "10")).toBe(false);
    expect(sameDecimal("", "0")).toBe(false);
  });

  it("formats ISO dates without time-zone drift", () => {
    expect(formatIsoDate("2026-12-15")).toBe("15 Dec 2026");
    expect(formatIsoDate("2027-01-01")).toBe("1 Jan 2027");
    expect(formatIsoDate("2027-02-30")).toBe("2027-02-30");
    expect(formatIsoDate("soon")).toBe("soon");
  });

  it("signs contributions: positive raises risk, negative lowers it", () => {
    expect(formatContribution(0.62)).toBe("+0.62");
    expect(formatContribution(-0.2)).toBe("\u22120.20");
    expect(formatContribution(0)).toBe("0.00");
    expect(formatContribution(-0.001)).toBe("0.00");
    expect(riskDirection(0.62)).toBe("raises");
    expect(riskDirection(-0.2)).toBe("lowers");
    expect(riskDirection(0)).toBe("neutral");
  });
});

describe("env config", () => {
  it("treats 0/false as live mode and everything else as mocks", () => {
    expect(parseUseMocks(undefined)).toBe(true);
    expect(parseUseMocks("1")).toBe(true);
    expect(parseUseMocks("true")).toBe(true);
    expect(parseUseMocks("0")).toBe(false);
    expect(parseUseMocks("false")).toBe(false);
    expect(parseUseMocks(" FALSE ")).toBe(false);
  });

  it("always targets /api/v1", () => {
    expect(apiBase(undefined)).toBe("http://localhost:8000/api/v1");
    expect(apiBase("http://localhost:8000")).toBe("http://localhost:8000/api/v1");
    expect(apiBase("https://gameya-api.onrender.com/")).toBe(
      "https://gameya-api.onrender.com/api/v1",
    );
    expect(apiBase("http://x/api/v1/")).toBe("http://x/api/v1");
  });
});
