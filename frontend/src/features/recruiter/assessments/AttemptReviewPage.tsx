import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { BackLink } from "../../../shared/components/BackLink";
import { SkeletonLines } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import { QUESTION_TYPE_LABELS, isChoiceType, type AttemptAnswerView } from "../../../types/assessment";
import { useAuth } from "../../auth/AuthContext";
import { getAttemptReview, gradeAttempt } from "./api";

interface Draft {
  marks: string;
  comment: string;
}

/**
 * Staff-only review of one submitted attempt: each answer next to the
 * answer key, with manual grading for short-answer, descriptive and coding
 * answers. Marks are bounded by the question's maximum here and again on
 * the server; pass/fail appears only once every answer has marks.
 */
export function AttemptReviewPage() {
  const { invitationId = "" } = useParams<{ invitationId: string }>();
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const canGrade = user?.roles.some((r) => ["ORG_ADMIN", "RECRUITER", "HIRING_MANAGER"].includes(r)) ?? false;
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const reviewKey = ["recruiter", "assessments", "attempt", invitationId];
  const reviewQuery = useQuery({
    queryKey: reviewKey,
    queryFn: () => getAttemptReview(invitationId, token),
    enabled: accessToken !== null && invitationId !== "",
  });

  const gradeMutation = useMutation({
    mutationFn: () => {
      const answers = reviewQuery.data?.answers ?? [];
      const grades = answers
        .filter((a) => drafts[a.question_id]?.marks !== undefined && drafts[a.question_id].marks !== "")
        .map((a) => ({
          question_id: a.question_id,
          awarded_points: Number(drafts[a.question_id].marks),
          comment: drafts[a.question_id].comment.trim() || null,
        }));
      return gradeAttempt(invitationId, grades, token);
    },
    onSuccess: (review) => {
      queryClient.setQueryData(reviewKey, review);
      setDrafts({});
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "assessment-attempts", review.application_id] });
      showToast(
        review.result?.grading_status === "FINAL"
          ? `Grading complete: ${review.result.percentage}% — ${review.result.passed ? "passed" : "failed"}.`
          : "Marks saved. Some answers still need grading.",
        "success",
      );
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  function setDraft(answer: AttemptAnswerView, fields: Partial<Draft>) {
    setDrafts((current) => ({
      ...current,
      [answer.question_id]: {
        marks: current[answer.question_id]?.marks ?? "",
        comment: current[answer.question_id]?.comment ?? answer.grader_comment ?? "",
        ...fields,
      },
    }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const answers = reviewQuery.data?.answers ?? [];
    for (const answer of answers) {
      const draft = drafts[answer.question_id];
      if (!draft || draft.marks === "") continue;
      const marks = Number(draft.marks);
      if (!Number.isInteger(marks) || marks < 0 || marks > answer.points) {
        setFormError(`Question ${answer.order + 1}: marks must be a whole number from 0 to ${answer.points}.`);
        return;
      }
    }
    if (!Object.values(drafts).some((d) => d.marks !== "")) {
      setFormError("Enter marks for at least one answer.");
      return;
    }
    gradeMutation.mutate();
  }

  if (reviewQuery.isPending) return <SkeletonLines count={8} />;
  if (reviewQuery.isError || !reviewQuery.data) {
    return (
      <Alert>
        {reviewQuery.error instanceof ApiError ? reviewQuery.error.message : "Could not load this attempt."}
      </Alert>
    );
  }

  const review = reviewQuery.data;
  const result = review.result;
  const pending = review.answers.filter((a) => a.needs_manual_grading).length;

  return (
    <div className="stack-lg">
      <p>
        <BackLink to={`/recruiter/applications/${review.application_id}`}>Back to the application</BackLink>
      </p>
      <div className="page-header">
        <div>
          <h1>{review.assessment_title}</h1>
          <p className="muted">
            {review.candidate_full_name} · attempt {review.attempt_number}
            {review.submitted_at ? ` · submitted ${new Date(review.submitted_at).toLocaleString()}` : ""}
          </p>
        </div>
        {result && (
          <div className="card" style={{ padding: "0.75rem 1rem" }} aria-live="polite">
            <strong>
              {result.score} / {result.max_score} ({result.percentage}%)
            </strong>
            <div>
              {result.grading_status === "FINAL" ? (
                <span className={`badge ${result.passed ? "badge-active" : "badge-danger"}`}>
                  {result.passed ? "PASSED" : "FAILED"}
                </span>
              ) : (
                <span className="badge badge-warn">{pending} answer(s) awaiting grading</span>
              )}
            </div>
          </div>
        )}
      </div>

      <form onSubmit={submit} className="stack-lg">
        {review.answers.map((answer) => {
          const selected = new Set(answer.selected_option_ids);
          const draft = drafts[answer.question_id];
          return (
            <section key={answer.question_id} className="card stack-sm" aria-labelledby={`q-${answer.question_id}`}>
              <h2 id={`q-${answer.question_id}`} style={{ fontSize: "1rem" }}>
                {answer.order + 1}. {answer.prompt}
              </h2>
              <p className="muted">
                {QUESTION_TYPE_LABELS[answer.type]} · {answer.points} mark(s)
                {answer.negative_points ? ` · −${answer.negative_points} for a wrong answer` : ""}
              </p>

              {isChoiceType(answer.type) ? (
                <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
                  {answer.options.map((option) => (
                    <li key={option.id}>
                      {option.label}
                      {option.is_correct && <strong> ✓ correct</strong>}
                      {selected.has(option.id) && <em> — candidate's choice</em>}
                    </li>
                  ))}
                </ul>
              ) : (
                <>
                  <div>
                    <strong>Candidate's answer</strong>
                    {answer.text_answer ? (
                      <pre className="code-answer" style={{ whiteSpace: "pre-wrap", margin: "0.35rem 0 0" }}>
                        {answer.text_answer}
                      </pre>
                    ) : (
                      <p className="muted">Not answered.</p>
                    )}
                  </div>
                  {answer.accepted_answers.length > 0 && (
                    <p>
                      <strong>Accepted answers:</strong> {answer.accepted_answers.join(" · ")}
                    </p>
                  )}
                  {answer.grading_notes && (
                    <p>
                      <strong>Grading notes:</strong> {answer.grading_notes}
                    </p>
                  )}
                </>
              )}
              {answer.explanation && (
                <p className="field-hint">
                  <strong>Explanation:</strong> {answer.explanation}
                </p>
              )}

              <p>
                <strong>Marks:</strong>{" "}
                {answer.awarded_points === null ? (
                  answer.answer_id ? (
                    <span className="badge badge-warn">Needs grading</span>
                  ) : (
                    "0 (not answered)"
                  )
                ) : (
                  <>
                    {answer.awarded_points} / {answer.points}
                    {answer.grading_method === "MANUAL" && answer.graded_by_name
                      ? ` · graded by ${answer.graded_by_name}`
                      : answer.grading_method === "AUTO"
                        ? " · graded automatically"
                        : ""}
                  </>
                )}
              </p>
              {answer.grader_comment && <p className="muted">Grader comment: {answer.grader_comment}</p>}

              {canGrade && answer.manually_gradable && (
                <div className="field-row">
                  <label className="field" style={{ maxWidth: "10rem" }}>
                    <span>{answer.awarded_points === null ? "Award marks" : "Change marks"}</span>
                    <input
                      type="number"
                      min={0}
                      max={answer.points}
                      step={1}
                      value={draft?.marks ?? ""}
                      onChange={(e) => setDraft(answer, { marks: e.target.value })}
                      disabled={gradeMutation.isPending}
                    />
                  </label>
                  <label className="field">
                    <span>
                      Comment <span className="optional-mark">(Optional, staff only)</span>
                    </span>
                    <input
                      value={draft?.comment ?? answer.grader_comment ?? ""}
                      onChange={(e) => setDraft(answer, { comment: e.target.value })}
                      disabled={gradeMutation.isPending}
                    />
                  </label>
                </div>
              )}
            </section>
          );
        })}

        {formError && <Alert>{formError}</Alert>}
        {canGrade && review.answers.some((a) => a.manually_gradable) && (
          <div className="btn-group">
            <button type="submit" className="btn btn-primary" disabled={gradeMutation.isPending}>
              {gradeMutation.isPending ? <Spinner label="Saving…" /> : "Save marks"}
            </button>
            <Link className="btn btn-ghost" to={`/recruiter/applications/${review.application_id}`}>
              Done
            </Link>
          </div>
        )}
      </form>
    </div>
  );
}
