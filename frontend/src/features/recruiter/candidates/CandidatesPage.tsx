import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { Avatar } from "../../../shared/components/Avatar";
import { EmptyState } from "../../../shared/components/EmptyState";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import type { CandidateResponse, CandidateSource } from "../../../types/recruitment";
import { useAuth } from "../../auth/AuthContext";
import { listJobs } from "../jobs/api";
import {
  addApplicationWithResume,
  createCandidate,
  deleteCandidate,
  getCandidate,
  listCandidates,
} from "./api";
import { ReapplyPermissionPanel } from "./ReapplyPermissionPanel";
import { canGrantReapply } from "./reapplyPermission";

/** Roles a recruiter-added candidate can be put forward for — the same set
 * the candidate detail page offers. */
const SELECTABLE_JOB_STATUSES = ["OPEN", "DRAFT", "ON_HOLD"];

const CANDIDATES_QUERY_KEY = ["recruiter", "candidates"];

const SOURCE_BADGE: Record<CandidateSource, string> = {
  PORTAL: "badge-active",
  RECRUITER_ADDED: "badge-inactive",
  CAMPUS_IMPORT: "badge-warn",
  REFERRAL: "badge-inactive",
  OTHER: "badge-inactive",
};

export function CandidatesPage() {
  const { accessToken, user } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [showForm, setShowForm] = useState(false);

  const candidatesQuery = useQuery({
    queryKey: CANDIDATES_QUERY_KEY,
    queryFn: () => listCandidates(accessToken as string),
    enabled: accessToken !== null,
  });

  const [search, setSearch] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [location, setLocation] = useState("");
  const [currentTitle, setCurrentTitle] = useState("");
  const [yearsExperience, setYearsExperience] = useState("");
  // Optional in this form: HR often adds a candidate before deciding which
  // role to put them forward for. Supplied together, they create the
  // application and run AI screening in the same step.
  const [jobId, setJobId] = useState("");
  const [resume, setResume] = useState<File | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<CandidateResponse | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const jobsQuery = useQuery({
    queryKey: ["recruiter", "jobs"],
    queryFn: () => listJobs(accessToken as string),
    enabled: accessToken !== null,
  });

  const selectableJobs = (jobsQuery.data ?? []).filter(
    (job) => SELECTABLE_JOB_STATUSES.includes(job.status) && !job.deleted_at,
  );

  /**
   * Create the candidate, then — when HR also chose a role and attached a
   * resume — create the application and run the existing AI screening on it.
   *
   * A candidate who already exists is never duplicated: the backend refuses
   * the create and names the profile it collided with, and we continue on
   * *that* candidate, so the resume and role land on the existing record
   * along with all their history.
   */
  const createCandidateMutation = useMutation({
    mutationFn: async () => {
      const token = accessToken as string;
      let candidate: CandidateResponse | null = null;
      let reusedExisting = false;
      try {
        candidate = await createCandidate(
          {
            email,
            full_name: fullName,
            phone: phone || null,
            location: location || null,
            current_title: currentTitle || null,
            years_experience: yearsExperience ? Number(yearsExperience) : null,
          },
          token,
        );
      } catch (err) {
        const existingId =
          err instanceof ApiError && typeof err.data?.existing_candidate_id === "string"
            ? err.data.existing_candidate_id
            : null;
        // Only worth continuing if there is something to add to that
        // profile; otherwise the collision *is* the answer HR needs.
        if (!existingId || !jobId || !resume) throw err;
        candidate = await getCandidate(existingId, token);
        reusedExisting = true;
      }

      if (!jobId || !resume) {
        return { candidate, reusedExisting, application: null };
      }
      const result = await addApplicationWithResume(
        candidate.id,
        { jobId, resume, runScreening: true },
        token,
      );
      return { candidate, reusedExisting, application: result };
    },
    onSuccess: ({ candidate, reusedExisting, application }) => {
      const screened =
        application?.screening?.recommendation ?? application?.screening?.decision ?? null;
      if (application) {
        const who = reusedExisting
          ? `${candidate.full_name} already existed — added to`
          : `${candidate.full_name} added to`;
        showToast(
          `${who} ${application.application.job_title}.${screened ? ` AI screening: ${screened}.` : ""}`,
          "success",
        );
      } else {
        showToast("Candidate added.", "success");
      }
      setFullName("");
      setEmail("");
      setPhone("");
      setLocation("");
      setCurrentTitle("");
      setYearsExperience("");
      setJobId("");
      setResume(null);
      setFormError(null);
      setShowForm(false);
      void queryClient.invalidateQueries({ queryKey: CANDIDATES_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "applications"] });
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    // Both or neither: a role with no resume has nothing to screen, and a
    // resume with no role has nowhere to go.
    if (Boolean(jobId) !== Boolean(resume)) {
      setFormError(
        jobId
          ? "Attach the candidate's resume, or clear the applying role."
          : "Select the applying role, or remove the resume.",
      );
      return;
    }
    createCandidateMutation.mutate();
  }

  const deleteMutation = useMutation({
    mutationFn: () => deleteCandidate(pendingDelete!.id, deleteReason.trim(), accessToken as string),
    onSuccess: (_data, _vars) => {
      showToast(`${pendingDelete?.full_name} was deleted.`, "success");
      setPendingDelete(null);
      setDeleteReason("");
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: CANDIDATES_QUERY_KEY });
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openDeleteModal(candidate: CandidateResponse) {
    setPendingDelete(candidate);
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

  const canManageCandidates = !(
    candidatesQuery.error instanceof ApiError && candidatesQuery.error.status === 403
  );

  const filteredCandidates = useMemo(() => {
    if (!candidatesQuery.data) return [];
    const term = search.trim().toLowerCase();
    if (!term) return candidatesQuery.data;
    return candidatesQuery.data.filter(
      (candidate) =>
        candidate.full_name.toLowerCase().includes(term) ||
        candidate.email.toLowerCase().includes(term) ||
        (candidate.current_title ?? "").toLowerCase().includes(term) ||
        (candidate.location ?? "").toLowerCase().includes(term),
    );
  }, [candidatesQuery.data, search]);

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Candidates</h1>
          <p className="muted">Everyone in your talent pipeline.</p>
        </div>
        {canManageCandidates && (
          <button type="button" className="btn btn-primary" onClick={() => setShowForm(true)}>
            + Add candidate
          </button>
        )}
      </div>

      {canGrantReapply(user?.roles) && <ReapplyPermissionPanel />}

      {candidatesQuery.isPending && <SkeletonTable columns={8} />}

      {candidatesQuery.isError && !canManageCandidates && (
        <Alert>You do not have permission to view candidates.</Alert>
      )}
      {candidatesQuery.isError && canManageCandidates && (
        <Alert>
          {candidatesQuery.error instanceof ApiError
            ? candidatesQuery.error.message
            : "Could not load candidates."}
        </Alert>
      )}

      {candidatesQuery.isSuccess && (
        <section className="stack-lg" style={{ gap: "1rem" }}>
          {candidatesQuery.data.length === 0 ? (
            <EmptyState icon="candidates" title="No candidates yet">
              They'll show up here automatically once your career site starts receiving applications.
            </EmptyState>
          ) : (
            <>
              <div className="toolbar">
                <input
                  className="search-input"
                  placeholder="Search by name, email, title, or location…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Name</th>
                      <th>Email</th>
                      <th>Current title</th>
                      <th>Location</th>
                      <th>Experience</th>
                      <th>Source</th>
                      {canManageCandidates && <th>Actions</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredCandidates.map((candidate, index) => (
                      <tr
                        key={candidate.id}
                        className="clickable-row"
                        onClick={() => navigate(`/recruiter/candidates/${candidate.id}`)}
                      >
                        <td>{index + 1}</td>
                        <td>
                          <span className="person-cell">
                            <Avatar name={candidate.full_name} />
                            <span>{candidate.full_name}</span>
                          </span>
                        </td>
                        <td>{candidate.email}</td>
                        <td>{candidate.current_title ?? "—"}</td>
                        <td>{candidate.location ?? "—"}</td>
                        <td>
                          {candidate.years_experience !== null
                            ? `${candidate.years_experience} yrs`
                            : "—"}
                        </td>
                        <td>
                          <span className={`badge ${SOURCE_BADGE[candidate.source]}`}>
                            {candidate.source}
                          </span>
                        </td>
                        {canManageCandidates && (
                          <td onClick={(e) => e.stopPropagation()}>
                            <button
                              type="button"
                              className="btn btn-danger btn-sm"
                              onClick={() => openDeleteModal(candidate)}
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
            </>
          )}
        </section>
      )}

      {canManageCandidates && showForm && (
        <Modal title="Add a candidate" onClose={() => setShowForm(false)}>
          <form onSubmit={handleSubmit}>
            <label className="field">
              <span>Full name</span>
              <input
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                disabled={createCandidateMutation.isPending}
              />
            </label>

            <label className="field">
              <span>Email</span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={createCandidateMutation.isPending}
              />
            </label>

            <label className="field">
              <span>Phone</span>
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                disabled={createCandidateMutation.isPending}
              />
            </label>

            <label className="field">
              <span>Location</span>
              <input
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                disabled={createCandidateMutation.isPending}
              />
            </label>

            <label className="field">
              <span>Current title</span>
              <input
                value={currentTitle}
                onChange={(e) => setCurrentTitle(e.target.value)}
                disabled={createCandidateMutation.isPending}
              />
            </label>

            <label className="field">
              <span>Years of experience</span>
              <input
                type="number"
                min={0}
                max={80}
                value={yearsExperience}
                onChange={(e) => setYearsExperience(e.target.value)}
                disabled={createCandidateMutation.isPending}
              />
            </label>

            <fieldset className="field" style={{ border: "none", padding: 0, margin: 0 }}>
              <legend style={{ padding: 0, marginBottom: "0.35rem" }}>
                Applying role <span className="muted">(optional)</span>
              </legend>
              <p className="muted" style={{ fontSize: "0.82rem", marginTop: 0 }}>
                Choose a role and attach the resume to create the application now and run AI
                screening against that role. AI screening is advisory — you stay the decision
                maker.
              </p>

              <label className="field">
                <span>Role</span>
                <select
                  value={jobId}
                  onChange={(e) => {
                    setJobId(e.target.value);
                    if (formError) setFormError(null);
                  }}
                  disabled={createCandidateMutation.isPending || jobsQuery.isPending}
                >
                  <option value="">{jobsQuery.isPending ? "Loading roles…" : "No role yet"}</option>
                  {selectableJobs.map((job) => (
                    <option key={job.id} value={job.id}>
                      {job.title}
                      {job.department ? ` — ${job.department}` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Add resume</span>
                <input
                  type="file"
                  accept=".pdf,.doc,.docx,.txt"
                  onChange={(e) => {
                    setResume(e.target.files?.[0] ?? null);
                    if (formError) setFormError(null);
                  }}
                  disabled={createCandidateMutation.isPending}
                />
              </label>
            </fieldset>

            {formError && <Alert>{formError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={createCandidateMutation.isPending}
              >
                {createCandidateMutation.isPending ? <Spinner label="Adding…" /> : "Add candidate"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowForm(false)}
                disabled={createCandidateMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingDelete && (
        <Modal title="Delete candidate" onClose={closeDeleteModal}>
          <form onSubmit={handleDeleteSubmit}>
            <p>
              <strong>Candidate:</strong> {pendingDelete.full_name}
              <br />
              <strong>Email:</strong> {pendingDelete.email}
            </p>
            <p className="muted">
              This removes {pendingDelete.full_name} from your active candidate list. Their
              applications and history are preserved, and this action is recorded in Activities.
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
                placeholder="e.g. Duplicate candidate record"
              />
            </label>

            {deleteError && <Alert>{deleteError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-danger" disabled={deleteMutation.isPending}>
                {deleteMutation.isPending ? <Spinner label="Deleting…" /> : "Delete Candidate"}
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
