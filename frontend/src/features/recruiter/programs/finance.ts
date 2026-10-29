import type { FundingType } from "../../../types/program";

/**
 * Preview of the server's fee split (backend app/services/program_finance.py)
 * so HR sees the effect of a proposal before submitting it. Same rules:
 * 2-decimal money, ROUND_HALF_UP, candidate payable = fee − company
 * contribution. Integer cents (BigInt) avoid floating-point drift. The
 * server recomputes and stores the authoritative figures.
 */

export interface FeeSplit {
  fee: string;
  companyContribution: string;
  candidatePayable: string;
}

const DECIMAL = /^\d+(\.\d{1,2})?$/;

/** "1234.5" -> 123450n (cents). null when not a non-negative 2-dp number. */
export function toCents(value: string): bigint | null {
  const trimmed = value.trim();
  if (!DECIMAL.test(trimmed)) return null;
  const [whole, fraction = ""] = trimmed.split(".");
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
}

export function fromCents(cents: bigint): string {
  const sign = cents < 0n ? "-" : "";
  const abs = cents < 0n ? -cents : cents;
  return `${sign}${abs / 100n}.${(abs % 100n).toString().padStart(2, "0")}`;
}

/** Returns the split, or an error message the form can show. */
export function splitFee(fee: string, type: FundingType, value: string): FeeSplit | string {
  const feeCents = toCents(fee);
  if (feeCents === null) return "The training fee isn't set.";
  const valueCents = toCents(value);
  if (valueCents === null || valueCents === 0n) return "Enter an amount or percentage greater than zero.";
  let contribution: bigint;
  if (type === "PERCENTAGE") {
    if (valueCents > 10000n) return "A funding percentage can't be more than 100%.";
    // fee(cents) × pct(hundredths) / 10000, rounded half up.
    contribution = (feeCents * valueCents * 2n + 10000n) / 20000n;
  } else {
    if (valueCents > feeCents) return "Company funding can't be more than the training fee.";
    contribution = valueCents;
  }
  return {
    fee: fromCents(feeCents),
    companyContribution: fromCents(contribution),
    candidatePayable: fromCents(feeCents - contribution),
  };
}

export function formatMoney(amount: string | null | undefined, currency: string | null | undefined): string {
  if (amount === null || amount === undefined || amount === "" || !currency) return "—";
  const cents = toCents(amount.replace(/^-/, ""));
  if (cents === null) return `${currency} ${amount}`;
  const [whole, fraction] = fromCents(cents).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${currency} ${amount.startsWith("-") ? "-" : ""}${grouped}.${fraction}`;
}
