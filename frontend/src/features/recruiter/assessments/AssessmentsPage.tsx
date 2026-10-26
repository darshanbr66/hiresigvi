import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Modal } from "../../../shared/components/Modal";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import type { AssessmentCreateRequest, AssessmentSummary } from "../../../types/assessment";
import { useAuth } from "../../auth/AuthContext";
import { AssessmentForm, emptyAssessmentFormValue } from "./AssessmentForm";
import { createAssessment, deleteAssessment, listAssessments, listPendingReview } from "./api";

const ASSESSMENTS_QUERY_KEY = ["recruiter", "assessments"];

export function AssessmentsPage() {
  const { accessToken } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const assessmentsQuery = useQuery({
    queryKey: ASSESSMENTS_QUERY_KEY,
    queryFn: () => listAssessments(token),
    enabled: accessToken !== null,
  });

  const pendingQuery = useQuery({
    queryKey: [...ASSESSMENTS_QUERY_KEY, "pending-review"],
    queryFn: () => listPendingReview(token),
    enabled: accessToken !== null,
  });

  const [showForm, setShowForm] = useState(false);
  const [formValue, setFormValue] = useState<AssessmentCreateRequest>(emptyAssessmentFormValue());
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AssessmentSummary | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: (status: "DRAFT" | "PUBLISHED") => createAssessment({ ...formValue, status }, token),
    onSuccess: (created) => {
      showToast(
        created.status === "DRAFT"
          ? "Draft saved. Publish it when it's ready to send."
          : "Assessment created and published.",
        "success",
      );
      setFormValue(emptyAssessmentFormValue());
      setFormError(null);
      setShowForm(false);
      void queryClient.invalidateQueries({ queryKey: ASSESSMENTS_QUERY_KEY });
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    createMutation.mutate(submitter?.value === "DRAFT" ? "DRAFT" : "PUBLISHED");
  }

  function openCreateForm() {
    setFormValue(emptyAssessmentFormValue());
    setFormError(null);
    setShowForm(true);
  }

  function closeForm() {
    if (createMutation.isPending) return;
    setShowForm(false);
  }

  const deleteMutation = useMutation({
    mutationFn: () => deleteAssessment(pendingDelete!.id, { reason: deleteReason.trim() }, token),
    onSuccess: () => {
      showToast(`${pendingDelete?.title} was deleted.`, "success");
      setPendingDelete(null);
      setDeleteReason("");
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: ASSESSMENTS_QUERY_KEY });
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openDeleteModal(assessment: AssessmentSummary) {
    setPendingDelete(assessment);
    setDeleteReason("");
    setDeleteError(null);
  }

  function closeDeleteModal() {
    if (deleteMutation.isPending) return;
    setPendingDelete(null);
    setDeleteReason("");
    setDeleteError(null);
  }

  function handleDeleteSubmit(event: FormEvent) {
    event.preventDefault();
    if (deleteReason.trim().length === 0) {
      setDeleteError("A reason for deletion is required.");
      return;
    }
    deleteMutation.mutate();
  }

  const canManage = !(assessmentsQuery.error instanceof ApiError && assessmentsQuery.error.status === 403);

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Assessments</h1>
          <p className="muted">
            Reusable tests for campus drives, regular hiring and programs — build them from the{" "}
            <Link to="/recruiter/question-bank">question bank</Link> or write questions directly.
          </p>
        </div>
        {canManage && (
          <button type="button" className="btn btn-primary" onClick={openCreateForm}>
            + New assessment
          </button>
        )}
      </div>

      {pendingQuery.isSuccess && pendingQuery.data.length > 0 && (
        <section className="card stack-sm" aria-label="Answers awaiting grading">
          <h2>Answers awaiting grading ({pendingQuery.data.length})</h2>
          <ul className="stack-sm" style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {pendingQuery.data.map((entry) => (
              <li key={entry.invitation_id}>
                <Link to={`/recruiter/assessments/attempts/${entry.invitation_id}`}>
                  {entry.candidate_full_name} — {entry.assessment_title}
                </Link>{" "}
                <span className="muted">
                  (attempt {entry.attempt_number}
                  {entry.submitted_at ? `, submitted ${new Date(entry.submitted_at).toLocaleDateString()}` : ""})
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {assessmentsQuery.isPending && <SkeletonTable columns={6} />}
      {assessmentsQuery.isError && !canManage && (
        <Alert>You do not have permission to view assessments.</Alert>
      )}

      {assessmentsQuery.isSuccess &&
        (assessmentsQuery.data.length === 0 ? (
          <EmptyState icon="assessments" title="No assessments yet">
            Create one to start screening candidates with skills tests.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Title</th>
                  <th>Questions</th>
                  <th>Duration</th>
                  <th>Pass score</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {assessmentsQuery.data.map((a, index) => (
                  <tr key={a.id}>
                    <td>{index + 1}</td>
                    <td>
                      <Link to={`/recruiter/assessments/${a.id}`}>{a.title}</Link>
                    </td>
                    <td>{a.question_count}</td>
                    <td>{a.duration_minutes} min</td>
                    <td>{a.pass_score}%</td>
                    <td>
                      <span className={`badge ${a.status === "DRAFT" ? "badge-inactive" : "badge-active"}`}>
                        {a.status === "DRAFT" ? "Draft" : "Published"}
                      </span>
                    </td>
                    <td>
                      <div className="btn-group">
                        <Link to={`/recruiter/assessments/${a.id}`} className="btn btn-ghost btn-sm">
                          View
                        </Link>
                        {canManage && (
                          <button
                            type="button"
                            className="btn btn-danger btn-sm"
                            onClick={() => openDeleteModal(a)}
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {showForm && (
        <Modal title="Create an assessment" onClose={closeForm} wide>
          <form onSubmit={handleSubmit}>
            <AssessmentForm
              value={formValue}
              onChange={setFormValue}
              disabled={createMutation.isPending}
              accessToken={token}
            />

            {formError && <Alert>{formError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1.5rem" }}>
              <button
                type="submit"
                value="PUBLISHED"
                className="btn btn-primary"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? <Spinner label="Saving…" /> : "Create & publish"}
              </button>
              <button type="submit" value="DRAFT" className="btn btn-ghost" disabled={createMutation.isPending}>
                Save as draft
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeForm}
                disabled={createMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingDelete && (
        <Modal title="Delete assessment" onClose={closeDeleteModal}>
          <form onSubmit={handleDeleteSubmit}>
            <p>
              <strong>Assessment:</strong> {pendingDelete.title}
            </p>
            <p className="muted">
              This removes "{pendingDelete.title}" from your active assessment list and from the
              invite/retest pickers. Every past invitation and result that used it stays intact,
              and this action is recorded in Activities.
            </p>
            <label className="field">
              <span>Reason for deletion</span>
              <textarea
                required
                rows={3}
                value={deleteReason}
                onChange={(e) => {
                  setDeleteReason(e.target.value);
                  if (deleteError) setDeleteError(null);
                }}
                disabled={deleteMutation.isPending}
                placeholder="e.g. Outdated question set"
              />
            </label>

            {deleteError && <Alert>{deleteError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-danger" disabled={deleteMutation.isPending}>
                {deleteMutation.isPending ? <Spinner label="Deleting…" /> : "Delete assessment"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeDeleteModal}
                disabled={deleteMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
