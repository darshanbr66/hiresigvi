import { useState } from "react";
import { humanizeStatus } from "../../../shared/lib/statusTone";
import { FUNDING_TYPE_LABELS, type FundingType } from "../../../types/program";
import { formatMoney, splitFee } from "./finance";
import { Block, Row, StateBadge, type SectionProps } from "./panelParts";
import { SELECTED, today } from "./presentation";

/**
 * Company funding (the scholarship) for one participant: eligibility for
 * consideration → a proposal (fixed amount or % of the fixed fee) →
 * approval by a finance approver → funding applied. Each is a separate,
 * audited server action; nothing here is automatic.
 */
export function FundingSection({ e, program, busy, can, run }: SectionProps) {
  const [proposal, setProposal] = useState({
    type: (program.company_funding_type ?? "PERCENTAGE") as FundingType,
    value: program.company_funding_value ?? "",
    reason: "",
  });
  const [decisionNote, setDecisionNote] = useState("");
  const [applied, setApplied] = useState({ amount: "", paidOn: "", reference: "" });
  const [acceptedOn, setAcceptedOn] = useState("");

  const currency = e.fee_currency ?? program.training_fee_currency;
  const fee = e.fee_amount ?? program.training_fee_amount;
  const open = e.funding_revisions.find((r) => r.status === "PROPOSED");
  const approved = e.funding_revisions.find((r) => r.status === "APPROVED");
  const canPropose =
    can("program.manage") &&
    e.scholarship_eligibility === "ELIGIBLE" &&
    !open &&
    ["PENDING", "APPROVED"].includes(e.scholarship_status);
  const preview = fee && proposal.value ? splitFee(fee, proposal.type, proposal.value) : null;
  const amendment = e.funding_terms_accepted_at !== null;

  return (
    <Block title="Company funding (scholarship)">
      <p className="field-hint">
        Passing the test or the interview never approves funding. Eligibility, the proposal and its
        approval are separate decisions; the amount follows this program's configured rules.
      </p>
      <Row label="Eligibility"><StateBadge value={e.scholarship_eligibility} /></Row>
      <Row label="Decision"><StateBadge value={e.scholarship_status} /></Row>
      {e.finance_visible && approved && (
        <>
          <Row label="Approved terms">
            Revision {approved.revision_number}:{" "}
            {approved.funding_type === "PERCENTAGE"
              ? `${Number(approved.funding_value)}% of the fee`
              : formatMoney(approved.funding_value, approved.currency)}
          </Row>
          <Row label="Company contribution">{formatMoney(approved.company_contribution_amount, approved.currency)}</Row>
          <Row label="Candidate pays">{formatMoney(approved.candidate_payable_amount, approved.currency)}</Row>
          <Row label="Candidate acceptance">
            {e.funding_terms_accepted_revision
              ? `Revision ${e.funding_terms_accepted_revision} accepted ${new Date(e.funding_terms_accepted_at ?? "").toLocaleDateString()}`
              : "Not yet accepted"}
          </Row>
        </>
      )}

      {can("program.manage") &&
        SELECTED.includes(e.application_status) &&
        ["NOT_STARTED", "PENDING"].includes(e.scholarship_status) && (
          <div className="btn-group">
            {e.scholarship_eligibility !== "ELIGIBLE" && (
              <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                onClick={() => run("scholarship-eligibility", { decision: "ELIGIBLE" }, "Eligible for funding consideration.")}>
                Eligible for consideration
              </button>
            )}
            {e.scholarship_eligibility !== "NOT_ELIGIBLE" && (
              <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                onClick={() => run("scholarship-eligibility", { decision: "NOT_ELIGIBLE" }, "Not eligible for funding.")}>
                Not eligible
              </button>
            )}
          </div>
        )}

      {e.funding_revisions.length > 0 && (
        <table className="table" aria-label="Funding history">
          <thead>
            <tr>
              <th>#</th><th>Kind</th><th>Basis</th><th>Company</th><th>Candidate</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {e.funding_revisions.map((r) => (
              <tr key={r.id}>
                <td>{r.revision_number}</td>
                <td>{humanizeStatus(r.kind)}</td>
                <td>{r.funding_type === "PERCENTAGE" ? `${Number(r.funding_value)}%` : formatMoney(r.funding_value, r.currency)}</td>
                <td>{formatMoney(r.company_contribution_amount, r.currency)}</td>
                <td>{formatMoney(r.candidate_payable_amount, r.currency)}</td>
                <td>
                  <StateBadge value={r.status} />
                  {(r.reason || r.decision_note) && (
                    <div className="field-hint">{[r.reason, r.decision_note].filter(Boolean).join(" · ")}</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {open && (
        <div className="stack-sm">
          <p className="field-hint">Revision {open.revision_number} is awaiting approval.</p>
          <label className="field">
            <span>Decision note <span className="optional-mark">(required to reject)</span></span>
            <input value={decisionNote} onChange={(ev) => setDecisionNote(ev.target.value)} disabled={busy} />
          </label>
          <div className="btn-group">
            {can("program.finance.manage") && (
              <>
                <button type="button" className="btn btn-primary btn-sm" disabled={busy}
                  onClick={() => run(`funding-proposals/${open.id}/decision`, { decision: "APPROVED", note: decisionNote || null }, "Funding approved (not yet applied).")}>
                  Approve funding
                </button>
                <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !decisionNote.trim()}
                  onClick={() => run(`funding-proposals/${open.id}/decision`, { decision: "REJECTED", note: decisionNote }, "Funding proposal rejected.")}>
                  Reject
                </button>
              </>
            )}
            {can("program.manage") && (
              <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                onClick={() => run(`funding-proposals/${open.id}/withdraw`, { note: decisionNote || null }, "Proposal withdrawn.")}>
                Withdraw proposal
              </button>
            )}
          </div>
        </div>
      )}

      {canPropose && (
        <div className="stack-sm">
          <h4 style={{ margin: 0 }}>
            {e.scholarship_status === "APPROVED" ? (amendment ? "Propose an amendment" : "Propose revised terms") : "Propose funding"}
          </h4>
          {!fee && <p className="field-hint">Set the program's finalized training fee before proposing funding.</p>}
          <div className="field-row">
            <label className="field">
              <span>Basis</span>
              <select value={proposal.type} disabled={busy}
                onChange={(ev) => setProposal({ ...proposal, type: ev.target.value as FundingType })}>
                {(Object.keys(FUNDING_TYPE_LABELS) as FundingType[]).map((t) => (
                  <option key={t} value={t}>{FUNDING_TYPE_LABELS[t]}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>{proposal.type === "PERCENTAGE" ? "Percentage" : `Amount (${currency ?? "fee currency"})`}</span>
              <input type="number" min={0} step="0.01" value={proposal.value} disabled={busy}
                onChange={(ev) => setProposal({ ...proposal, value: ev.target.value })} />
            </label>
          </div>
          {preview && (
            typeof preview === "string" ? (
              <p className="field-error" role="alert">{preview}</p>
            ) : (
              <p className="field-hint" data-testid="proposal-preview">
                Fee {formatMoney(preview.fee, currency)} → company {formatMoney(preview.companyContribution, currency)},
                candidate pays {formatMoney(preview.candidatePayable, currency)}.
              </p>
            )
          )}
          <label className="field">
            <span>
              Reason {amendment ? <span className="optional-mark">(required — the candidate already accepted the current terms)</span> : <span className="optional-mark">(Optional)</span>}
            </span>
            <input value={proposal.reason} onChange={(ev) => setProposal({ ...proposal, reason: ev.target.value })} disabled={busy} />
          </label>
          <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}
            disabled={busy || !fee || !preview || typeof preview === "string" || (amendment && !proposal.reason.trim())}
            onClick={() => run("funding-proposals", { funding_type: proposal.type, value: proposal.value, reason: proposal.reason || null }, "Funding proposed — awaiting approval.")}>
            Submit for approval
          </button>
        </div>
      )}

      {can("program.finance.manage") && approved && !program.requires_agreement && e.funding_terms_accepted_revision !== approved.revision_number && (
        <div className="field-row">
          <label className="field">
            <span>Candidate accepted these terms on</span>
            <input type="date" value={acceptedOn} max={today()} onChange={(ev) => setAcceptedOn(ev.target.value)} disabled={busy} />
          </label>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !acceptedOn}
              onClick={() => run("funding-acceptance", { accepted_on: acceptedOn }, "Acceptance recorded.")}>
              Record acceptance
            </button>
          </div>
        </div>
      )}

      {can("program.finance.manage") && e.scholarship_status === "APPROVED" && (
        <div className="stack-sm">
          <Row label="Funding applied">
            {formatMoney(e.disbursed_total, e.approved_scholarship_currency)} of{" "}
            {formatMoney(e.approved_scholarship_amount, e.approved_scholarship_currency)}
          </Row>
          {e.disbursements.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {e.disbursements.map((d) => (
                <li key={d.id}>
                  {formatMoney(d.amount, d.currency)} — {humanizeStatus(d.status)}
                  {d.paid_on ? ` on ${d.paid_on}` : ""}
                  {d.reference ? ` · ref ${d.reference}` : ""}
                  {d.status === "SCHEDULED" && (
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                      onClick={() => run(`disbursements/${d.id}`, { status: "PAID", paid_on: today() }, "Marked applied/paid.")}>
                      Mark applied today
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="field-row">
            <label className="field">
              <span>Amount</span>
              <input type="number" min={0} value={applied.amount} onChange={(ev) => setApplied({ ...applied, amount: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Applied on <span className="optional-mark">(empty = scheduled)</span></span>
              <input type="date" value={applied.paidOn} onChange={(ev) => setApplied({ ...applied, paidOn: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Finance reference <span className="optional-mark">(Optional)</span></span>
              <input value={applied.reference} onChange={(ev) => setApplied({ ...applied, reference: ev.target.value })} disabled={busy} />
            </label>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} disabled={busy || !applied.amount}
            onClick={() => run("disbursements", {
              amount: applied.amount,
              status: applied.paidOn ? "PAID" : "SCHEDULED",
              paid_on: applied.paidOn || null,
              reference: applied.reference || null,
            }, "Funding record added.")}>
            Record funding applied
          </button>
        </div>
      )}
    </Block>
  );
}
