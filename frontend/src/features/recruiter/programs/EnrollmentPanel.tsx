import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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
  type AffiliationMethod,
  type EnrollmentResponse,
  type ProgramResponse,
} from "../../../types/program";
import { useAuth } from "../../auth/AuthContext";
import { ApplicationInterviews } from "../interviews/ApplicationInterviews";
import { AgreementSection } from "./AgreementSection";
import {
  downloadAgreementDocument,
  enrollmentAction,
  getEnrollment,
  inviteToProgramAssessment,
  uploadAgreementDocument,
} from "./api";
import { EmploymentSection } from "./EmploymentSection";
import { FeeSection } from "./FeeSection";
import { FundingSection } from "./FundingSection";
import { Block, Row, StateBadge, type SectionProps } from "./panelParts";
import { TrainingSection } from "./TrainingSection";

const IN_REVIEW = ["APPLIED", "AI_SCREENED_OUT", "UNDER_REVIEW"];
const EVIDENCE: { value: AffiliationMethod; label: string }[] = [
  { value: "STUDENT_ID_DOCUMENT", label: "Student ID / document checked" },
  { value: "COORDINATOR_CONFIRMATION", label: "Confirmed by campus coordinator" },
  { value: "MANUAL", label: "Other manual check" },
];

/**
 * One participant's program record. Every button is a single server action
 * that re-checks the program's rules (verified institution before
 * eligibility, test pass before interview, recorded interview outcomes
 * before selection, selection before funding eligibility, approval by a
 * finance approver, training completion before the employment review…).
 * Which actions appear follows the viewer's permissions as reported by the
 * server; selection itself (shortlist / select / reject) stays on the
 * application, the same pipeline every application uses.
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
  const [error, setError] = useState<string | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [evidence, setEvidence] = useState<AffiliationMethod>("STUDENT_ID_DOCUMENT");

  const key = ["recruiter", "programs", program.id, "enrollments", enrollmentId];
  const enrollmentQuery = useQuery({ queryKey: key, queryFn: () => getEnrollment(program.id, enrollmentId, token) });

  function updated(next: EnrollmentResponse, message: string) {
    queryClient.setQueryData(key, next);
    void queryClient.invalidateQueries({ queryKey: ["recruiter", "programs", program.id] });
    setError(null);
    showToast(message, "success");
  }
  const failed = (err: unknown) => setError(err instanceof ApiError ? err.message : "Unable to reach the server.");

  const action = useMutation({
    mutationFn: ({ path, payload }: { path: string; payload: Record<string, unknown>; message: string }) =>
      enrollmentAction(program.id, enrollmentId, path, payload, token),
    onSuccess: (next, vars) => updated(next, vars.message),
    onError: failed,
  });
  const invite = useMutation({
    mutationFn: () => inviteToProgramAssessment(program.id, enrollmentId, token),
    onSuccess: (invitation) => {
      setInviteLink(invitation.invitation_link);
      void queryClient.invalidateQueries({ queryKey: key });
      showToast("Test invitation prepared. Send it from the application page.", "success");
    },
    onError: failed,
  });
  const upload = useMutation({
    mutationFn: (file: File) => uploadAgreementDocument(program.id, enrollmentId, file, token),
    onSuccess: (next) => updated(next, "Signed agreement attached."),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not upload the file."),
  });

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
  const can = (permission: string) => e?.viewer_permissions.includes(permission) ?? false;
  const run = (path: string, payload: Record<string, unknown>, message: string) =>
    action.mutate({ path, payload, message });

  return (
    <Modal title={e ? `${e.candidate_full_name} — ${program.title}` : "Participant"} onClose={onClose} wide>
      {enrollmentQuery.isPending && <p role="status">Loading…</p>}
      {enrollmentQuery.isError && <Alert>Could not load this participant.</Alert>}
      {e && (() => {
        const section: SectionProps = { e, program, busy, can, run };
        const affiliation = e.affiliation;
        return (
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

            <Block title="Institution verification and eligibility">
              <Row label="Institution membership">
                {affiliation ? (
                  <>
                    <StateBadge value={affiliation.status} />
                    {affiliation.method ? ` · ${humanizeStatus(affiliation.method)}` : ""}
                    {affiliation.reviewed_at ? ` · ${new Date(affiliation.reviewed_at).toLocaleDateString()}` : ""}
                  </>
                ) : (
                  "Verification not required by this program"
                )}
              </Row>
              {affiliation?.review_note && <Row label="Verification note">{affiliation.review_note}</Row>}
              <Row label="Eligibility"><StateBadge value={e.eligibility_status} /></Row>
              {e.eligibility_note && <Row label="Note">{e.eligibility_note}</Row>}
              {can("program.manage") && IN_REVIEW.includes(e.application_status) && (
                <>
                  <label className="field">
                    <span>
                      Note <span className="optional-mark">(required to reject or mark not eligible)</span>
                    </span>
                    <input value={note} onChange={(ev) => setNote(ev.target.value)} disabled={busy} />
                  </label>
                  {affiliation && e.eligibility_status !== "ELIGIBLE" && (
                    <div className="field-row">
                      {affiliation.status !== "VERIFIED" && (
                        <label className="field">
                          <span>Evidence checked</span>
                          <select value={evidence} onChange={(ev) => setEvidence(ev.target.value as AffiliationMethod)} disabled={busy}>
                            {EVIDENCE.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                          </select>
                        </label>
                      )}
                      <div className="field" style={{ alignSelf: "end" }}>
                        <div className="btn-group">
                          {affiliation.status !== "VERIFIED" && (
                            <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                              onClick={() => run("affiliation", { status: "VERIFIED", method: evidence, note: note || null }, "Institution membership verified.")}>
                              Verify membership
                            </button>
                          )}
                          {affiliation.status !== "REJECTED" && (
                            <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !note.trim()}
                              onClick={() => run("affiliation", { status: "REJECTED", note }, "Membership rejected.")}>
                              Reject membership
                            </button>
                          )}
                          {affiliation.status !== "PENDING" && (
                            <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                              onClick={() => run("affiliation", { status: "PENDING", note: note || null }, "Reopened for new evidence.")}>
                              Reopen (resubmission)
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                  <div className="btn-group">
                    {e.eligibility_status !== "ELIGIBLE" && (
                      <button type="button" className="btn btn-primary btn-sm"
                        disabled={busy || (affiliation !== null && affiliation.status !== "VERIFIED")}
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
                  {affiliation && affiliation.status !== "VERIFIED" && e.eligibility_status !== "ELIGIBLE" && (
                    <p className="field-hint">Verify the institution membership before marking the candidate eligible.</p>
                  )}
                </>
              )}
            </Block>

            <Block title="Selection test">
              {program.assessment_title ? (
                <Row label="Test">
                  {program.assessment_title} —{" "}
                  {e.latest_result
                    ? e.latest_result.passed === null
                      ? `awaiting grading (${e.latest_result.percentage}% so far)`
                      : `${e.latest_result.passed ? "passed" : "failed"} (${e.latest_result.percentage}%` +
                        (e.latest_result.pass_score_applied !== null ? ` against a pass mark of ${e.latest_result.pass_score_applied}%` : "") +
                        `, attempt ${e.latest_result.attempt_number})`
                    : "not taken yet"}
                </Row>
              ) : (
                <p className="muted">This program has no selection test.</p>
              )}
              {can("program.manage") &&
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
              <p className="field-hint">
                A pass makes the candidate eligible to be shortlisted for interview — it guarantees
                nothing further.
              </p>
            </Block>

            <ApplicationInterviews
              applicationId={e.application_id}
              applicationStatus={e.application_status}
              token={token}
              userId={user?.id}
              stages={program.interview_stages}
              onChanged={() => void queryClient.invalidateQueries({ queryKey: ["recruiter", "programs", program.id] })}
            >
              <Row label="Interview process">
                {e.interview_completed_at
                  ? `Recorded complete ${new Date(e.interview_completed_at).toLocaleDateString()}`
                  : "Not complete — every required stage needs a recorded outcome"}
              </Row>
              {can("program.manage") && e.application_status === "INTERVIEW" && !e.interview_completed_at && (
                <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} disabled={busy}
                  onClick={() => run("interview-completed", {}, "Interview process recorded as complete.")}>
                  Record interview process complete
                </button>
              )}
            </ApplicationInterviews>
            <FundingSection {...section} />
            {program.tracks_fee_payments && <FeeSection {...section} />}
            {program.tracks_training && <TrainingSection {...section} />}
            {program.requires_agreement && (
              <AgreementSection {...section} onUpload={(file) => upload.mutate(file)} onDownload={() => void download()} uploading={upload.isPending} />
            )}
            {program.tracks_employment && <EmploymentSection {...section} token={token} />}
          </div>
        );
      })()}
    </Modal>
  );
}
