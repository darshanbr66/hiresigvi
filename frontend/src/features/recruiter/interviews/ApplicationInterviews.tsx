import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { SkeletonLines } from "../../../shared/components/Skeleton";
import { useToast } from "../../../shared/components/ToastContext";
import type { ApplicationStatus } from "../../../types/recruitment";
import type { InterviewOutcome } from "../../../types/program";
import { listUsers } from "../../auth/api";
import { StateBadge } from "../programs/panelParts";
import { closeInterview, listInterviews, recordInterviewOutcome, scheduleInterview } from "./api";

const SCHEDULABLE: ApplicationStatus[] = ["SHORTLISTED", "INTERVIEW"];

/**
 * Interview rounds of one application — used on the application page for
 * every job and inside a program participant's panel. Schedule, assign an
 * interviewer (who gets an in-app notice), record the outcome and staff-only
 * feedback, cancel or mark a no-show. The outcome never selects or rejects
 * anyone; that stays a status change on the application. Which actions show
 * follows the permissions the server returns with the list, and feedback
 * is withheld by the server from anyone not allowed to read it.
 */
export function ApplicationInterviews({
  applicationId,
  applicationStatus,
  token,
  userId,
  stages = [],
  onChanged,
  children,
}: {
  applicationId: string;
  applicationStatus: ApplicationStatus;
  token: string;
  userId: string | undefined;
  /** Configured stage names (a program's); free text when empty. */
  stages?: string[];
  /** Called after any change (scheduling can move SHORTLISTED → INTERVIEW). */
  onChanged?: () => void;
  children?: ReactNode;
}) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const key = ["recruiter", "applications", applicationId, "interviews"];
  const listQuery = useQuery({ queryKey: key, queryFn: () => listInterviews(applicationId, token), retry: false });
  const [form, setForm] = useState({ stage: stages[0] ?? "", at: "", interviewer: "", location: "" });
  const [drafts, setDrafts] = useState<Record<string, { outcome: InterviewOutcome; feedback: string; rating: string }>>({});
  const [error, setError] = useState<string | null>(null);
  const manage = listQuery.data?.viewer_permissions.includes("interview.manage") ?? false;
  const usersQuery = useQuery({
    queryKey: ["recruiter", "users"],
    queryFn: () => listUsers(token),
    enabled: manage,
    retry: false,
  });

  const done = (message: string) => {
    void queryClient.invalidateQueries({ queryKey: key });
    onChanged?.();
    setError(null);
    showToast(message, "success");
  };
  const fail = (err: unknown) => setError(err instanceof ApiError ? err.message : "Unable to reach the server.");
  const schedule = useMutation({
    mutationFn: () =>
      scheduleInterview(applicationId, {
        stage: form.stage,
        scheduled_at: new Date(form.at).toISOString(),
        location: form.location || null,
        interviewer_user_id: form.interviewer || null,
      }, token),
    onSuccess: () => {
      setForm({ ...form, at: "" });
      done("Interview scheduled. Invite the candidate with the Interview Invitation email.");
    },
    onError: fail,
  });
  const record = useMutation({
    mutationFn: (id: string) =>
      recordInterviewOutcome(applicationId, id, {
        outcome: drafts[id].outcome,
        feedback: drafts[id].feedback || null,
        rating: drafts[id].rating ? Number(drafts[id].rating) : null,
      }, token),
    onSuccess: () => done("Interview outcome recorded."),
    onError: fail,
  });
  const close = useMutation({
    mutationFn: ({ id, status, reason }: { id: string; status: "CANCELLED" | "NO_SHOW"; reason: string | null }) =>
      closeInterview(applicationId, id, { status, reason }, token),
    onSuccess: () => done("Interview updated."),
    onError: fail,
  });

  // No interview permission: the section isn't shown at all.
  if (listQuery.isError && listQuery.error instanceof ApiError && listQuery.error.status === 403) return null;

  const working = schedule.isPending || record.isPending || close.isPending;
  const interviews = listQuery.data?.items ?? [];

  return (
    <section className="card stack-sm" aria-label="Interviews">
      <h2 style={{ margin: 0 }}>Interviews</h2>
      {error && <Alert>{error}</Alert>}
      {listQuery.isPending && <SkeletonLines count={2} />}
      {listQuery.isError && !(listQuery.error instanceof ApiError && listQuery.error.status === 403) && (
        <Alert>Could not load interviews.</Alert>
      )}
      {stages.length > 0 && <p className="field-hint">Required stages: {stages.join(" → ")}</p>}
      {listQuery.isSuccess && interviews.length === 0 && <p className="muted">No interviews scheduled yet.</p>}
      {interviews.map((i) => {
        const draft = drafts[i.id] ?? { outcome: "PASS" as InterviewOutcome, feedback: "", rating: "" };
        const setDraft = (patch: Partial<typeof draft>) => setDrafts({ ...drafts, [i.id]: { ...draft, ...patch } });
        return (
          <div key={i.id} className="card stack-sm" style={{ background: "var(--color-bg)" }}>
            <div className="detail-row">
              <span className="detail-row-label">{i.stage}</span>
              <span>
                {new Date(i.scheduled_at).toLocaleString()} · <StateBadge value={i.status} />
                {i.outcome && <> · <StateBadge value={i.outcome} /></>}
              </span>
            </div>
            {i.interviewer_name && <p className="field-hint">Interviewer: {i.interviewer_name}</p>}
            {i.location && <p className="field-hint">Where: {i.location}</p>}
            {i.rating !== null && <p className="field-hint">Rating: {i.rating} / 5</p>}
            {i.feedback && <p style={{ margin: 0 }}><strong>Feedback:</strong> {i.feedback}</p>}
            {i.feedback_restricted && <p className="field-hint">Feedback restricted to the interviewer and interview managers.</p>}
            {i.cancellation_reason && <p className="field-hint">Reason: {i.cancellation_reason}</p>}
            {i.status === "SCHEDULED" && (manage || i.interviewer_user_id === userId) && (
              <div className="stack-sm">
                <div className="field-row">
                  <label className="field">
                    <span>Outcome</span>
                    <select value={draft.outcome} disabled={working}
                      onChange={(ev) => setDraft({ outcome: ev.target.value as InterviewOutcome })}>
                      <option value="PASS">Pass</option>
                      <option value="ON_HOLD">On hold</option>
                      <option value="FAIL">Fail</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Rating <span className="optional-mark">(1–5, optional)</span></span>
                    <input type="number" min={1} max={5} value={draft.rating} disabled={working}
                      onChange={(ev) => setDraft({ rating: ev.target.value })} />
                  </label>
                </div>
                <label className="field">
                  <span>Feedback <span className="optional-mark">(required unless pass; staff only)</span></span>
                  <textarea rows={2} value={draft.feedback} disabled={working}
                    onChange={(ev) => setDraft({ feedback: ev.target.value })} />
                </label>
                <div className="btn-group">
                  <button type="button" className="btn btn-primary btn-sm"
                    disabled={working || (draft.outcome !== "PASS" && !draft.feedback.trim())}
                    onClick={() => { setDrafts({ ...drafts, [i.id]: draft }); record.mutate(i.id); }}>
                    Record outcome
                  </button>
                  {manage && (
                    <>
                      <button type="button" className="btn btn-ghost btn-sm" disabled={working}
                        onClick={() => {
                          const reason = window.prompt("Why is this interview being cancelled?");
                          if (reason && reason.trim()) close.mutate({ id: i.id, status: "CANCELLED", reason });
                        }}>
                        Cancel interview
                      </button>
                      <button type="button" className="btn btn-ghost btn-sm" disabled={working}
                        onClick={() => close.mutate({ id: i.id, status: "NO_SHOW", reason: null })}>
                        No-show
                      </button>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
      {manage && SCHEDULABLE.includes(applicationStatus) && (
        <div className="stack-sm">
          <h3 style={{ margin: 0 }}>Schedule an interview</h3>
          <div className="field-row">
            <label className="field">
              <span>Stage</span>
              {stages.length > 0 ? (
                <select value={form.stage} onChange={(ev) => setForm({ ...form, stage: ev.target.value })} disabled={working}>
                  {stages.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              ) : (
                <input value={form.stage} placeholder="e.g. Technical" onChange={(ev) => setForm({ ...form, stage: ev.target.value })} disabled={working} />
              )}
            </label>
            <label className="field">
              <span>Date and time</span>
              <input type="datetime-local" value={form.at} onChange={(ev) => setForm({ ...form, at: ev.target.value })} disabled={working} />
            </label>
            {usersQuery.data && (
              <label className="field">
                <span>Interviewer <span className="optional-mark">(Optional)</span></span>
                <select value={form.interviewer} onChange={(ev) => setForm({ ...form, interviewer: ev.target.value })} disabled={working}>
                  <option value="">Unassigned</option>
                  {usersQuery.data.filter((u) => u.is_active).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                </select>
              </label>
            )}
            <label className="field">
              <span>Location / link <span className="optional-mark">(Optional)</span></span>
              <input value={form.location} onChange={(ev) => setForm({ ...form, location: ev.target.value })} disabled={working} />
            </label>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}
            disabled={working || !form.stage.trim() || !form.at} onClick={() => schedule.mutate()}>
            Schedule
          </button>
        </div>
      )}
      {manage && !SCHEDULABLE.includes(applicationStatus) && interviews.length === 0 && (
        <p className="field-hint">Interviews can be scheduled once the candidate is shortlisted.</p>
      )}
      {children}
      <p className="field-hint">
        An interview outcome is input to the decision — select or reject the candidate with the
        application status.
      </p>
    </section>
  );
}
