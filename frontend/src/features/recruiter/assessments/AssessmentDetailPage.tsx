import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { BackLink } from "../../../shared/components/BackLink";
import { SkeletonLines } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import { Modal } from "../../../shared/components/Modal";
import {
  QUESTION_TYPE_LABELS,
  isChoiceType,
  type AssessmentCreateRequest,
  type AssessmentResponse,
} from "../../../types/assessment";
import { useAuth } from "../../auth/AuthContext";
import { AssessmentForm } from "./AssessmentForm";
import { changeAssessmentStatus, getAssessment, previewAssessment, updateAssessment } from "./api";

function toFormValue(assessment: AssessmentResponse): AssessmentCreateRequest {
  return {
    title: assessment.title,
    instructions: assessment.instructions,
    duration_minutes: assessment.duration_minutes,
    pass_score: assessment.pass_score,
    questions: assessment.questions.map((q) => ({
      prompt: q.prompt,
      type: q.type,
      points: q.points,
      negative_points: q.negative_points ?? 0,
      options: q.options.map((o) => ({ label: o.label, is_correct: o.is_correct })),
      accepted_answers: q.accepted_answers ?? [],
      case_sensitive: q.case_sensitive ?? false,
      grading_notes: q.grading_notes ?? null,
      explanation: q.explanation ?? null,
      // Re-saving keeps the exact bank snapshot: the server copies the
      // same version again rather than trusting edited content.
      bank_version_id: q.source_bank_version_id ?? null,
    })),
  };
}

export function AssessmentDetailPage() {
  const { assessmentId = "" } = useParams<{ assessmentId: string }>();
  const { accessToken } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const assessmentQuery = useQuery({
    queryKey: ["recruiter", "assessments", assessmentId],
    queryFn: () => getAssessment(assessmentId, token),
    enabled: accessToken !== null && assessmentId !== "",
  });

  const [editing, setEditing] = useState(false);
  // Only meaningful while `editing` is true — `startEditing`/`cancelEditing`
  // set it from the latest server data in the same event handler that
  // flips `editing`, so it's never stale or out of sync on entry.
  const [formValue, setFormValue] = useState<AssessmentCreateRequest | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const canManage = !(assessmentQuery.error instanceof ApiError && assessmentQuery.error.status === 403);
  const [showPreview, setShowPreview] = useState(false);
  const previewQuery = useQuery({
    queryKey: ["recruiter", "assessments", assessmentId, "preview"],
    queryFn: () => previewAssessment(assessmentId, token),
    enabled: showPreview,
  });

  const statusMutation = useMutation({
    mutationFn: (status: "DRAFT" | "PUBLISHED") => changeAssessmentStatus(assessmentId, status, token),
    onSuccess: (updated) => {
      showToast(updated.status === "PUBLISHED" ? "Assessment published." : "Moved back to draft.", "success");
      queryClient.setQueryData(["recruiter", "assessments", assessmentId], updated);
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessments"] });
    },
    onError: (err) => showToast(err instanceof ApiError ? err.message : "Could not change the status.", "error"),
  });

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!formValue || !assessmentQuery.data) throw new Error("Nothing to save.");
      const locked = assessmentQuery.data.has_invitations;
      return updateAssessment(
        assessmentId,
        {
          title: formValue.title,
          instructions: formValue.instructions,
          duration_minutes: formValue.duration_minutes,
          pass_score: formValue.pass_score,
          // Omitted entirely when locked — sending an unchanged question
          // set would still trip the backend's has-invitations guard.
          ...(locked ? {} : { questions: formValue.questions }),
        },
        token,
      );
    },
    onSuccess: (updated) => {
      showToast("Assessment updated.", "success");
      setEditing(false);
      queryClient.setQueryData(["recruiter", "assessments", assessmentId], updated);
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessments"] });
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function startEditing() {
    if (assessmentQuery.data) setFormValue(toFormValue(assessmentQuery.data));
    setFormError(null);
    setEditing(true);
  }

  function cancelEditing() {
    if (assessmentQuery.data) setFormValue(toFormValue(assessmentQuery.data));
    setFormError(null);
    setEditing(false);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    saveMutation.mutate();
  }

  return (
    <div className="stack-lg">
      <p>
        <BackLink to="/recruiter/assessments">Back to assessments</BackLink>
      </p>

      {assessmentQuery.isPending && <SkeletonLines count={6} />}
      {assessmentQuery.isError && !canManage && (
        <Alert>You do not have permission to view this assessment.</Alert>
      )}
      {assessmentQuery.isError && canManage && (
        <Alert>
          {assessmentQuery.error instanceof ApiError
            ? assessmentQuery.error.message
            : "Could not load this assessment."}
        </Alert>
      )}

      {assessmentQuery.isSuccess && (
        <>
          <div className="page-header">
            <div>
              <h1>{assessmentQuery.data.title}</h1>
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.35rem" }}>
                <span
                  className={`badge ${
                    assessmentQuery.data.deleted_at || assessmentQuery.data.status === "DRAFT"
                      ? "badge-inactive"
                      : "badge-active"
                  }`}
                >
                  {assessmentQuery.data.deleted_at
                    ? "Archived"
                    : assessmentQuery.data.status === "DRAFT"
                      ? "Draft"
                      : "Published"}
                </span>
                {assessmentQuery.data.has_invitations && (
                  <span className="badge badge-warn" title="Questions are locked because this assessment has been sent to a candidate">
                    In use — questions locked
                  </span>
                )}
              </div>
            </div>
            <div className="btn-group">
              <button type="button" className="btn btn-ghost" onClick={() => setShowPreview(true)}>
                Preview as candidate
              </button>
              {canManage && !editing && !assessmentQuery.data.deleted_at && (
                <>
                  {assessmentQuery.data.status === "DRAFT" ? (
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={statusMutation.isPending}
                      onClick={() => statusMutation.mutate("PUBLISHED")}
                    >
                      Publish
                    </button>
                  ) : (
                    !assessmentQuery.data.has_invitations && (
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={statusMutation.isPending}
                        onClick={() => statusMutation.mutate("DRAFT")}
                      >
                        Back to draft
                      </button>
                    )
                  )}
                  <button type="button" className="btn btn-primary" onClick={startEditing}>
                    Edit
                  </button>
                </>
              )}
            </div>
          </div>

          {!editing ? (
            <section className="card stack-lg" style={{ gap: "1rem" }}>
              <div className="detail-row">
                <span className="detail-row-label">Instructions</span>
                <span>{assessmentQuery.data.instructions}</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Duration</span>
                <span>{assessmentQuery.data.duration_minutes} minutes</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Pass score</span>
                <span>{assessmentQuery.data.pass_score}%</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Created</span>
                <span>{new Date(assessmentQuery.data.created_at).toLocaleString()}</span>
              </div>
              <div className="detail-row">
                <span className="detail-row-label">Last updated</span>
                <span>{new Date(assessmentQuery.data.updated_at).toLocaleString()}</span>
              </div>

              <div>
                <h2>Questions ({assessmentQuery.data.questions.length})</h2>
                <div className="stack-lg" style={{ gap: "0.75rem" }}>
                  {assessmentQuery.data.questions.map((question, index) => (
                    <div key={question.id} className="card" style={{ background: "var(--color-bg)" }}>
                      <p>
                        <strong>
                          {index + 1}. {question.prompt}
                        </strong>{" "}
                        <span className="muted">
                          ({QUESTION_TYPE_LABELS[question.type]}, {question.points} pt
                          {question.negative_points ? `, −${question.negative_points} if wrong` : ""})
                          {question.source_bank_version_id ? " · from question bank" : ""}
                        </span>
                      </p>
                      {isChoiceType(question.type) ? (
                        <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
                          {question.options.map((option) => (
                            <li key={option.id}>
                              {option.label} {option.is_correct && <span aria-label="Correct answer">✓</span>}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="muted" style={{ margin: 0 }}>
                          {question.accepted_answers?.length
                            ? `Accepted answers: ${question.accepted_answers.join(" · ")}`
                            : "Graded manually"}
                          {question.grading_notes ? ` — Notes: ${question.grading_notes}` : ""}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </section>
          ) : formValue ? (
            <section className="card">
              <form onSubmit={handleSubmit}>
                <AssessmentForm
                  value={formValue}
                  onChange={setFormValue}
                  disabled={saveMutation.isPending}
                  questionsLocked={assessmentQuery.data.has_invitations}
                  accessToken={token}
                />

                {formError && <Alert>{formError}</Alert>}

                <div className="btn-group" style={{ marginTop: "1.5rem" }}>
                  <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                    {saveMutation.isPending ? <Spinner label="Saving…" /> : "Save changes"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={cancelEditing}
                    disabled={saveMutation.isPending}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            </section>
          ) : null}
        </>
      )}

      {showPreview && (
        <Modal title="Candidate preview" onClose={() => setShowPreview(false)} wide>
          {previewQuery.isPending && <p role="status">Loading…</p>}
          {previewQuery.isError && <Alert>Could not load the preview.</Alert>}
          {previewQuery.isSuccess && (
            <div className="stack-sm">
              <p className="muted">
                {previewQuery.data.duration_minutes} minutes · {previewQuery.data.instructions}
              </p>
              <ol className="stack-sm">
                {previewQuery.data.questions.map((q) => (
                  <li key={q.id}>
                    <strong>{q.prompt}</strong> <span className="muted">({q.points} mark(s))</span>
                    {q.options.length > 0 ? (
                      <ul>
                        {q.options.map((o) => (
                          <li key={o.id}>{o.label}</li>
                        ))}
                      </ul>
                    ) : (
                      <textarea rows={q.type === "SHORT_ANSWER" ? 1 : 4} disabled aria-label="Answer box" />
                    )}
                  </li>
                ))}
              </ol>
              <p className="field-hint">Exactly what candidates see — no answer key or grading notes.</p>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
