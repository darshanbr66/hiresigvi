import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { ConfirmDialog } from "../../../shared/components/ConfirmDialog";
import { Icon } from "../../../shared/components/Icon";
import { Modal } from "../../../shared/components/Modal";
import { EmptyState } from "../../../shared/components/EmptyState";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import {
  EMPLOYMENT_TYPE_LABELS,
  EMPLOYMENT_TYPES,
  type EmploymentType,
  type JobResponse,
  type JobStatus,
} from "../../../types/recruitment";
import { useAuth } from "../../auth/AuthContext";
import { createJob, deleteJob, listJobs, updateJob } from "./api";
import {
  detailsFromJob,
  detailsPayload,
  EMPTY_DETAILS,
  EmploymentDetailsFields,
  type EmploymentDetailsForm,
} from "./employmentDetails";

const JOBS_QUERY_KEY = ["recruiter", "jobs"];

const STATUS_BADGE_CLASS: Record<JobStatus, string> = {
  DRAFT: "badge-inactive",
  OPEN: "badge-active",
  ON_HOLD: "badge-warn",
  CLOSED: "badge-inactive",
  WITHDRAWN: "badge-inactive",
};

function jobActions(job: JobResponse): { label: string; status: JobStatus; confirm?: boolean }[] {
  switch (job.status) {
    case "DRAFT":
      return [{ label: "Publish", status: "OPEN" }];
    case "OPEN":
      return [
        { label: "Put on hold", status: "ON_HOLD" },
        { label: "Close", status: "CLOSED", confirm: true },
      ];
    case "ON_HOLD":
      return [
        { label: "Reopen", status: "OPEN" },
        { label: "Close", status: "CLOSED", confirm: true },
      ];
    case "CLOSED":
      return [{ label: "Reopen", status: "OPEN" }];
    default:
      return [];
  }
}

interface JobFormState {
  title: string;
  department: string;
  location: string;
  /** null only for a recruitment program's carrier job, which has no type. */
  employmentType: EmploymentType | null;
  details: EmploymentDetailsForm;
  openingsCount: string;
  description: string;
  descriptionVisible: boolean;
}

const EMPTY_FORM: JobFormState = {
  title: "",
  department: "",
  location: "",
  employmentType: "FULL_TIME",
  details: EMPTY_DETAILS,
  openingsCount: "1",
  description: "",
  descriptionVisible: true,
};

function jobToFormState(job: JobResponse): JobFormState {
  return {
    title: job.title,
    department: job.department ?? "",
    location: job.location ?? "",
    employmentType: job.employment_type,
    details: detailsFromJob(job),
    openingsCount: String(job.openings_count),
    description: job.description,
    descriptionVisible: job.description_visible,
  };
}

/** No icon library exists in this project (see ThemeToggle's plain-emoji
 * convention) — small inline SVGs, matching that lightweight approach. */
function EyeIcon({ visible }: { visible: boolean }) {
  if (visible) {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M1.5 12S5 5 12 5s10.5 7 10.5 7-3.5 7-10.5 7S1.5 12 1.5 12Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.8" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 3l18 18M10.6 10.7a3 3 0 0 0 4.2 4.2M6.6 6.7C3.9 8.4 1.5 12 1.5 12S5 19 12 19c1.8 0 3.4-.4 4.7-1M17.4 17.3C20.1 15.6 22.5 12 22.5 12S19.4 6.2 14 5.2"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function JobsPage() {
  const { accessToken } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const jobsQuery = useQuery({
    queryKey: JOBS_QUERY_KEY,
    queryFn: () => listJobs(token),
    enabled: accessToken !== null,
  });

  const [search, setSearch] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editingJob, setEditingJob] = useState<JobResponse | null>(null);
  const [form, setForm] = useState<JobFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [openingsError, setOpeningsError] = useState<string | null>(null);
  const [pendingClose, setPendingClose] = useState<JobResponse | null>(null);
  // The job whose status just changed: its row flashes and its badge pops once.
  const [flashJobId, setFlashJobId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<JobResponse | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  function openCreateForm() {
    setEditingJob(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setOpeningsError(null);
    setShowForm(true);
  }

  function openEditForm(job: JobResponse) {
    setEditingJob(job);
    setForm(jobToFormState(job));
    setFormError(null);
    setOpeningsError(null);
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditingJob(null);
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        title: form.title,
        department: form.department || null,
        location: form.location || null,
        ...(form.employmentType
          ? { employment_type: form.employmentType, ...detailsPayload(form.employmentType, form.details) }
          : {}),
        description: form.description,
        description_visible: form.descriptionVisible,
        openings_count: Number(form.openingsCount),
      };
      return editingJob
        ? updateJob(editingJob.id, payload, token)
        : createJob(payload, token);
    },
    onSuccess: () => {
      showToast(editingJob ? "Job updated." : "Job created.", "success");
      closeForm();
      void queryClient.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  const statusMutation = useMutation({
    mutationFn: ({ jobId, status }: { jobId: string; status: JobStatus; label: string }) =>
      updateJob(jobId, { status }, token),
    onSuccess: (_data, variables) => {
      const title = jobsQuery.data?.find((job) => job.id === variables.jobId)?.title;
      const now = { OPEN: "open", ON_HOLD: "on hold", CLOSED: "closed", DRAFT: "a draft" }[variables.status as string];
      showToast(
        title && now ? `“${title}” is now ${now}.` : `${variables.label} — done.`,
        "success",
      );
      setFlashJobId(variables.jobId);
      window.setTimeout(() => setFlashJobId((current) => (current === variables.jobId ? null : current)), 1800);
      setPendingClose(null);
      void queryClient.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
    },
  });

  function validateOpenings(value: string): boolean {
    const num = Number(value);
    if (!Number.isInteger(num) || num < 1) {
      setOpeningsError("Number of openings must be a positive whole number.");
      return false;
    }
    setOpeningsError(null);
    return true;
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!validateOpenings(form.openingsCount)) return;
    saveMutation.mutate();
  }

  function handleAction(job: JobResponse, action: { label: string; status: JobStatus; confirm?: boolean }) {
    if (action.confirm) {
      setPendingClose(job);
    } else {
      statusMutation.mutate({ jobId: job.id, status: action.status, label: action.label });
    }
  }

  const deleteMutation = useMutation({
    mutationFn: () => deleteJob(pendingDelete!.id, { reason: deleteReason.trim() }, token),
    onSuccess: () => {
      showToast(`${pendingDelete?.title} was deleted.`, "success");
      setPendingDelete(null);
      setDeleteReason("");
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openDeleteModal(job: JobResponse) {
    setPendingDelete(job);
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

  const canManageJobs = !(jobsQuery.error instanceof ApiError && jobsQuery.error.status === 403);

  const [typeFilter, setTypeFilter] = useState<EmploymentType | "">("");

  const filteredJobs = useMemo(() => {
    if (!jobsQuery.data) return [];
    const term = search.trim().toLowerCase();
    return jobsQuery.data.filter(
      (job) =>
        (!typeFilter || job.employment_type === typeFilter) &&
        (!term ||
          job.title.toLowerCase().includes(term) ||
          (job.department ?? "").toLowerCase().includes(term) ||
          (job.location ?? "").toLowerCase().includes(term)),
    );
  }, [jobsQuery.data, search, typeFilter]);

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Jobs</h1>
          <p className="muted">Open requisitions across your organization.</p>
        </div>
        {canManageJobs && (
          <div className="page-header-actions">
            <button type="button" className="btn btn-primary" onClick={openCreateForm}>
              + New job
            </button>
          </div>
        )}
      </div>

      {jobsQuery.isPending && <SkeletonTable columns={8} />}

      {jobsQuery.isError && !canManageJobs && (
        <Alert>You do not have permission to view jobs.</Alert>
      )}
      {jobsQuery.isError && canManageJobs && (
        <Alert>
          {jobsQuery.error instanceof ApiError ? jobsQuery.error.message : "Could not load jobs."}
        </Alert>
      )}

      {jobsQuery.isSuccess && (
        <section className="stack-lg" style={{ gap: "1rem" }}>
          {jobsQuery.data.length === 0 ? (
            <EmptyState
              icon="jobs"
              title="No jobs yet"
              action={
                canManageJobs ? (
                  <button type="button" className="btn btn-primary" onClick={openCreateForm}>
                    Create your first job
                  </button>
                ) : undefined
              }
            >
              Create your first requisition to start hiring.
            </EmptyState>
          ) : (
            <>
              <div className="toolbar">
                <input
                  className="search-input"
                  placeholder="Search by title, department, or location…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <select
                  aria-label="Employment type"
                  value={typeFilter}
                  onChange={(e) => setTypeFilter(e.target.value as EmploymentType | "")}
                >
                  <option value="">All types</option>
                  {EMPLOYMENT_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {EMPLOYMENT_TYPE_LABELS[type]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Title</th>
                      <th>Department</th>
                      <th>Location</th>
                      <th>Type</th>
                      <th>Openings</th>
                      <th>Status</th>
                      {canManageJobs && <th>Actions</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredJobs.map((job, index) => (
                      <tr key={job.id} className={flashJobId === job.id ? "row-flash" : undefined}>
                        <td>{index + 1}</td>
                        <td>{job.title}</td>
                        <td>{job.department ?? "—"}</td>
                        <td>{job.location ?? "—"}</td>
                        <td>{job.employment_type ? EMPLOYMENT_TYPE_LABELS[job.employment_type] : "Program"}</td>
                        <td>{job.openings_count}</td>
                        <td>
                          <span
                            key={job.status}
                            className={`badge ${STATUS_BADGE_CLASS[job.status]}${flashJobId === job.id ? " badge-pop" : ""}`}
                          >
                            {job.status}
                          </span>
                        </td>
                        {canManageJobs && (
                          <td>
                            <div className="btn-group">
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  navigate(`/recruiter/ai?jobId=${job.id}`);
                                }}
                              >
                                <Icon name="sparkles" size={14} />
                                AI Match
                              </button>
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                onClick={() => openEditForm(job)}
                              >
                                Edit
                              </button>
                              {jobActions(job).map((action) => (
                                <button
                                  key={action.status}
                                  type="button"
                                  className="btn btn-ghost btn-sm"
                                  disabled={statusMutation.isPending}
                                  onClick={() => handleAction(job, action)}
                                >
                                  {action.label}
                                </button>
                              ))}
                              <button
                                type="button"
                                className="btn btn-danger btn-sm"
                                onClick={() => openDeleteModal(job)}
                              >
                                Delete
                              </button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      )}

      {canManageJobs && showForm && (
        <Modal title={editingJob ? `Edit ${editingJob.title}` : "Create a job"} onClose={closeForm}>
          <form onSubmit={handleSubmit}>
            <label className="field">
              <span>Title</span>
              <input
                required
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                disabled={saveMutation.isPending}
              />
            </label>

            <div className="field-row">
              <label className="field">
                <span>Department</span>
                <input
                  value={form.department}
                  onChange={(e) => setForm({ ...form, department: e.target.value })}
                  disabled={saveMutation.isPending}
                />
              </label>

              <label className="field">
                <span>Location</span>
                <input
                  value={form.location}
                  onChange={(e) => setForm({ ...form, location: e.target.value })}
                  disabled={saveMutation.isPending}
                />
              </label>
            </div>

            <div className="field-row">
              <label className="field">
                <span>Employment type</span>
                {form.employmentType ? (
                  <select
                    required
                    value={form.employmentType}
                    onChange={(e) => setForm({ ...form, employmentType: e.target.value as EmploymentType })}
                    disabled={saveMutation.isPending}
                  >
                    {EMPLOYMENT_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {EMPLOYMENT_TYPE_LABELS[type]}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="field-hint">Recruitment program job — managed from Programs.</span>
                )}
              </label>

              <label className="field">
                <span>Number of openings</span>
                <input
                  type="number"
                  min={1}
                  step={1}
                  required
                  value={form.openingsCount}
                  onChange={(e) => {
                    setForm({ ...form, openingsCount: e.target.value });
                    if (openingsError) validateOpenings(e.target.value);
                  }}
                  onBlur={(e) => validateOpenings(e.target.value)}
                  disabled={saveMutation.isPending}
                  aria-invalid={openingsError ? "true" : undefined}
                />
                {openingsError ? (
                  <span className="field-hint" style={{ color: "var(--color-danger)" }}>
                    {openingsError}
                  </span>
                ) : (
                  <span className="field-hint">How many positions are you hiring for this role?</span>
                )}
              </label>
            </div>

            {form.employmentType && (
              <EmploymentDetailsFields
                type={form.employmentType}
                value={form.details}
                onChange={(details) => setForm({ ...form, details })}
                disabled={saveMutation.isPending}
              />
            )}

            <label className="field">
              <span style={{ display: "flex", alignItems: "center", gap: "0.5rem", justifyContent: "space-between" }}>
                Job Description
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem" }}
                  onClick={() => setForm({ ...form, descriptionVisible: !form.descriptionVisible })}
                  disabled={saveMutation.isPending}
                  aria-pressed={form.descriptionVisible}
                  title={
                    form.descriptionVisible
                      ? "Visible to candidates on the public job page — click to hide"
                      : "Hidden from candidates on the public job page — click to show"
                  }
                >
                  <EyeIcon visible={form.descriptionVisible} />
                  {form.descriptionVisible ? "Visible to candidates" : "Hidden from candidates"}
                </button>
              </span>
              <textarea
                required
                rows={4}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                disabled={saveMutation.isPending}
              />
              {!form.descriptionVisible && (
                <span className="field-hint">
                  This description is saved but hidden from the public job page. Toggle "Visible to
                  candidates" to show it again.
                </span>
              )}
            </label>

            {formError && <Alert>{formError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? (
                  <Spinner label={editingJob ? "Saving…" : "Creating…"} />
                ) : editingJob ? (
                  "Save changes"
                ) : (
                  "Create job"
                )}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeForm}
                disabled={saveMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingClose && (
        <ConfirmDialog
          title="Close this job?"
          message={`This closes "${pendingClose.title}". It stays in your job history and any existing applications are unaffected — you can reopen it at any time.`}
          confirmLabel="Close job"
          isConfirming={statusMutation.isPending}
          onCancel={() => setPendingClose(null)}
          onConfirm={() =>
            statusMutation.mutate({ jobId: pendingClose.id, status: "CLOSED", label: "Close" })
          }
        />
      )}

      {pendingDelete && (
        <Modal title="Delete job" onClose={closeDeleteModal}>
          <form onSubmit={handleDeleteSubmit}>
            <p>
              <strong>Job:</strong> {pendingDelete.title}
            </p>
            <p className="muted">
              This removes "{pendingDelete.title}" from your active job list. Any applications
              against it are preserved, and this action is recorded in Activities.
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
                placeholder="e.g. Requisition cancelled"
              />
            </label>

            {deleteError && <Alert>{deleteError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-danger" disabled={deleteMutation.isPending}>
                {deleteMutation.isPending ? <Spinner label="Deleting…" /> : "Delete job"}
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
