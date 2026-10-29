import { useState } from "react";
import { humanizeStatus } from "../../../shared/lib/statusTone";
import type { PaymentMethod } from "../../../types/program";
import { formatMoney } from "./finance";
import { Block, Row, StateBadge, type SectionProps } from "./panelParts";
import { SELECTED } from "./presentation";

const PAYMENT_METHODS: PaymentMethod[] = ["BANK_TRANSFER", "UPI", "CHEQUE", "CASH", "OTHER"];

/** The candidate's training-fee obligation (fixed per participant, so
 * later fee changes never rewrite it) and manually recorded payments.
 * Visible only to finance users — the server sends nothing otherwise. */
export function FeeSection({ e, program, busy, can, run }: SectionProps) {
  const [dueDate, setDueDate] = useState(e.fee_due_date ?? "");
  const [payment, setPayment] = useState({
    amount: "",
    paidOn: "",
    method: "BANK_TRANSFER" as PaymentMethod,
    reference: "",
  });
  if (!e.finance_visible) return null;
  const currency = e.fee_currency;

  return (
    <Block title="Training fee and payments">
      <p className="field-hint">
        The training is paid. Payments happen outside the platform and are recorded here with the
        receipt reference that confirms them — never card or bank account details. A mistaken entry
        is voided with a reason, never deleted.
      </p>
      {e.fee_amount === null ? (
        <Row label="Obligation">Not fixed yet (fixed on training enrollment or a funding proposal).</Row>
      ) : (
        <>
          <Row label="Training fee">{formatMoney(e.fee_amount, currency)}</Row>
          <Row label="Company contribution">{formatMoney(e.company_contribution_amount ?? "0", currency)}</Row>
          <Row label="Candidate payable">{formatMoney(e.candidate_payable_amount, currency)}</Row>
          <Row label="Paid">{formatMoney(e.fee_paid_total, currency)}</Row>
          <Row label="Balance due">{formatMoney(e.balance_due, currency)}</Row>
          <Row label="Due date">{e.fee_due_date ?? "—"}</Row>
          <Row label="Payment status">{e.payment_status && <StateBadge value={e.payment_status} />}</Row>
        </>
      )}
      {can("program.finance.manage") && SELECTED.includes(e.application_status) && (
        <div className="field-row">
          <label className="field">
            <span>Payment due date <span className="optional-mark">(Optional)</span></span>
            <input type="date" value={dueDate} onChange={(ev) => setDueDate(ev.target.value)} disabled={busy} />
          </label>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy || (!program.training_fee_amount && e.fee_amount === null)}
              onClick={() => run("fee-obligation", { due_date: dueDate || null }, "Fee obligation saved.")}>
              {e.fee_amount === null ? "Fix fee obligation" : "Update due date"}
            </button>
          </div>
        </div>
      )}
      {e.fee_payments.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
          {e.fee_payments.map((p) => (
            <li key={p.id}>
              {formatMoney(p.amount, p.currency)} on {p.paid_on} via {humanizeStatus(p.method)}
              {p.reference ? ` · ref ${p.reference}` : ""}
              {p.status === "VOIDED" ? (
                <em> — voided ({p.void_reason})</em>
              ) : (
                can("program.finance.manage") && (
                  <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                    onClick={() => {
                      const reason = window.prompt("Why is this payment record being voided?");
                      if (reason && reason.trim()) run(`fee-payments/${p.id}/void`, { reason }, "Payment record voided.");
                    }}>
                    Void
                  </button>
                )
              )}
            </li>
          ))}
        </ul>
      )}
      {can("program.finance.manage") && e.fee_amount !== null && e.balance_due !== null && Number(e.balance_due) > 0 && (
        <>
          <div className="field-row">
            <label className="field">
              <span>Amount ({currency})</span>
              <input type="number" min={0} step="0.01" value={payment.amount} onChange={(ev) => setPayment({ ...payment, amount: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Paid on</span>
              <input type="date" value={payment.paidOn} onChange={(ev) => setPayment({ ...payment, paidOn: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Method</span>
              <select value={payment.method} onChange={(ev) => setPayment({ ...payment, method: ev.target.value as PaymentMethod })} disabled={busy}>
                {PAYMENT_METHODS.map((m) => <option key={m} value={m}>{humanizeStatus(m)}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Receipt / transaction reference</span>
              <input value={payment.reference} onChange={(ev) => setPayment({ ...payment, reference: ev.target.value })} disabled={busy} />
            </label>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}
            disabled={busy || !payment.amount || !payment.paidOn || !payment.reference.trim()}
            onClick={() => run("fee-payments", {
              amount: payment.amount,
              currency,
              paid_on: payment.paidOn,
              method: payment.method,
              reference: payment.reference,
            }, "Payment recorded.")}>
            Record payment
          </button>
        </>
      )}
    </Block>
  );
}
