import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { triggerBlobDownload } from "../../../lib/downloadBlob";
import { Alert } from "../../../shared/components/Alert";
import { ConfirmDialog } from "../../../shared/components/ConfirmDialog";
import { Modal } from "../../../shared/components/Modal";
import { ResumePreviewModal } from "../../../shared/components/ResumePreviewModal";
import { SkeletonLines } from "../../../shared/components/Skeleton";
import { BackLink } from "../../../shared/components/BackLink";
import { PipelineTrack } from "../../../shared/components/PipelineTrack";
import { useToast } from "../../../shared/components/ToastContext";
import { humanizeStatus, statusTone, toneBadgeClass } from "../../../shared/lib/statusTone";
import { Spinner } from "../../../shared/components/Spinner";
import { APPLICATION_TRANSITIONS, type ApplicationStatus } from "../../../types/recruitment";
import type { EmailTemplateKey } from "../../../types/email";
import type {
  AssessmentCreateRequest,
  MonitoringEventType,
  RetestAssessmentChoice,
} from "../../../types/assessment";
import { useAuth } from "../../auth/AuthContext";
import { AssessmentForm, emptyAssessmentFormValue } from "../assessments/AssessmentForm";
import {
  getApplicationAssessment,
  inviteCandidate,
  listApplicationAssessmentAttempts,
  listApplicationAssessmentEvents,
  listAssessments,
  retestCandidate,
} from "../assessments/api";
import { createNote, listNotes } from "../notes/api";
import { listScreeningRuns, startScreening } from "../screening/api";
import {
  changeApplicationStatus,
  downloadResume,
  getApplication,
  overrideAiScreening,
} from "./api";
import { EmailComposerModal } from "./EmailComposerModal";

/** Non-accusatory, human-readable labels for observed monitoring events
 * (SIGVITAS platform overhaul § 7) — describes what was observed, never
 * accuses the candidate of anything. */
const MONITORING_EVENT_LABELS: Record<MonitoringEventType, string> = {
  MONITORING_CONSENT_GIVEN: "Candidate agreed to assessment monitoring",
  TAB_SWITCH: "Assessment tab/window changed",
  WINDOW_BLUR: "Assessment window lost focus",
  WINDOW_FOCUS: "Assessment window regained focus",
  FULLSCREEN_EXIT: "Fullscreen exited",
  CAMERA_PERMISSION_CHANGED: "Camera permission changed",
  MICROPHONE_PERMISSION_CHANGED: "Microphone permission changed",
  CAMERA_DEVICE_CHANGED: "Camera device changed",
  MICROPHONE_DEVICE_CHANGED: "Microphone device changed",
  CAMERA_UNAVAILABLE: "Camera unavailable",
  MICROPHONE_UNAVAILABLE: "Microphone unavailable",
  CONNECTION_INTERRUPTED: "Connection interrupted",
  CONNECTION_RESTORED: "Connection restored",
};

const RECOMMENDATION_BADGE: Record<string, string> = {
  STRONG_MATCH: "badge-active",
  POSSIBLE_MATCH: "badge-active",
  WEAK_MATCH: "badge-warn",
  NOT_A_MATCH: "badge-danger",
};

const STATUSES_REQUIRING_CONFIRMATION: ApplicationStatus[] = ["REJECTED"];

/** A result with answers still waiting for a grader has no pass/fail yet. */
function resultLabel(passed: boolean | null): string {
  return passed === null ? "AWAITING GRADING" : passed ? "PASSED" : "FAILED";
}

function resultBadge(passed: boolean | null): string {
  return passed === null ? "badge-warn" : passed ? "badge-active" : "badge-danger";
}

export function ApplicationDetailPage() {
  const { applicationId = "" } = useParams<{ applicationId: string }>();
  const { accessToken } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [noteBody, setNoteBody] = useState("");
  const [selectedAssessmentId, setSelectedAssessmentId] = useState("");
  const [pendingStatus, setPendingStatus] = useState<ApplicationStatus | null>(null);
  const [showRetestForm, setShowRetestForm] = useState(false);
  const [retestReason, setRetestReason] = useState("");
  const [retestError, setRetestError] = useState<string | null>(null);
  const [retestChoice, setRetestChoice] = useState<RetestAssessmentChoice>("SAME");
  const [retestAssessmentId, setRetestAssessmentId] = useState("");
  const [retestNewAssessment, setRetestNewAssessment] = useState<AssessmentCreateRequest>(
    emptyAssessmentFormValue(),
  );
  const [showResumePreview, setShowResumePreview] = useState(false);
  // null = composer closed; "" = open with no template chosen yet.
  const [composerTemplate, setComposerTemplate] = useState<EmailTemplateKey | "" | null>(null);
  const [emailFeedback, setEmailFeedback] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState("");

  const applicationQuery = useQuery({
    queryKey: ["recruiter", "applications", applicationId],
    queryFn: () => getApplication(applicationId, token),
    enabled: accessToken !== null,
  });

  const screeningQuery = useQuery({
    queryKey: ["recruiter", "screening", applicationId],
    queryFn: () => listScreeningRuns(applicationId, token),
    enabled: accessToken !== null,
  });

  const assessmentInvitationQuery = useQuery({
    queryKey: ["recruiter", "assessment-invitation", applicationId],
    queryFn: () => getApplicationAssessment(applicationId, token),
    enabled: accessToken !== null,
  });

  const assessmentsQuery = useQuery({
    queryKey: ["recruiter", "assessments"],
    queryFn: () => listAssessments(token),
    enabled: accessToken !== null,
  });

  const attemptsQuery = useQuery({
    queryKey: ["recruiter", "assessment-attempts", applicationId],
    queryFn: () => listApplicationAssessmentAttempts(applicationId, token),
    enabled: accessToken !== null,
  });

  const monitoringEventsQuery = useQuery({
    queryKey: ["recruiter", "assessment-events", applicationId],
    queryFn: () => listApplicationAssessmentEvents(applicationId, token),
    enabled: accessToken !== null && !!assessmentInvitationQuery.data,
  });

  const notesQuery = useQuery({
    queryKey: ["recruiter", "notes", applicationId],
    queryFn: () => listNotes(applicationId, token),
    enabled: accessToken !== null,
  });

  const statusMutation = useMutation({
    mutationFn: (status: ApplicationStatus) => changeApplicationStatus(applicationId, status, token),
    onSuccess: (_data, status) => {
      showToast(`Moved to ${humanizeStatus(status)}.`, "success");
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "applications", applicationId] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "applications"] });
    },
    onError: (err) => {
      showToast(err instanceof ApiError ? err.message : "Could not update the status.", "error");
    },
  });

  const overrideMutation = useMutation({
    mutationFn: () => overrideAiScreening(applicationId, overrideReason.trim(), token),
    onSuccess: () => {
      setOverrideReason("");
      showToast("AI decision overridden — moved to Under review.", "success");
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "applications"] });
    },
    onError: (err) => {
      showToast(err instanceof ApiError ? err.message : "Could not override the AI decision.", "error");
    },
  });

  const screeningMutation = useMutation({
    mutationFn: () => startScreening(applicationId, token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "screening", applicationId] });
    },
  });

  const inviteMutation = useMutation({
    mutationFn: () => inviteCandidate({ assessment_id: selectedAssessmentId, application_id: applicationId }, token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessment-invitation", applicationId] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessment-attempts", applicationId] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "applications", applicationId] });
    },
  });

  const retestMutation = useMutation({
    mutationFn: () =>
      retestCandidate(
        {
          application_id: applicationId,
          reason: retestReason.trim(),
          assessment_choice: retestChoice,
          assessment_id: retestChoice === "EXISTING" ? retestAssessmentId : undefined,
          new_assessment: retestChoice === "NEW" ? retestNewAssessment : undefined,
        },
        token,
      ),
    onSuccess: () => {
      // Not `closeRetestForm()`: it refuses to close while the mutation is
      // pending, and `onSuccess` runs before the mutation leaves that state.
      dismissRetestForm();
      showToast("Retest created — the new assessment link is ready.", "success");
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessment-invitation", applicationId] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessment-attempts", applicationId] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "applications", applicationId] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessments"] });
    },
    onError: (err) => {
      setRetestError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openRetestForm() {
    setRetestReason("");
    setRetestError(null);
    setRetestChoice("SAME");
    setRetestAssessmentId("");
    setRetestNewAssessment(emptyAssessmentFormValue());
    setShowRetestForm(true);
  }

  function dismissRetestForm() {
    setShowRetestForm(false);
    setRetestReason("");
    setRetestError(null);
  }

  /** User-initiated close (Cancel, Escape, backdrop): blocked while a request
   * is in flight so it can't be abandoned half-sent. */
  function closeRetestForm() {
    if (retestMutation.isPending) return;
    dismissRetestForm();
  }

  function handleRetestSubmit(event: FormEvent) {
    event.preventDefault();
    if (retestMutation.isPending) return;
    if (retestReason.trim().length === 0) {
      setRetestError("A reason for the retest is required.");
      return;
    }
    if (retestChoice === "EXISTING" && !retestAssessmentId) {
      setRetestError("Choose which existing assessment to use.");
      return;
    }
    if (retestChoice === "NEW" && retestNewAssessment.questions.length === 0) {
      setRetestError("Add at least one question to the new assessment.");
      return;
    }
    retestMutation.mutate();
  }

  const noteMutation = useMutation({
    mutationFn: () => createNote(applicationId, noteBody, token),
    onSuccess: () => {
      setNoteBody("");
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "notes", applicationId] });
    },
  });

  async function handleDownload() {
    if (!applicationQuery.data) return;
    setDownloadError(null);
    setDownloading(true);
    try {
      const { blob, filename } = await downloadResume(applicationId, token);
      triggerBlobDownload(blob, filename ?? applicationQuery.data.resume_filename ?? "resume");
    } catch (err) {
      setDownloadError(err instanceof ApiError ? err.message : "Could not download the resume.");
    } finally {
      setDownloading(false);
    }
  }

  function handleNoteSubmit(event: FormEvent) {
    event.preventDefault();
    if (noteBody.trim()) noteMutation.mutate();
  }

  if (applicationQuery.isPending) {
    return (
      <div className="stack-lg">
        <SkeletonLines count={6} />
      </div>
    );
  }
  if (applicationQuery.isError || !applicationQuery.data) {
    return (
      <Alert>
        {applicationQuery.error instanceof ApiError
          ? applicationQuery.error.message
          : "Application not found."}
      </Alert>
    );
  }

  const application = applicationQuery.data;
  const nextStatuses = APPLICATION_TRANSITIONS[application.status];
  const latestScreening = screeningQuery.data?.[0] ?? null;

  return (
    <div className="stack-lg">
      <BackLink to="/recruiter/applications">Back to applications</BackLink>

      <div className="page-header">
        <div>
          <h1>{application.job_title}</h1>
          <p className="muted">
            <Link to={`/recruiter/candidates/${application.candidate_id}`}>
              {application.candidate_full_name}
            </Link>{" "}
            · Applied {new Date(application.applied_at).toLocaleDateString()}
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <span className={toneBadgeClass(statusTone(application.status))}>{application.status}</span>
          {nextStatuses.length > 0 && (
            <select
              value=""
              disabled={statusMutation.isPending}
              onChange={(e) => {
                const next = e.target.value as ApplicationStatus;
                if (!next) return;
                if (STATUSES_REQUIRING_CONFIRMATION.includes(next)) {
                  setPendingStatus(next);
                } else {
                  statusMutation.mutate(next);
                }
              }}
            >
              <option value="">Move to…</option>
              {nextStatuses.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setEmailFeedback(null);
              setComposerTemplate("");
            }}
          >
            Send Email
          </button>
        </div>
      </div>

      {emailFeedback && <Alert variant="success">{emailFeedback}</Alert>}

      <section className="card pipeline-card" aria-label="Pipeline progress">
        <PipelineTrack status={application.status} />
      </section>

      {application.status === "AI_SCREENED_OUT" && (
        <section className="card ai-override-card" aria-labelledby="ai-override-title">
          <h2 id="ai-override-title">AI screened out — advisory only</h2>
          <p className="muted">
            The automatic screening at submission judged this resume not to match the role's
            requirements. AI screening is advisory: you have the final say. Review the evidence
            below, then either move the candidate forward or confirm the rejection from the status
            menu.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              overrideMutation.mutate();
            }}
          >
            <label className="field">
              <span>
                Reason for overriding <span className="required-mark" aria-hidden="true">*</span>
              </span>
              <textarea
                required
                rows={2}
                maxLength={1000}
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder="e.g. Relevant experience the resume under-describes"
              />
            </label>
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={overrideMutation.isPending || !overrideReason.trim()}
            >
              {overrideMutation.isPending ? <Spinner label="Overriding…" /> : "Override — move to review"}
            </button>
          </form>
        </section>
      )}

      <div className="detail-grid">
        <div className="stack-lg" style={{ gap: "1.5rem" }}>
          {/* Resume */}
          <section className="card">
            <h2>Resume</h2>
            {downloadError && <Alert>{downloadError}</Alert>}
            {application.resume_id ? (
              <div className="btn-group">
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setShowResumePreview(true)}>
                  Preview Resume
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => void handleDownload()}
                  disabled={downloading}
                >
                  {downloading ? "Downloading…" : `Download ${application.resume_filename ?? "resume"}`}
                </button>
              </div>
            ) : (
              <p className="muted">No resume on file for this application.</p>
            )}
          </section>

          {/* AI Screening */}
          <section className="card">
            <div className="page-header">
              <h2>AI Screening</h2>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={screeningMutation.isPending || !application.resume_id}
                onClick={() => screeningMutation.mutate()}
                title={!application.resume_id ? "This application has no resume to screen" : undefined}
              >
                {screeningMutation.isPending ? <Spinner label="Screening…" /> : "Run AI screening"}
              </button>
            </div>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              AI-assisted opinion for you to review — not an automatic decision.
            </p>

            {screeningQuery.isPending && <SkeletonLines count={2} />}

            {latestScreening && latestScreening.status === "FAILED" && (
              <Alert>{latestScreening.error_message ?? "Screening failed."}</Alert>
            )}

            {latestScreening && latestScreening.status === "COMPLETED" && (
              <div className="stack-lg" style={{ gap: "0.85rem" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                  <span className="stat-value">{latestScreening.overall_score}</span>
                  <span
                    className={`badge ${RECOMMENDATION_BADGE[latestScreening.recommendation ?? ""] ?? "badge-active"}`}
                  >
                    {latestScreening.recommendation?.replace(/_/g, " ")}
                  </span>
                  {latestScreening.decision && (
                    <span
                      className={`badge ${latestScreening.decision === "MATCH" ? "badge-active" : "badge-warn"}`}
                    >
                      {latestScreening.decision === "MATCH" ? "Match" : "Not a match"}
                    </span>
                  )}
                  {latestScreening.requested_by_user_id === null && (
                    <span className="chip">Automatic at submission</span>
                  )}
                </div>
                <p>{latestScreening.summary}</p>
                {(latestScreening.matched_requirements?.length ?? 0) > 0 && (
                  <div>
                    <strong>Requirements evidenced</strong>
                    <ul>
                      {latestScreening.matched_requirements?.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {(latestScreening.missing_requirements?.length ?? 0) > 0 && (
                  <div>
                    <strong>Requirements not evidenced</strong>
                    <ul>
                      {latestScreening.missing_requirements?.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {(latestScreening.matching_skills?.length ?? 0) > 0 && (
                  <p>
                    <strong>Matching skills:</strong> {latestScreening.matching_skills?.join(", ")}
                  </p>
                )}
                {(latestScreening.missing_skills?.length ?? 0) > 0 && (
                  <p>
                    <strong>Missing skills:</strong> {latestScreening.missing_skills?.join(", ")}
                  </p>
                )}
                {(latestScreening.strengths?.length ?? 0) > 0 && (
                  <div>
                    <strong>Strengths</strong>
                    <ul>
                      {latestScreening.strengths?.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {(latestScreening.concerns?.length ?? 0) > 0 && (
                  <div>
                    <strong>Concerns</strong>
                    <ul>
                      {latestScreening.concerns?.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <p className="muted" style={{ fontSize: "0.75rem" }}>
                  {latestScreening.provider} · {latestScreening.model} ·{" "}
                  {new Date(latestScreening.created_at).toLocaleString()}
                </p>
              </div>
            )}

            {!latestScreening && screeningQuery.isSuccess && (
              <p className="muted">No screening run yet.</p>
            )}
          </section>

          {/* Assessment */}
          <section className="card">
            <h2>Assessment</h2>
            {assessmentInvitationQuery.isPending && <SkeletonLines count={2} />}
            {assessmentInvitationQuery.isSuccess && !assessmentInvitationQuery.data && (
              <div className="stack-lg" style={{ gap: "0.75rem" }}>
                <p className="muted">
                  No assessment assigned yet. Assigning one only prepares the invitation — nothing is
                  emailed until you send it.
                </p>
                <div className="field-row" style={{ alignItems: "end" }}>
                  <label className="field" style={{ marginBottom: 0 }}>
                    <span>Reuse existing assessment</span>
                    <select
                      value={selectedAssessmentId}
                      onChange={(e) => setSelectedAssessmentId(e.target.value)}
                    >
                      <option value="">Select an assessment…</option>
                      {assessmentsQuery.data?.filter((a) => a.status !== "DRAFT").map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!selectedAssessmentId || inviteMutation.isPending}
                    onClick={() => inviteMutation.mutate()}
                  >
                    {inviteMutation.isPending ? <Spinner label="Assigning…" /> : "Assign assessment"}
                  </button>
                </div>
                <p className="field-hint">
                  Don't have one yet?{" "}
                  <a href="/recruiter/assessments" target="_blank" rel="noreferrer">
                    + Create new assessment
                  </a>{" "}
                  in a new tab, then come back and select it above.
                </p>
                {inviteMutation.isError && (
                  <Alert>
                    {inviteMutation.error instanceof ApiError
                      ? inviteMutation.error.message
                      : "Could not assign the assessment."}
                  </Alert>
                )}
              </div>
            )}
            {assessmentInvitationQuery.data && (
              <div className="stack-lg" style={{ gap: "0.75rem" }}>
                <p>
                  <strong>{assessmentInvitationQuery.data.assessment_title}</strong> —{" "}
                  <span className="badge badge-active">{assessmentInvitationQuery.data.status}</span>{" "}
                  {assessmentInvitationQuery.data.emailed_at ? (
                    <span className="badge badge-active">
                      Emailed {new Date(assessmentInvitationQuery.data.emailed_at).toLocaleDateString()}
                    </span>
                  ) : (
                    <span className="badge badge-warn">Not emailed yet</span>
                  )}
                </p>
                {assessmentInvitationQuery.data.status === "SENT" && (
                  <div>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() => {
                        setEmailFeedback(null);
                        setComposerTemplate("ASSESSMENT_INVITATION");
                      }}
                    >
                      {assessmentInvitationQuery.data.emailed_at
                        ? "Resend Assessment Invitation"
                        : "Send Assessment Invitation"}
                    </button>
                    <p className="field-hint" style={{ marginTop: "0.4rem" }}>
                      Sending issues a fresh personal link for the candidate; any link emailed before
                      stops working.
                    </p>
                  </div>
                )}
                {(inviteMutation.data?.invitation_link || retestMutation.data?.invitation_link) && (
                  <Alert variant="success">
                    Assessment assigned — the invitation is prepared but has not been emailed. If you
                    would rather share the link yourself instead of emailing it, copy it now (it is
                    not shown again):{" "}
                    <code>
                      {retestMutation.data?.invitation_link ?? inviteMutation.data?.invitation_link}
                    </code>
                  </Alert>
                )}

                {attemptsQuery.data && attemptsQuery.data.length > 0 && (
                  <div className="stack-sm">
                    <strong style={{ fontSize: "0.85rem" }}>Assessment attempts</strong>
                    {attemptsQuery.data.map((attempt) => (
                      <div key={attempt.id} className="timeline-item">
                        <span className="timeline-dot" />
                        <div>
                          <div>
                            {attempt.attempt_number === 1 ? "Attempt #1" : `Retest #${attempt.attempt_number - 1}`}
                            {attempt.result && (
                              <>
                                {" "}
                                — Score: {attempt.result.percentage}%
                                {attempt.result.passed === null ? " so far" : ""} —{" "}
                                <span className={`badge ${resultBadge(attempt.result.passed)}`}>
                                  {resultLabel(attempt.result.passed)}
                                </span>{" "}
                                <Link to={`/recruiter/assessments/attempts/${attempt.id}`}>
                                  {attempt.result.passed === null ? "Grade answers" : "Review answers"}
                                </Link>
                              </>
                            )}
                            {!attempt.result && (
                              <span className="badge badge-inactive" style={{ marginLeft: "0.5rem" }}>
                                {attempt.status}
                              </span>
                            )}
                          </div>
                          <div className="muted" style={{ fontSize: "0.75rem" }}>
                            {attempt.submitted_at
                              ? new Date(attempt.submitted_at).toLocaleString()
                              : new Date(attempt.expires_at).toLocaleDateString() + " (not yet submitted)"}
                            {attempt.retest_reason && <> · Reason: {attempt.retest_reason}</>}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {monitoringEventsQuery.data && monitoringEventsQuery.data.length > 0 && (
                  <div className="stack-sm">
                    <strong style={{ fontSize: "0.85rem" }}>Assessment Activity</strong>
                    <p className="field-hint">
                      Browser-observed events during this attempt — not an accusation, just what
                      was detected.
                    </p>
                    <div className="timeline">
                      {monitoringEventsQuery.data.map((event) => (
                        <div key={event.id} className="timeline-item">
                          <span className="timeline-dot" />
                          <div>
                            {new Date(event.occurred_at).toLocaleTimeString()} —{" "}
                            {MONITORING_EVENT_LABELS[event.event_type]}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {assessmentInvitationQuery.data.status === "SUBMITTED" && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    style={{ alignSelf: "flex-start" }}
                    onClick={openRetestForm}
                  >
                    Give Retest
                  </button>
                )}
              </div>
            )}
          </section>
        </div>

        {/* Notes */}
        <section className="card">
          <h2>Notes</h2>
          <form onSubmit={handleNoteSubmit} noValidate style={{ marginBottom: "1rem" }}>
            <label className="field">
              <span>Add a note</span>
              <textarea
                rows={3}
                value={noteBody}
                onChange={(e) => setNoteBody(e.target.value)}
                disabled={noteMutation.isPending}
              />
            </label>
            <button
              type="submit"
              className="btn btn-ghost btn-sm"
              disabled={noteMutation.isPending || !noteBody.trim()}
            >
              {noteMutation.isPending ? "Saving…" : "Add note"}
            </button>
          </form>

          {notesQuery.isPending && <SkeletonLines count={2} />}
          {notesQuery.isSuccess && notesQuery.data.length === 0 && (
            <p className="muted">No notes yet.</p>
          )}
          {notesQuery.isSuccess && notesQuery.data.length > 0 && (
            <div className="timeline">
              {notesQuery.data.map((note) => (
                <div key={note.id} className="timeline-item">
                  <span className="timeline-dot" />
                  <div>
                    <div>{note.body}</div>
                    <div className="muted" style={{ fontSize: "0.75rem" }}>
                      {note.author_name} · {new Date(note.created_at).toLocaleString()}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {pendingStatus && (
        <ConfirmDialog
          title="Reject this application?"
          message={`This marks ${application.candidate_full_name}'s application for ${application.job_title} as rejected. This can't be undone.`}
          confirmLabel="Reject application"
          isConfirming={statusMutation.isPending}
          onCancel={() => setPendingStatus(null)}
          onConfirm={() =>
            statusMutation.mutate(pendingStatus, { onSuccess: () => setPendingStatus(null) })
          }
        />
      )}

      {composerTemplate !== null && (
        <EmailComposerModal
          applicationId={applicationId}
          candidateName={application.candidate_full_name}
          candidateEmail={application.candidate_email}
          accessToken={token}
          initialTemplate={composerTemplate === "" ? undefined : composerTemplate}
          onClose={() => setComposerTemplate(null)}
          onSent={(result) => {
            setComposerTemplate(null);
            setEmailFeedback(`Email sent to ${result.to}: "${result.subject}".`);
            void queryClient.invalidateQueries({
              queryKey: ["recruiter", "assessment-invitation", applicationId],
            });
            void queryClient.invalidateQueries({
              queryKey: ["recruiter", "assessment-attempts", applicationId],
            });
            void queryClient.invalidateQueries({ queryKey: ["recruiter", "activities"] });
          }}
        />
      )}

      {showRetestForm && assessmentInvitationQuery.data && (
        <Modal title="Give a retest" onClose={closeRetestForm} wide={retestChoice === "NEW"}>
          <form onSubmit={handleRetestSubmit}>
            <div className="stack-sm" style={{ marginBottom: "1rem" }}>
              <div className="detail-row">
                <span className="detail-row-label">Candidate</span>
                <span>{application.candidate_full_name}</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Application</span>
                <span>{application.job_title}</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Previous assessment</span>
                <span>{assessmentInvitationQuery.data.assessment_title}</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Previous attempt</span>
                <span>#{assessmentInvitationQuery.data.attempt_number}</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Previous result</span>
                <span>
                  {assessmentInvitationQuery.data.result ? (
                    <>
                      {assessmentInvitationQuery.data.result.percentage}% —{" "}
                      <span className={`badge ${resultBadge(assessmentInvitationQuery.data.result.passed)}`}>
                        {resultLabel(assessmentInvitationQuery.data.result.passed)}
                      </span>
                    </>
                  ) : (
                    "—"
                  )}
                </span>
              </div>
            </div>

            <label className="field">
              <span>Reason for retest</span>
              <textarea
                required
                rows={2}
                value={retestReason}
                onChange={(e) => {
                  setRetestReason(e.target.value);
                  if (retestError) setRetestError(null);
                }}
                disabled={retestMutation.isPending}
                placeholder="e.g. Candidate experienced network interruption."
              />
            </label>

            <fieldset className="field">
              <legend>Assessment for this retest</legend>
              <div className="stack-sm">
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <input
                    type="radio"
                    name="retest-choice"
                    checked={retestChoice === "SAME"}
                    onChange={() => setRetestChoice("SAME")}
                    disabled={retestMutation.isPending}
                    style={{ width: "auto" }}
                  />
                  Same assessment ({assessmentInvitationQuery.data.assessment_title})
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <input
                    type="radio"
                    name="retest-choice"
                    checked={retestChoice === "EXISTING"}
                    onChange={() => setRetestChoice("EXISTING")}
                    disabled={retestMutation.isPending}
                    style={{ width: "auto" }}
                  />
                  Another existing assessment
                </label>
                {retestChoice === "EXISTING" && (
                  <select
                    value={retestAssessmentId}
                    onChange={(e) => setRetestAssessmentId(e.target.value)}
                    disabled={retestMutation.isPending}
                    style={{ marginLeft: "1.6rem" }}
                  >
                    <option value="">Select an assessment…</option>
                    {assessmentsQuery.data?.filter((a) => a.status !== "DRAFT").map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.title}
                      </option>
                    ))}
                  </select>
                )}
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <input
                    type="radio"
                    name="retest-choice"
                    checked={retestChoice === "NEW"}
                    onChange={() => setRetestChoice("NEW")}
                    disabled={retestMutation.isPending}
                    style={{ width: "auto" }}
                  />
                  Create a new assessment
                </label>
              </div>
            </fieldset>

            {retestChoice === "NEW" && (
              <section className="card" style={{ background: "var(--color-bg)", marginTop: "0.75rem" }}>
                <AssessmentForm
                  value={retestNewAssessment}
                  onChange={setRetestNewAssessment}
                  disabled={retestMutation.isPending}
                  accessToken={token}
                />
              </section>
            )}

            {retestError && <Alert>{retestError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={retestMutation.isPending}>
                {retestMutation.isPending ? <Spinner label="Sending…" /> : "Send Retest"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeRetestForm}
                disabled={retestMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showResumePreview && (
        <ResumePreviewModal
          filename={application.resume_filename ?? "resume"}
          fetchResume={() => downloadResume(applicationId, token)}
          onClose={() => setShowResumePreview(false)}
        />
      )}
    </div>
  );
}
