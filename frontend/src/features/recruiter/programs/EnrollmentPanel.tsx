import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { triggerBlobDownload } from "../../../lib/downloadBlob";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import { humanizeStatus } from "../../../shared/lib/statusTone";
import {
  EDUCATION_LEVEL_LABELS,
  type AgreementStatus,
  type EnrollmentResponse,
  type OfferStatus,
  type PaymentMethod,
  type ProgramResponse,
  type TrainingStatus,
} from "../../../types/program";
import { useAuth } from "../../auth/AuthContext";
import {
  downloadAgreementDocument,
  enrollmentAction,
  getEnrollment,
  inviteToProgramAssessment,
  uploadAgreementDocument,
} from "./api";

const TRAINING_NEXT: Record<TrainingStatus, TrainingStatus[]> = {
  NOT_ENROLLED: ["ENROLLED"],
  ENROLLED: ["IN_PROGRESS", "DISCONTINUED"],
  IN_PROGRESS: ["COMPLETED", "DISCONTINUED"],
  COMPLETED: [],
  DISCONTINUED: [],
};
const OFFER_NEXT: Record<OfferStatus, OfferStatus[]> = {
  NOT_OFFERED: ["OFFERED"],
  OFFERED: ["ACCEPTED", "DECLINED", "WITHDRAWN"],
  ACCEPTED: ["WITHDRAWN"],
  DECLINED: [],
  WITHDRAWN: [],
};
const AGREEMENT_NEXT: Record<AgreementStatus, AgreementStatus[]> = {
  NOT_REQUIRED: ["PENDING"],
  PENDING: ["SENT", "VOID"],
  SENT: ["SIGNED", "DECLINED", "VOID"],
  SIGNED: ["VOID"],
  DECLINED: ["PENDING"],
  VOID: ["PENDING"],
};
const PAYMENT_METHODS: PaymentMethod[] = ["BANK_TRANSFER", "UPI", "CHEQUE", "CASH", "OTHER"];
const SELECTED = ["SELECTED", "HIRED"];
const IN_REVIEW = ["APPLIED", "AI_SCREENED_OUT", "UNDER_REVIEW"];

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card stack-sm" style={{ background: "var(--color-bg)" }}>
      <h3 style={{ margin: 0 }}>{title}</h3>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="detail-row">
      <span className="detail-row-label">{label}</span>
      <span>{children}</span>
    </div>
  );
}

const money = (amount: string | null, currency: string | null) =>
  amount && currency ? `${currency} ${Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—";

/**
 * One participant's program record. Every button is a single server action
 * that re-checks the program's rules (eligibility before progress, test pass
 * before interview, interview before selection, selection before
 * scholarship eligibility, eligibility before approval…). Selection itself
 * (shortlist / interview / select / reject) stays on the application, the
 * same pipeline every application uses.
 */
export function EnrollmentPanel({
  program,
  enrollmentId,
  onClose,
}: {
  program: ProgramResponse;
  enrollmentId: string;
  onClose: () => void;
}) {
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  // Mirrors the default role grants: `program.manage` (ORG_ADMIN, RECRUITER)
  // for workflow actions, `program.finance.manage` (ORG_ADMIN) for money and
  // agreements. The server enforces both; this only hides refused controls.
  const canManage = user?.roles.some((r) => ["ORG_ADMIN", "RECRUITER"].includes(r)) ?? false;
  const isFinance = user?.roles.includes("ORG_ADMIN") ?? false;
  const fileInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [award, setAward] = useState({ amount: "", currency: program.scholarship_currency ?? "" });
  const [disbursement, setDisbursement] = useState({ amount: "", paidOn: "", reference: "" });
  const [payment, setPayment] = useState({
    amount: "",
    currency: program.training_fee_currency ?? "",
    paidOn: "",
    method: "BANK_TRANSFER" as PaymentMethod,
    reference: "",
  });
  const [training, setTraining] = useState({ duration: "", start: "", end: "" });
  const [agreement, setAgreement] = useState({ reference: "", signedOn: "" });
  const [bondStart, setBondStart] = useState("");
  const [joinedOn, setJoinedOn] = useState("");

  const key = ["recruiter", "programs", program.id, "enrollments", enrollmentId];
  const enrollmentQuery = useQuery({ queryKey: key, queryFn: () => getEnrollment(program.id, enrollmentId, token) });

  function updated(next: EnrollmentResponse, message: string) {
    queryClient.setQueryData(key, next);
    void queryClient.invalidateQueries({ queryKey: ["recruiter", "programs", program.id] });
    setError(null);
    showToast(message, "success");
  }

  const action = useMutation({
    mutationFn: ({ path, payload }: { path: string; payload: Record<string, unknown>; message: string }) =>
      enrollmentAction(program.id, enrollmentId, path, payload, token),
    onSuccess: (next, vars) => updated(next, vars.message),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });
  const invite = useMutation({
    mutationFn: () => inviteToProgramAssessment(program.id, enrollmentId, token),
    onSuccess: (invitation) => {
      setInviteLink(invitation.invitation_link);
      void queryClient.invalidateQueries({ queryKey: key });
      showToast("Test invitation prepared. Send it from the application page.", "success");
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });
  const upload = useMutation({
    mutationFn: (file: File) => uploadAgreementDocument(program.id, enrollmentId, file, token),
    onSuccess: (next) => updated(next, "Signed agreement attached."),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not upload the file."),
  });

  function run(path: string, payload: Record<string, unknown>, message: string) {
    action.mutate({ path, payload, message });
  }

  async function download() {
    try {
      const { blob, filename } = await downloadAgreementDocument(program.id, enrollmentId, token);
      triggerBlobDownload(blob, filename ?? "agreement.pdf");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not download the file.");
    }
  }

  const busy = action.isPending || invite.isPending || upload.isPending;
  const e = enrollmentQuery.data;

  return (
    <Modal title={e ? `${e.candidate_full_name} — ${program.title}` : "Participant"} onClose={onClose} wide>
      {enrollmentQuery.isPending && <p role="status">Loading…</p>}
      {enrollmentQuery.isError && <Alert>Could not load this participant.</Alert>}
      {e && (
        <div className="stack-sm">
          {error && <Alert>{error}</Alert>}
          <Block title="Application">
            <Row label="Stage">{e.stage}</Row>
            <Row label="Pipeline status">
              {humanizeStatus(e.application_status)} ·{" "}
              <Link to={`/recruiter/applications/${e.application_id}`}>Open application</Link>
            </Row>
            <Row label="Education (declared)">
              {EDUCATION_LEVEL_LABELS[e.education_level]}
              {e.study_year ? `, year ${e.study_year}` : ""} · {e.specialization} · {e.institution_name}
              {e.expected_completion_date ? ` · completes ${e.expected_completion_date}` : ""}
            </Row>
            <Row label="Terms accepted">
              Version {e.terms_version_accepted} on {new Date(e.terms_accepted_at).toLocaleString()}
            </Row>
          </Block>

          <Block title="Eligibility review">
            <Row label="Status">{humanizeStatus(e.eligibility_status)}</Row>
            {e.eligibility_note && <Row label="Note">{e.eligibility_note}</Row>}
            {canManage && IN_REVIEW.includes(e.application_status) && (
              <>
                <label className="field">
                  <span>
                    Note <span className="optional-mark">(required when not eligible)</span>
                  </span>
                  <input value={note} onChange={(ev) => setNote(ev.target.value)} disabled={busy} />
                </label>
                <div className="btn-group">
                  {e.eligibility_status !== "ELIGIBLE" && (
                    <button type="button" className="btn btn-primary btn-sm" disabled={busy}
                      onClick={() => run("eligibility", { status: "ELIGIBLE", note: note || null }, "Marked eligible.")}>
                      Mark eligible
                    </button>
                  )}
                  {e.eligibility_status !== "NOT_ELIGIBLE" && (
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                      onClick={() => run("eligibility", { status: "NOT_ELIGIBLE", note: note || null }, "Marked not eligible.")}>
                      Mark not eligible
                    </button>
                  )}
                </div>
              </>
            )}
          </Block>

          <Block title="Selection test and interview">
            {program.assessment_title ? (
              <Row label="Test">
                {program.assessment_title} —{" "}
                {e.latest_result
                  ? e.latest_result.passed === null
                    ? `awaiting grading (${e.latest_result.percentage}% so far)`
                    : `${e.latest_result.passed ? "passed" : "failed"} (${e.latest_result.percentage}%, attempt ${e.latest_result.attempt_number})`
                  : "not taken yet"}
              </Row>
            ) : (
              <p className="muted">This program has no selection test.</p>
            )}
            {canManage &&
              program.assessment_id &&
              e.eligibility_status === "ELIGIBLE" &&
              ["UNDER_REVIEW", "SCREENING"].includes(e.application_status) && (
                <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}
                  disabled={busy} onClick={() => invite.mutate()}>
                  {invite.isPending ? <Spinner label="Preparing…" /> : "Invite to the selection test"}
                </button>
              )}
            {inviteLink && (
              <p className="field-hint">
                Invitation prepared. Email it from the{" "}
                <Link to={`/recruiter/applications/${e.application_id}`}>application</Link> ("Send
                Assessment Invitation").
              </p>
            )}
            <Row label="Interview">
              {e.interview_completed_at
                ? `Completed ${new Date(e.interview_completed_at).toLocaleDateString()}`
                : e.application_status === "INTERVIEW"
                  ? "Scheduled"
                  : "—"}
            </Row>
            {program.interview_stages.length > 0 && <Row label="Stages">{program.interview_stages.join(" → ")}</Row>}
            {canManage && e.application_status === "INTERVIEW" && !e.interview_completed_at && (
              <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} disabled={busy}
                onClick={() => run("interview-completed", {}, "Interview recorded as completed.")}>
                Record interview completed
              </button>
            )}
            <p className="field-hint">
              Shortlist, interview and select/reject on the application. The program's rules are
              enforced there too.
            </p>
          </Block>

          <Block title="Scholarship">
            <Row label="Eligibility">{humanizeStatus(e.scholarship_eligibility)}</Row>
            <Row label="Decision">{humanizeStatus(e.scholarship_status)}</Row>
            <Row label="Approved amount">{money(e.approved_scholarship_amount, e.approved_scholarship_currency)}</Row>
            {canManage &&
              SELECTED.includes(e.application_status) &&
              e.scholarship_status !== "APPROVED" &&
              e.scholarship_status !== "DECLINED" && (
                <div className="btn-group">
                  {e.scholarship_eligibility !== "ELIGIBLE" && (
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                      onClick={() => run("scholarship-eligibility", { decision: "ELIGIBLE" }, "Eligible for scholarship consideration.")}>
                      Eligible for consideration
                    </button>
                  )}
                  {e.scholarship_eligibility !== "NOT_ELIGIBLE" && (
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                      onClick={() => run("scholarship-eligibility", { decision: "NOT_ELIGIBLE" }, "Not eligible for the scholarship.")}>
                      Not eligible
                    </button>
                  )}
                </div>
              )}
            {isFinance && e.scholarship_status === "PENDING" && (
              <div className="field-row">
                <label className="field">
                  <span>Approved amount</span>
                  <input type="number" min={0} value={award.amount} onChange={(ev) => setAward({ ...award, amount: ev.target.value })} disabled={busy} />
                </label>
                <label className="field">
                  <span>Currency</span>
                  <input value={award.currency} onChange={(ev) => setAward({ ...award, currency: ev.target.value })} disabled={busy} placeholder="INR" />
                </label>
                <div className="field" style={{ alignSelf: "end" }}>
                  <div className="btn-group">
                    <button type="button" className="btn btn-primary btn-sm" disabled={busy || !award.amount || !award.currency}
                      onClick={() => run("scholarship-decision", { decision: "APPROVED", approved_amount: award.amount, currency: award.currency }, "Scholarship approved (not yet disbursed).")}>
                      Approve
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                      onClick={() => run("scholarship-decision", { decision: "DECLINED" }, "Scholarship declined.")}>
                      Decline
                    </button>
                  </div>
                </div>
              </div>
            )}
            {isFinance && e.scholarship_status === "APPROVED" && (
              <>
                <Row label="Disbursed">{money(e.disbursed_total, e.approved_scholarship_currency)}</Row>
                {e.disbursements.length > 0 && (
                  <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                    {e.disbursements.map((d) => (
                      <li key={d.id}>
                        {money(d.amount, d.currency)} — {humanizeStatus(d.status)}
                        {d.paid_on ? ` on ${d.paid_on}` : ""}
                        {d.reference ? ` · ref ${d.reference}` : ""}
                        {d.status === "SCHEDULED" && (
                          <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                            onClick={() => run(`disbursements/${d.id}`, { status: "PAID", paid_on: new Date().toISOString().slice(0, 10) }, "Disbursement marked paid.")}>
                            Mark paid today
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="field-row">
                  <label className="field">
                    <span>Disbursement amount</span>
                    <input type="number" min={0} value={disbursement.amount} onChange={(ev) => setDisbursement({ ...disbursement, amount: ev.target.value })} disabled={busy} />
                  </label>
                  <label className="field">
                    <span>Paid on <span className="optional-mark">(empty = scheduled)</span></span>
                    <input type="date" value={disbursement.paidOn} onChange={(ev) => setDisbursement({ ...disbursement, paidOn: ev.target.value })} disabled={busy} />
                  </label>
                  <label className="field">
                    <span>Finance reference <span className="optional-mark">(Optional)</span></span>
                    <input value={disbursement.reference} onChange={(ev) => setDisbursement({ ...disbursement, reference: ev.target.value })} disabled={busy} />
                  </label>
                </div>
                <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} disabled={busy || !disbursement.amount}
                  onClick={() => run("disbursements", {
                    amount: disbursement.amount,
                    status: disbursement.paidOn ? "PAID" : "SCHEDULED",
                    paid_on: disbursement.paidOn || null,
                    reference: disbursement.reference || null,
                  }, "Disbursement recorded.")}>
                  Record disbursement
                </button>
              </>
            )}
          </Block>

          {program.tracks_training && (
            <Block title="Training">
              <Row label="Status">{humanizeStatus(e.training_status)}</Row>
              <Row label="Duration">{e.training_duration_months ? `${e.training_duration_months} months` : "—"}</Row>
              <Row label="Dates">{[e.training_start_date, e.training_end_date].filter(Boolean).join(" → ") || "—"}</Row>
              {canManage && SELECTED.includes(e.application_status) && TRAINING_NEXT[e.training_status].length > 0 && (
                <>
                  <div className="field-row">
                    {e.training_status === "NOT_ENROLLED" && (
                      <label className="field">
                        <span>Duration</span>
                        <select value={training.duration} onChange={(ev) => setTraining({ ...training, duration: ev.target.value })} disabled={busy}>
                          <option value="">Choose…</option>
                          {program.training_duration_options.map((m) => (
                            <option key={m} value={m}>{m} months</option>
                          ))}
                        </select>
                      </label>
                    )}
                    <label className="field">
                      <span>Start date <span className="optional-mark">(Optional)</span></span>
                      <input type="date" value={training.start} onChange={(ev) => setTraining({ ...training, start: ev.target.value })} disabled={busy} />
                    </label>
                    <label className="field">
                      <span>End date <span className="optional-mark">(Optional)</span></span>
                      <input type="date" value={training.end} onChange={(ev) => setTraining({ ...training, end: ev.target.value })} disabled={busy} />
                    </label>
                  </div>
                  <div className="btn-group">
                    {TRAINING_NEXT[e.training_status].map((next) => (
                      <button key={next} type="button" className="btn btn-ghost btn-sm" disabled={busy}
                        onClick={() => run("training", {
                          status: next,
                          duration_months: training.duration ? Number(training.duration) : null,
                          start_date: training.start || null,
                          end_date: training.end || null,
                        }, `Training: ${humanizeStatus(next).toLowerCase()}.`)}>
                        {humanizeStatus(next)}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </Block>
          )}

          {program.tracks_fee_payments && isFinance && (
            <Block title="Training-fee payments (manual record)">
              <p className="field-hint">
                Payments are made outside the platform. Record the receipt reference only — never
                card or bank account details. A mistaken entry is voided, never deleted.
              </p>
              <Row label="Total recorded">{money(e.fee_paid_total, program.training_fee_currency ?? e.fee_payments[0]?.currency ?? null)}</Row>
              {e.fee_payments.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                  {e.fee_payments.map((p) => (
                    <li key={p.id}>
                      {money(p.amount, p.currency)} on {p.paid_on} via {humanizeStatus(p.method)}
                      {p.reference ? ` · ref ${p.reference}` : ""}
                      {p.status === "VOIDED" ? (
                        <em> — voided ({p.void_reason})</em>
                      ) : (
                        <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                          onClick={() => {
                            const reason = window.prompt("Why is this payment record being voided?");
                            if (reason && reason.trim()) run(`fee-payments/${p.id}/void`, { reason }, "Payment record voided.");
                          }}>
                          Void
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <div className="field-row">
                <label className="field">
                  <span>Amount</span>
                  <input type="number" min={0} value={payment.amount} onChange={(ev) => setPayment({ ...payment, amount: ev.target.value })} disabled={busy} />
                </label>
                <label className="field">
                  <span>Currency</span>
                  <input value={payment.currency} onChange={(ev) => setPayment({ ...payment, currency: ev.target.value })} disabled={busy} placeholder="INR" />
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
                  <span>Receipt reference <span className="optional-mark">(Optional)</span></span>
                  <input value={payment.reference} onChange={(ev) => setPayment({ ...payment, reference: ev.target.value })} disabled={busy} />
                </label>
              </div>
              <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}
                disabled={busy || !payment.amount || !payment.currency || !payment.paidOn}
                onClick={() => run("fee-payments", {
                  amount: payment.amount,
                  currency: payment.currency,
                  paid_on: payment.paidOn,
                  method: payment.method,
                  reference: payment.reference || null,
                }, "Payment recorded.")}>
                Record payment
              </button>
            </Block>
          )}

          {program.tracks_employment && (
            <Block title="Full-time employment">
              <Row label="Offer">{humanizeStatus(e.offer_status)}</Row>
              <Row label="Joined">{e.joined_on ?? "—"}</Row>
              {canManage && (
                <div className="btn-group">
                  {OFFER_NEXT[e.offer_status]
                    .filter((next) => next !== "OFFERED" || e.application_status === "SELECTED")
                    .map((next) => (
                      <button key={next} type="button" className="btn btn-ghost btn-sm" disabled={busy}
                        onClick={() => run("offer", { status: next }, `Offer ${humanizeStatus(next).toLowerCase()}.`)}>
                        {next === "OFFERED" ? "Record offer made" : `Offer ${humanizeStatus(next).toLowerCase()}`}
                      </button>
                    ))}
                </div>
              )}
              {canManage && e.offer_status === "ACCEPTED" && !e.joined_on && (
                <div className="field-row">
                  <label className="field">
                    <span>Joining date</span>
                    <input type="date" value={joinedOn} onChange={(ev) => setJoinedOn(ev.target.value)} disabled={busy} />
                  </label>
                  <div className="field" style={{ alignSelf: "end" }}>
                    <button type="button" className="btn btn-primary btn-sm" disabled={busy || !joinedOn}
                      onClick={() => run("joining", { joined_on: joinedOn }, "Joining recorded.")}>
                      Record joining
                    </button>
                  </div>
                </div>
              )}
            </Block>
          )}

          {program.requires_agreement && isFinance && (
            <Block title="Agreement and commitment">
              <p className="field-hint">
                Track the company-approved agreement by its reference and the signed copy. The
                platform never drafts or edits agreement wording.
              </p>
              <Row label="Agreement">{humanizeStatus(e.agreement_status)}</Row>
              <Row label="Reference">{e.agreement_reference ?? "—"}</Row>
              <Row label="Signed">{e.agreement_signed_on ?? "—"}</Row>
              <Row label="Signed copy">
                {e.agreement_document_filename ? (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => void download()}>
                    Download {e.agreement_document_filename}
                  </button>
                ) : (
                  "—"
                )}
              </Row>
              <div className="field-row">
                <label className="field">
                  <span>Agreement reference</span>
                  <input value={agreement.reference} onChange={(ev) => setAgreement({ ...agreement, reference: ev.target.value })} disabled={busy} placeholder={e.agreement_reference ?? "e.g. AGR-2027-001"} />
                </label>
                <label className="field">
                  <span>Signed on</span>
                  <input type="date" value={agreement.signedOn} onChange={(ev) => setAgreement({ ...agreement, signedOn: ev.target.value })} disabled={busy} />
                </label>
              </div>
              <div className="btn-group">
                {AGREEMENT_NEXT[e.agreement_status].map((next) => (
                  <button key={next} type="button" className="btn btn-ghost btn-sm" disabled={busy}
                    onClick={() => run("agreement", {
                      status: next,
                      reference: agreement.reference || null,
                      signed_on: agreement.signedOn || null,
                    }, `Agreement: ${humanizeStatus(next).toLowerCase()}.`)}>
                    Mark {humanizeStatus(next).toLowerCase()}
                  </button>
                ))}
                <input ref={fileInput} type="file" accept="application/pdf,.pdf" style={{ display: "none" }}
                  onChange={(ev) => {
                    const file = ev.target.files?.[0];
                    ev.target.value = "";
                    if (file) upload.mutate(file);
                  }} />
                <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => fileInput.current?.click()}>
                  {upload.isPending ? <Spinner label="Uploading…" /> : "Attach signed copy (PDF)"}
                </button>
              </div>
              <Row label="Commitment">
                {e.bond_duration_months ? `${e.bond_duration_months} months` : "—"}
                {e.bond_start_date ? ` · ${e.bond_start_date} → ${e.bond_end_date}` : ""}
              </Row>
              {e.scholarship_status === "APPROVED" && e.agreement_status === "SIGNED" && (
                <div className="field-row">
                  <label className="field">
                    <span>Commitment start date</span>
                    <input type="date" value={bondStart} onChange={(ev) => setBondStart(ev.target.value)} disabled={busy} />
                  </label>
                  <div className="field" style={{ alignSelf: "end" }}>
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !bondStart}
                      onClick={() => run("bond", { start_date: bondStart }, "Commitment dates recorded.")}>
                      Record start
                    </button>
                  </div>
                </div>
              )}
            </Block>
          )}
        </div>
      )}
    </Modal>
  );
}
