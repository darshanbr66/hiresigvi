import { useRef, useState } from "react";
import type { AgreementStatus } from "../../../types/program";
import { formatMoney } from "./finance";
import { Block, Row, StateBadge, type SectionProps } from "./panelParts";

const AGREEMENT_NEXT: Record<AgreementStatus, AgreementStatus[]> = {
  NOT_REQUIRED: ["PENDING"],
  PENDING: ["SENT", "VOID"],
  SENT: ["SIGNED", "DECLINED", "VOID"],
  SIGNED: ["EXPIRED", "TERMINATED"],
  DECLINED: ["PENDING"],
  VOID: ["PENDING"],
  EXPIRED: [],
  TERMINATED: [],
};
const ACTION: Record<AgreementStatus, string> = {
  NOT_REQUIRED: "Not required",
  PENDING: "Prepare agreement",
  SENT: "Mark sent (freeze terms)",
  SIGNED: "Record signed copy received",
  DECLINED: "Candidate declined",
  VOID: "Void",
  EXPIRED: "Mark expired",
  TERMINATED: "Terminate",
};
const NEEDS_NOTE: AgreementStatus[] = ["DECLINED", "VOID", "TERMINATED"];

/**
 * The company-approved agreement and the bond (commitment) for a funded
 * participant. The platform never drafts agreement wording and recording
 * "signed" is a record of a signature made elsewhere — not an electronic
 * signature. Sending freezes the funding terms being agreed.
 */
export function AgreementSection({
  e,
  busy,
  can,
  run,
  onUpload,
  onDownload,
  uploading,
}: SectionProps & { onUpload: (file: File) => void; onDownload: () => void; uploading: boolean }) {
  const [form, setForm] = useState({ reference: "", version: "", signedOn: "", note: "" });
  const [bondStart, setBondStart] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  if (!e.finance_visible) return null;
  const finance = can("program.finance.manage");
  const snapshot = e.agreement_terms_snapshot;
  const currency = snapshot ? String(snapshot.currency ?? "") : null;

  return (
    <Block title="Agreement and bond">
      <p className="field-hint">
        A bond applies only to participants who receive approved company funding, under the company's
        approved agreement (wording and legal terms are set by HR/legal, not by this platform).
      </p>
      <Row label="Agreement"><StateBadge value={e.agreement_status} /></Row>
      {e.bond_applies_reason && <Row label="Why the bond applies">{e.bond_applies_reason}</Row>}
      <Row label="Version / reference">{[e.agreement_version, e.agreement_reference].filter(Boolean).join(" · ") || "—"}</Row>
      {snapshot && snapshot.funding_revision === null && (
        <Row label="Terms sent">
          Funding approved before revisions were tracked: company{" "}
          {formatMoney(snapshot.company_contribution === null ? null : String(snapshot.company_contribution), currency)},
          bond {String(snapshot.bond_duration_months ?? "—")} months
        </Row>
      )}
      {snapshot && snapshot.funding_revision !== null && (
        <Row label="Terms sent">
          Fee {formatMoney(String(snapshot.training_fee), currency)}, company{" "}
          {formatMoney(String(snapshot.company_contribution), currency)}, candidate{" "}
          {formatMoney(String(snapshot.candidate_payable), currency)}, bond {String(snapshot.bond_duration_months ?? "—")} months
          (funding revision {String(snapshot.funding_revision)})
          {e.agreement_terms_current === false && (
            <> · <StateBadge value="OVERDUE" label="Funding changed since — re-acceptance needed" /></>
          )}
        </Row>
      )}
      <Row label="Signed">{e.agreement_signed_on ?? "—"}</Row>
      <Row label="In force">
        {e.agreement_effective_date ? `${e.agreement_effective_date} → ${e.agreement_expiry_date}` : "—"}
      </Row>
      {e.agreement_status_note && <Row label="Note">{e.agreement_status_note}</Row>}
      <Row label="Signed copy">
        {e.agreement_document_filename ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onDownload}>
            Download {e.agreement_document_filename}
          </button>
        ) : (
          "—"
        )}
      </Row>
      {finance && AGREEMENT_NEXT[e.agreement_status].length > 0 && (
        <>
          <div className="field-row">
            <label className="field">
              <span>Approved agreement version</span>
              <input value={form.version} placeholder={e.agreement_version ?? "e.g. AGR-TPL-v3"} onChange={(ev) => setForm({ ...form, version: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Agreement reference</span>
              <input value={form.reference} placeholder={e.agreement_reference ?? "e.g. AGR-2027-001"} onChange={(ev) => setForm({ ...form, reference: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Signed on</span>
              <input type="date" value={form.signedOn} onChange={(ev) => setForm({ ...form, signedOn: ev.target.value })} disabled={busy} />
            </label>
          </div>
          <label className="field">
            <span>Note <span className="optional-mark">(required to decline, void or terminate)</span></span>
            <input value={form.note} onChange={(ev) => setForm({ ...form, note: ev.target.value })} disabled={busy} />
          </label>
          <div className="btn-group">
            {AGREEMENT_NEXT[e.agreement_status].map((next) => (
              <button key={next} type="button" className="btn btn-ghost btn-sm"
                disabled={busy || (NEEDS_NOTE.includes(next) && !form.note.trim())}
                onClick={() => run("agreement", {
                  status: next,
                  reference: form.reference || null,
                  version: form.version || null,
                  signed_on: form.signedOn || null,
                  note: form.note || null,
                }, `Agreement: ${ACTION[next].toLowerCase()}.`)}>
                {ACTION[next]}
              </button>
            ))}
            <input ref={fileInput} type="file" accept="application/pdf,.pdf" style={{ display: "none" }}
              onChange={(ev) => {
                const file = ev.target.files?.[0];
                ev.target.value = "";
                if (file) onUpload(file);
              }} />
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => fileInput.current?.click()}>
              {uploading ? "Uploading…" : "Attach signed copy (PDF)"}
            </button>
          </div>
        </>
      )}
      <Row label="Bond">
        {e.bond_duration_months && e.scholarship_status === "APPROVED" ? `${e.bond_duration_months} months` : "Not applicable (no approved funding)"}
        {e.bond_start_date ? ` · ${e.bond_start_date} → ${e.bond_end_date}` : ""}
      </Row>
      {finance && e.scholarship_status === "APPROVED" && e.agreement_status === "SIGNED" && (
        <div className="field-row">
          <label className="field">
            <span>Bond start date</span>
            <input type="date" value={bondStart} onChange={(ev) => setBondStart(ev.target.value)} disabled={busy} />
          </label>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !bondStart}
              onClick={() => run("bond", { start_date: bondStart }, "Bond dates recorded.")}>
              Record start
            </button>
          </div>
        </div>
      )}
    </Block>
  );
}
