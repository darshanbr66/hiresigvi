import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Pagination } from "../../../shared/components/Pagination";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { statusTone, toneBadgeClass } from "../../../shared/lib/statusTone";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import type { ApplicationResponse } from "../../../types/recruitment";
import { useAuth } from "../../auth/AuthContext";
import { listCampusDrives } from "../campusDrives/api";
import { listCandidates } from "../candidates/api";
import { listInstitutions } from "../institutions/api";
import { listJobs } from "../jobs/api";
import {
  PAGE_SIZE,
  hasActiveCriteria,
  toApiQuery,
  useApplicationListState,
} from "./applicationFilters";
import { ApplicationListControls, type FilterField } from "./ApplicationListControls";
import { createApplication, deleteApplication, listApplicationsPage } from "./api";

const APPLICATIONS_QUERY_KEY = ["recruiter", "applications"];

const FILTER_FIELDS: FilterField[] = [
  "job",
  "status",
  "candidateType",
  "experience",
  "currentTitle",
  "currentCompany",
  "location",
  "preferredLocation",
  "qualification",
  "notice",
  "immediateJoiner",
  "applied",
  "source",
  "campusDrive",
  "institution",
];

export function ApplicationsPage() {
  const { accessToken } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const token = accessToken as string;
  const { showToast } = useToast();
  const [showForm, setShowForm] = useState(false);
  // Search, filters, sort and page live in the URL and are applied by the server.
  const [listState, setListState] = useApplicationListState();
  const apiQuery = toApiQuery(listState);

  const applicationsQuery = useQuery({
    queryKey: [...APPLICATIONS_QUERY_KEY, "list", apiQuery],
    // The page number rides along with the rows, so the row numbers and the
    // "Showing 26–50" summary always describe the rows on screen.
    queryFn: async () => ({
      ...(await listApplicationsPage(apiQuery, token)),
      page: listState.page,
    }),
    enabled: accessToken !== null,
    // Keep showing the previous page (dimmed) while the next one loads.
    placeholderData: keepPreviousData,
  });

  const candidatesQuery = useQuery({
    queryKey: ["recruiter", "candidates"],
    queryFn: () => listCandidates(token),
    enabled: accessToken !== null,
  });

  const jobsQuery = useQuery({
    queryKey: ["recruiter", "jobs"],
    queryFn: () => listJobs(token),
    enabled: accessToken !== null,
  });

  // Options for the campus drive / institution filters. Same cache keys as
  // the Campus Drives and Institutions pages, so they share one fetch.
  const campusDrivesQuery = useQuery({
    queryKey: ["recruiter", "campus-drives"],
    queryFn: () => listCampusDrives(token),
    enabled: accessToken !== null,
  });

  const institutionsQuery = useQuery({
    queryKey: ["recruiter", "institutions", "active"],
    queryFn: () => listInstitutions(token, { activeOnly: true }),
    enabled: accessToken !== null,
  });

  const [candidateId, setCandidateId] = useState("");
  const [jobId, setJobId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ApplicationResponse | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const createApplicationMutation = useMutation({
    mutationFn: () => createApplication({ candidate_id: candidateId, job_id: jobId }, token),
    onSuccess: () => {
      showToast("Application created.", "success");
      setCandidateId("");
      setJobId("");
      setFormError(null);
      setShowForm(false);
      void queryClient.invalidateQueries({ queryKey: APPLICATIONS_QUERY_KEY });
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createApplicationMutation.mutate();
  }

  function closeForm() {
    if (createApplicationMutation.isPending) return;
    setShowForm(false);
  }

  const deleteMutation = useMutation({
    mutationFn: () => deleteApplication(pendingDelete!.id, { reason: deleteReason.trim() }, token),
    onSuccess: () => {
      showToast(`Application for ${pendingDelete?.candidate_full_name} was deleted.`, "success");
      setPendingDelete(null);
      setDeleteReason("");
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: APPLICATIONS_QUERY_KEY });
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openDeleteModal(application: ApplicationResponse) {
    setPendingDelete(application);
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

  const canManageApplications = !(
    applicationsQuery.error instanceof ApiError && applicationsQuery.error.status === 403
  );

  const applications = applicationsQuery.data?.items ?? [];
  const totalMatches = applicationsQuery.data?.total ?? 0;
  const shownPage = applicationsQuery.data?.page ?? listState.page;
  const criteriaActive = hasActiveCriteria(listState.filters);
  const lastPage = Math.max(1, Math.ceil(totalMatches / PAGE_SIZE));

  // If the current page no longer exists (rows were deleted, or a stale link),
  // move to the last real page rather than showing an empty one.
  useEffect(() => {
    if (
      applicationsQuery.isSuccess &&
      !applicationsQuery.isPlaceholderData &&
      listState.page > lastPage
    ) {
      setListState({ ...listState, page: lastPage });
    }
  }, [applicationsQuery.isSuccess, applicationsQuery.isPlaceholderData, listState, lastPage, setListState]);

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Applications</h1>
          <p className="muted">Every candidate's progress through your hiring pipeline.</p>
        </div>
        {canManageApplications && (
          <button type="button" className="btn btn-primary" onClick={() => setShowForm(true)}>
            + Link candidate to job
          </button>
        )}
      </div>

      {applicationsQuery.isPending && <SkeletonTable columns={7} />}

      {applicationsQuery.isError && !canManageApplications && (
        <Alert>You do not have permission to view applications.</Alert>
      )}
      {applicationsQuery.isError && canManageApplications && (
        <Alert>
          {applicationsQuery.error instanceof ApiError
            ? applicationsQuery.error.message
            : "Could not load applications."}
        </Alert>
      )}

      {/* Nothing to search until at least one application exists. */}
      {((applicationsQuery.isSuccess && (totalMatches > 0 || criteriaActive)) ||
        (applicationsQuery.isError && canManageApplications)) && (
        <ApplicationListControls
          state={listState}
          onChange={setListState}
          fields={FILTER_FIELDS}
          jobs={jobsQuery.data}
          campusDrives={campusDrivesQuery.data}
          institutions={institutionsQuery.data}
          showSort
          searchLabel="Search applications"
          searchPlaceholder="Search by name, email, phone or job…"
        />
      )}

      {applicationsQuery.isSuccess && (
        <section className="stack-lg" style={{ gap: "1rem" }}>
          {totalMatches === 0 && !criteriaActive ? (
            <EmptyState icon="inbox" title="No applications yet">
              Use "+ Link candidate to job" above to add one, or wait for candidates to apply.
            </EmptyState>
          ) : totalMatches === 0 ? (
            <EmptyState icon="search" title="No applications match these filters">
              Try a different search, or remove some filters.
            </EmptyState>
          ) : (
            <div
              className={
                applicationsQuery.isPlaceholderData ? "table-scroll is-refreshing" : "table-scroll"
              }
              aria-busy={applicationsQuery.isPlaceholderData}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Candidate</th>
                    <th>Job</th>
                    <th>Status</th>
                    <th>Source</th>
                    <th>Applied</th>
                    <th>Resume</th>
                    {canManageApplications && <th>Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {applications.map((application, index) => (
                    <tr
                      key={application.id}
                      className="clickable-row"
                      onClick={() => navigate(`/recruiter/applications/${application.id}`)}
                    >
                      <td>{(shownPage - 1) * PAGE_SIZE + index + 1}</td>
                      <td>
                        {application.candidate_full_name}
                        <span className="cell-sub">
                          {[application.candidate_email, application.candidate_phone]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </td>
                      <td>{application.job_title}</td>
                      <td>
                        <span className={toneBadgeClass(statusTone(application.status))}>
                          {application.status}
                        </span>
                      </td>
                      <td>
                        <span className="badge badge-inactive">{application.source}</span>
                      </td>
                      <td>{new Date(application.applied_at).toLocaleDateString()}</td>
                      <td>{application.resume_id ? "Yes" : "—"}</td>
                      {canManageApplications && (
                        <td onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className="btn btn-danger btn-sm"
                            onClick={() => openDeleteModal(application)}
                          >
                            Delete
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {totalMatches > 0 && (
            <Pagination
              page={shownPage}
              pageSize={PAGE_SIZE}
              total={totalMatches}
              noun={totalMatches === 1 ? "application" : "applications"}
              disabled={applicationsQuery.isPlaceholderData}
              onPageChange={(page) => setListState({ ...listState, page })}
            />
          )}
        </section>
      )}

      {canManageApplications && showForm && (
        <Modal title="Link a candidate to a job" onClose={closeForm}>
          <p className="muted">
            Most applications arrive through your career site automatically — use this to add one
            by hand.
          </p>
          <form onSubmit={handleSubmit} noValidate>
            <label className="field">
              <span>Candidate</span>
              <select
                required
                value={candidateId}
                onChange={(e) => setCandidateId(e.target.value)}
                disabled={createApplicationMutation.isPending}
              >
                <option value="" disabled>
                  Select a candidate…
                </option>
                {candidatesQuery.data?.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.full_name} ({candidate.email})
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Job</span>
              <select
                required
                value={jobId}
                onChange={(e) => setJobId(e.target.value)}
                disabled={createApplicationMutation.isPending}
              >
                <option value="" disabled>
                  Select a job…
                </option>
                {jobsQuery.data?.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.title}
                  </option>
                ))}
              </select>
            </label>

            {(candidatesQuery.data?.length ?? 0) === 0 && (
              <p className="field-hint">Add a candidate first from the Candidates page.</p>
            )}
            {(jobsQuery.data?.length ?? 0) === 0 && (
              <p className="field-hint">Create a job first from the Jobs page.</p>
            )}

            {formError && <Alert>{formError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={createApplicationMutation.isPending || !candidateId || !jobId}
              >
                {createApplicationMutation.isPending ? (
                  <Spinner label="Linking…" />
                ) : (
                  "Create application"
                )}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeForm}
                disabled={createApplicationMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingDelete && (
        <Modal title="Delete application" onClose={closeDeleteModal}>
          <form onSubmit={handleDeleteSubmit}>
            <p>
              <strong>Candidate:</strong> {pendingDelete.candidate_full_name}
              <br />
              <strong>Job:</strong> {pendingDelete.job_title}
            </p>
            <p className="muted">
              This removes this application from your active pipeline. Its status history and any
              assessment attempts are preserved, and this action is recorded in Activities.
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
                placeholder="e.g. Duplicate application"
              />
            </label>

            {deleteError && <Alert>{deleteError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-danger" disabled={deleteMutation.isPending}>
                {deleteMutation.isPending ? <Spinner label="Deleting…" /> : "Delete application"}
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
