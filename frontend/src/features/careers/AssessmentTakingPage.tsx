import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { Icon } from "../../shared/components/Icon";
import type { MonitoringEventCreate } from "../../types/assessment";
import { PublicHeader } from "../public/PublicHeader";
import { MonitoringConsentScreen } from "./MonitoringConsentScreen";
import { SubmissionSuccess } from "./SubmissionSuccess";
import { useProctoring } from "./useProctoring";
import { getAssessmentInvitation, sendMonitoringEvents, startAssessment, submitAssessment } from "./api";

/** Best-effort permission read — a combined audio+video request can't
 * distinguish which device was denied, so both flags fall back together;
 * documented in docs/assessment.md. */
async function checkCameraAndMicrophone(): Promise<{ camera: boolean; microphone: boolean }> {
  if (!navigator.mediaDevices?.getUserMedia) return { camera: false, microphone: false };
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    stream.getTracks().forEach((track) => track.stop());
    return { camera: true, microphone: true };
  } catch {
    return { camera: false, microphone: false };
  }
}

export function AssessmentTakingPage() {
  const { token = "" } = useParams<{ token: string }>();
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, string[]>>({});

  const invitationQuery = useQuery({
    queryKey: ["public", "assessment", token],
    queryFn: () => getAssessmentInvitation(token),
  });

  useProctoring(token, invitationQuery.data?.status === "STARTED");

  const startMutation = useMutation({
    mutationFn: () => startAssessment(token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["public", "assessment", token] });
      void (async () => {
        const events: MonitoringEventCreate[] = [
          { event_type: "MONITORING_CONSENT_GIVEN", occurred_at: new Date().toISOString() },
        ];
        const { camera, microphone } = await checkCameraAndMicrophone();
        if (!camera) events.push({ event_type: "CAMERA_UNAVAILABLE", occurred_at: new Date().toISOString() });
        if (!microphone) {
          events.push({ event_type: "MICROPHONE_UNAVAILABLE", occurred_at: new Date().toISOString() });
        }
        await sendMonitoringEvents(token, events).catch(() => {
          // Best-effort — never blocks the candidate from proceeding.
        });
      })();
    },
  });

  function handleAcceptMonitoring() {
    startMutation.mutate();
  }

  const submitMutation = useMutation({
    mutationFn: () =>
      submitAssessment(
        token,
        Object.entries(answers).map(([question_id, selected_option_ids]) => ({
          question_id,
          selected_option_ids,
        })),
      ),
  });

  const answeredCount = (invitationQuery.data?.questions ?? []).filter(
    (question) => (answers[question.id] ?? []).length > 0,
  ).length;

  function toggleOption(questionId: string, optionId: string, multi: boolean) {
    setAnswers((current) => {
      const existing = current[questionId] ?? [];
      if (multi) {
        const next = existing.includes(optionId)
          ? existing.filter((id) => id !== optionId)
          : [...existing, optionId];
        return { ...current, [questionId]: next };
      }
      return { ...current, [questionId]: [optionId] };
    });
  }

  return (
    <div className="public-page assess-page">
      <PublicHeader title={invitationQuery.data?.organization_name ?? "Careers"} />
      <div className="public-shell assess-shell">
        {invitationQuery.isPending && <p role="status">Loading assessment…</p>}
        {invitationQuery.isError && (
          <Alert>
            {invitationQuery.error instanceof ApiError
              ? invitationQuery.error.message
              : "This invitation link is no longer valid."}
          </Alert>
        )}

        {invitationQuery.isSuccess && submitMutation.isSuccess && <SubmissionSuccess />}

        {invitationQuery.isSuccess && !submitMutation.isSuccess && (
          <>
            <h1>{invitationQuery.data.assessment_title}</h1>
            <p className="muted">
              {invitationQuery.data.job_title} at {invitationQuery.data.organization_name}
            </p>
            {invitationQuery.data.status !== "SUBMITTED" && (
              <div className="assess-facts">
                <span className="chip">
                  <Icon name="clock" size={13} />
                  {invitationQuery.data.duration_minutes} minutes
                </span>
                <span className="chip">
                  <Icon name="assessments" size={13} />
                  {invitationQuery.data.questions.length} question
                  {invitationQuery.data.questions.length === 1 ? "" : "s"}
                </span>
              </div>
            )}

            {invitationQuery.data.status === "SUBMITTED" && (
              <Alert variant="success">You've already submitted this assessment. Thank you!</Alert>
            )}

            {invitationQuery.data.status === "SENT" && (
              <div className="stack-lg">
                <section className="card">
                  <p>{invitationQuery.data.instructions}</p>
                  <p className="muted">
                    Duration: {invitationQuery.data.duration_minutes} minutes ·{" "}
                    {invitationQuery.data.questions.length} question(s)
                  </p>
                </section>
                {startMutation.isError && (
                  <Alert>
                    {startMutation.error instanceof ApiError
                      ? startMutation.error.message
                      : "Could not start the assessment."}
                  </Alert>
                )}
                <MonitoringConsentScreen
                  onAccept={handleAcceptMonitoring}
                  isStarting={startMutation.isPending}
                />
              </div>
            )}

            {invitationQuery.data.status === "STARTED" && (
              <section className="stack-lg">
                <p className="muted">{invitationQuery.data.instructions}</p>
                <div
                  className="assess-progress"
                  role="progressbar"
                  aria-label="Questions answered"
                  aria-valuemin={0}
                  aria-valuemax={invitationQuery.data.questions.length}
                  aria-valuenow={answeredCount}
                  aria-valuetext={`${answeredCount} of ${invitationQuery.data.questions.length} answered`}
                >
                  <span className="assess-progress-label">
                    {answeredCount} of {invitationQuery.data.questions.length} answered
                  </span>
                  <span className="assess-progress-track" aria-hidden="true">
                    <span
                      className="assess-progress-fill"
                      style={{
                        width: `${
                          invitationQuery.data.questions.length === 0
                            ? 0
                            : (answeredCount / invitationQuery.data.questions.length) * 100
                        }%`,
                      }}
                    />
                  </span>
                </div>
                {invitationQuery.data.questions.map((question, index) => (
                  <div key={question.id} className="card question-card">
                    <p>
                      <strong>
                        {index + 1}. {question.prompt}
                      </strong>{" "}
                      <span className="muted">({question.points} pt{question.points === 1 ? "" : "s"})</span>
                    </p>
                    <div className="stack-lg choice-list">
                      {question.options.map((option) => (
                        <label key={option.id} className="choice">
                          <input
                            type={question.type === "MCQ_MULTI" ? "checkbox" : "radio"}
                            name={question.id}
                            checked={(answers[question.id] ?? []).includes(option.id)}
                            onChange={() =>
                              toggleOption(question.id, option.id, question.type === "MCQ_MULTI")
                            }
                          />
                          {option.label}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}

                {submitMutation.isError && (
                  <Alert>
                    {submitMutation.error instanceof ApiError
                      ? submitMutation.error.message
                      : "Could not submit your answers."}
                  </Alert>
                )}

                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={submitMutation.isPending}
                  onClick={() => submitMutation.mutate()}
                >
                  {submitMutation.isPending ? "Submitting…" : "Submit assessment"}
                </button>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
