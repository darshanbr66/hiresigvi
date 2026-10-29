import { describe, expect, it } from "vitest";
import { formatMoney, splitFee, toCents } from "./finance";

describe("splitFee", () => {
  it("splits a percentage of the fee (illustrative values, not policy)", () => {
    expect(splitFee("100000", "PERCENTAGE", "40")).toEqual({
      fee: "100000.00",
      companyContribution: "40000.00",
      candidatePayable: "60000.00",
    });
  });

  it("rounds half up exactly like the server and the parts add up to the fee", () => {
    expect(splitFee("1000.01", "PERCENTAGE", "33.33")).toEqual({
      fee: "1000.01",
      companyContribution: "333.30",
      candidatePayable: "666.71",
    });
    expect(splitFee("0.05", "PERCENTAGE", "50")).toMatchObject({ companyContribution: "0.03" });
  });

  it("handles a fixed amount and full funding", () => {
    expect(splitFee("75000.50", "FIXED_AMOUNT", "25000")).toMatchObject({ candidatePayable: "50000.50" });
    expect(splitFee("1000", "PERCENTAGE", "100")).toMatchObject({ candidatePayable: "0.00" });
  });

  it("refuses invalid values", () => {
    expect(typeof splitFee("1000", "PERCENTAGE", "100.01")).toBe("string");
    expect(typeof splitFee("1000", "FIXED_AMOUNT", "1000.01")).toBe("string");
    expect(typeof splitFee("1000", "PERCENTAGE", "0")).toBe("string");
    expect(typeof splitFee("", "PERCENTAGE", "10")).toBe("string");
    expect(typeof splitFee("1000", "PERCENTAGE", "-5")).toBe("string");
    expect(toCents("1.234")).toBeNull();
  });
});

describe("formatMoney", () => {
  it("groups thousands and keeps two decimals", () => {
    expect(formatMoney("1234567.5", "INR")).toBe("INR 1,234,567.50");
    expect(formatMoney(null, "INR")).toBe("—");
    expect(formatMoney("10", null)).toBe("—");
  });
});
