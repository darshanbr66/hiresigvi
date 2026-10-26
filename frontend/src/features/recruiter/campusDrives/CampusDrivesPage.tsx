import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Modal } from "../../../shared/components/Modal";
import { QrCode } from "../../../shared/components/QrCode";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import {
  VERIFICATION_METHOD_LABELS,
  type AffiliationVerificationMethod,
  type CampusDriveResponse,
  type CampusDriveStatus,
} from "../../../types/campusDrive";
import { useAuth } from "../../auth/AuthContext";
import { listAssessments } from "../assessments/api";
import { listInstitutions } from "../institutions/api";
import { listJobs } from "../jobs/api";
import { createCampusDrive, deleteCampusDrive, getCampusReport, listCampusDrives } from "./api";

const DRIVES_QUERY_KEY = ["recruiter", "campus-drives"];

const STATUS_BADGE: Record<CampusDriveStatus, string> = {
  DRAFT: "badge-inactive",
  ACTIVE: "badge-active",
  PAUSED: "badge-warn",
  CLOSED: "badge-inactive",
};

interface DriveFormState {
  name: string;
  institutionSource: "existing" | "new";
  institutionId: string;
  newInstitutionName: string;
  newInstitutionCity: string;
  newInstitutionDomains: string;
  verificationMethod: AffiliationVerificationMethod;
  jobSource: "existing" | "new";
  jobId: string;
  newJobTitle: string;
  newJobDescription: string;
  description: string;
  registrationDeadline: string;
  assessmentMode: "none" | "assessment";
  defaultAssessmentId: string;
}

const EMPTY_FORM: DriveFormState = {
  name: "",
  institutionSource: "existing",
  institutionId: "",
  newInstitutionName: "",
  newInstitutionCity: "",
  newInstitutionDomains: "",
  verificationMethod: "NONE",
  jobSource: "existing",
  jobId: "",
  newJobTitle: "",
  newJobDescription: "",
  description: "",
  registrationDeadline: "",
  assessmentMode: "none",
  defaultAssessmentId: "",
};

export function CampusDrivesPage() {
  const { accessToken } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();

  const drivesQuery = useQuery({
    queryKey: DRIVES_QUERY_KEY,
    queryFn: () => listCampusDrives(token),
    enabled: accessToken !== null,
  });

  const jobsQuery = useQuery({
    queryKey: ["recruiter", "jobs"],
    queryFn: () => listJobs(token),
    enabled: accessToken !== null,
  });

  const assessmentsQuery = useQuery({
    queryKey: ["recruiter", "assessments"],
    queryFn: () => listAssessments(token),
    enabled: accessToken !== null,
  });
  // Only a published assessment can be attached to a drive.
  const publishedAssessments = assessmentsQuery.data?.filter((a) => a.status === "PUBLISHED") ?? [];

  const institutionsQuery = useQuery({
    queryKey: ["recruiter", "institutions", "active"],
    queryFn: () => listInstitutions(token, { activeOnly: true }),
    enabled: accessToken !== null,
  });

  const [institutionFilter, setInstitutionFilter] = useState("");
  const [showReport, setShowReport] = useState(false);
  const reportQuery = useQuery({
    queryKey: ["recruiter", "campus-drives", "report", institutionFilter],
    queryFn: () => getCampusReport(token, institutionFilter || undefined),
    enabled: accessToken !== null && showReport,
  });

  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<DriveFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [createdLink, setCreatedLink] = useState<{ name: string; link: string } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<CampusDriveResponse | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  function openCreateForm() {
    setForm(EMPTY_FORM);
    setFormError(null);
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
  }

  const createMutation = useMutation({
    mutationFn: () =>
      createCampusDrive(
        {
          name: form.name,
          ...(form.institutionSource === "existing"
            ? { institution_id: form.institutionId }
            : {
                new_institution: {
                  name: form.newInstitutionName.trim(),
                  city: form.newInstitutionCity.trim() || null,
                  email_domains: form.newInstitutionDomains
                    .split(/[\s,]+/)
                    .map((d) => d.trim())
                    .filter(Boolean),
                },
              }),
          affiliation_verification_method: form.verificationMethod,
          job_id: form.jobSource === "existing" ? form.jobId : null,
          new_job_title: form.jobSource === "new" ? form.newJobTitle : null,
          new_job_description: form.jobSource === "new" ? form.newJobDescription : null,
          description: form.description || null,
          registration_deadline: form.registrationDeadline || null,
          default_assessment_id: form.assessmentMode === "assessment" ? form.defaultAssessmentId : null,
        },
        token,
      ),
    onSuccess: (drive) => {
      showToast("Campus drive created.", "success");
      setFormError(null);
      setShowForm(false);
      if (drive.application_link) {
        setCreatedLink({ name: drive.name, link: drive.application_link });
      }
      void queryClient.invalidateQueries({ queryKey: DRIVES_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "institutions"] });
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  const deleteMutation = useMutation({
    mutationFn: () => deleteCampusDrive(pendingDelete!.id, { reason: deleteReason.trim() }, token),
    onSuccess: () => {
      showToast(`${pendingDelete?.name} was deleted.`, "success");
      setPendingDelete(null);
      setDeleteReason("");
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: DRIVES_QUERY_KEY });
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openDeleteModal(drive: CampusDriveResponse) {
    setPendingDelete(drive);
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

  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      showToast("Link copied to clipboard.", "success");
    } catch {
      showToast("Could not copy the link automatically — copy it manually.", "error");
    }
  }

  const canManage = !(drivesQuery.error instanceof ApiError && drivesQuery.error.status === 403);
  const isFormValid =
    form.name.trim() !== "" &&
    (form.institutionSource === "existing"
      ? form.institutionId !== ""
      : form.newInstitutionName.trim() !== "") &&
    (form.jobSource === "existing"
      ? form.jobId !== ""
      : form.newJobTitle.trim() !== "" && form.newJobDescription.trim() !== "") &&
    (form.assessmentMode === "none" || form.defaultAssessmentId !== "");

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Campus Drives</h1>
          <p className="muted">Mass hiring events tied to a job and a college, with their own public link.</p>
        </div>
        <div className="btn-group">
          <button
            type="button"
            className="btn btn-ghost"
            aria-expanded={showReport}
            onClick={() => setShowReport((v) => !v)}
          >
            {showReport ? "Hide campus report" : "Campus report"}
          </button>
          {canManage && (
            <button type="button" className="btn btn-primary" onClick={openCreateForm}>
              + New drive
            </button>
          )}
        </div>
      </div>

      {(institutionsQuery.data?.length ?? 0) > 0 && (
        <label className="field" style={{ maxWidth: "22rem" }}>
          <span>Filter by institution</span>
          <select value={institutionFilter} onChange={(e) => setInstitutionFilter(e.target.value)}>
            <option value="">All institutions</option>
            {institutionsQuery.data?.map((institution) => (
              <option key={institution.id} value={institution.id}>
                {institution.name}
              </option>
            ))}
          </select>
        </label>
      )}

      {showReport && (
        <section className="card stack-sm" aria-label="Campus report">
          <h2>Campus report</h2>
          {reportQuery.isPending && <SkeletonTable columns={8} />}
          {reportQuery.isError && <Alert>Could not load the campus report.</Alert>}
          {reportQuery.isSuccess && reportQuery.data.length === 0 && (
            <p className="muted">No campus drives to report on yet.</p>
          )}
          {reportQuery.isSuccess && reportQuery.data.length > 0 && (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Institution</th>
                    <th>Drive</th>
                    <th>Applications</th>
                    <th>Verified / pending / rejected</th>
                    <th>Tests invited / completed</th>
                    <th>Passed / failed / to grade</th>
                    <th>Interview</th>
                    <th>Selected / hired / rejected</th>
                  </tr>
                </thead>
                <tbody>
                  {reportQuery.data.map((row) => (
                    <tr key={row.drive_id}>
                      <td>{row.institution_name}</td>
                      <td>
                        {row.drive_name}
                        <span className="cell-sub">{row.job_title}</span>
                      </td>
                      <td>{row.applications}</td>
                      <td>
                        {row.affiliation_verification_method === "NONE"
                          ? "Not required"
                          : `${row.verified} / ${row.verification_pending} / ${row.verification_rejected}`}
                      </td>
                      <td>
                        {row.assessment_invited} / {row.assessment_completed}
                      </td>
                      <td>
                        {row.assessment_passed} / {row.assessment_failed} / {row.assessment_pending_review}
                      </td>
                      <td>{row.interview}</td>
                      <td>
                        {row.selected} / {row.hired} / {row.rejected}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {createdLink && (
        <section className="card stack-sm" role="status">
          <p>
            <strong>{createdLink.name}</strong> is ready. Share this link or scan the QR code to
            register candidates — it won't be shown again (you can always generate a new one from
            the drive page):
          </p>
          <div className="link-copy-row">
            <code className="link-copy-value">{createdLink.link}</code>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => copyLink(createdLink.link)}>
              Copy link
            </button>
            <a className="btn btn-ghost btn-sm" href={createdLink.link} target="_blank" rel="noreferrer">
              Open link
            </a>
          </div>
          <QrCode value={createdLink.link} filename={`${createdLink.name}-registration-qr.png`} />
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            style={{ alignSelf: "flex-start" }}
            onClick={() => setCreatedLink(null)}
          >
            Dismiss
          </button>
        </section>
      )}

      {drivesQuery.isPending && <SkeletonTable columns={6} />}
      {drivesQuery.isError && !canManage && (
        <Alert>You do not have permission to view campus drives.</Alert>
      )}

      {drivesQuery.isSuccess &&
        (drivesQuery.data.length === 0 ? (
          <EmptyState icon="campus" title="No campus drives yet">
            Create one to start mass hiring for a specific job and college.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Drive</th>
                  <th>Job</th>
                  <th>Institution</th>
                  <th>Status</th>
                  <th>Applications</th>
                  {canManage && <th>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {drivesQuery.data
                  .filter((drive) => !institutionFilter || drive.institution_id === institutionFilter)
                  .map((drive, index) => (
                  <tr
                    key={drive.id}
                    className="clickable-row"
                    onClick={() => navigate(`/recruiter/campus-drives/${drive.id}`)}
                  >
                    <td>{index + 1}</td>
                    <td>{drive.name}</td>
                    <td>{drive.job_title}</td>
                    <td>{drive.institution_name}</td>
                    <td>
                      <span className={`badge ${STATUS_BADGE[drive.status]}`}>{drive.status}</span>
                    </td>
                    <td>{drive.application_count}</td>
                    {canManage && (
                      <td onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          className="btn btn-danger btn-sm"
                          onClick={() => openDeleteModal(drive)}
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
        ))}

      {showForm && (
        <Modal title="Create a campus drive" onClose={closeForm} wide>
          <form onSubmit={handleSubmit}>
            <label className="field">
              <span>Drive name</span>
              <input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                disabled={createMutation.isPending}
              />
            </label>

            <fieldset className="field">
              <legend>College / institution</legend>
              <div className="segmented-control">
                <button
                  type="button"
                  className={`btn btn-sm ${form.institutionSource === "existing" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setForm({ ...form, institutionSource: "existing" })}
                  disabled={createMutation.isPending}
                >
                  Registered institution
                </button>
                <button
                  type="button"
                  className={`btn btn-sm ${form.institutionSource === "new" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setForm({ ...form, institutionSource: "new" })}
                  disabled={createMutation.isPending}
                >
                  + Register new institution
                </button>
              </div>
              {form.institutionSource === "existing" ? (
                <label className="field">
                  <span>Institution</span>
                  <select
                    required
                    value={form.institutionId}
                    onChange={(e) => setForm({ ...form, institutionId: e.target.value })}
                    disabled={createMutation.isPending}
                  >
                    <option value="" disabled>
                      Select an institution…
                    </option>
                    {institutionsQuery.data?.map((institution) => (
                      <option key={institution.id} value={institution.id}>
                        {institution.name}
                        {institution.city ? ` — ${institution.city}` : ""}
                      </option>
                    ))}
                  </select>
                  {(institutionsQuery.data?.length ?? 0) === 0 && (
                    <span className="field-hint">
                      No institutions registered yet — use "+ Register new institution".
                    </span>
                  )}
                </label>
              ) : (
                <div className="field-row">
                  <label className="field">
                    <span>Institution name</span>
                    <input
                      required
                      value={form.newInstitutionName}
                      onChange={(e) => setForm({ ...form, newInstitutionName: e.target.value })}
                      disabled={createMutation.isPending}
                    />
                  </label>
                  <label className="field">
                    <span>
                      City <span className="optional-mark">(Optional)</span>
                    </span>
                    <input
                      value={form.newInstitutionCity}
                      onChange={(e) => setForm({ ...form, newInstitutionCity: e.target.value })}
                      disabled={createMutation.isPending}
                    />
                  </label>
                  <label className="field">
                    <span>
                      Official email domain(s) <span className="optional-mark">(Optional)</span>
                    </span>
                    <input
                      value={form.newInstitutionDomains}
                      onChange={(e) => setForm({ ...form, newInstitutionDomains: e.target.value })}
                      placeholder="college.edu"
                      disabled={createMutation.isPending}
                    />
                  </label>
                </div>
              )}
            </fieldset>

            <label className="field">
              <span>Institution verification</span>
              <select
                value={form.verificationMethod}
                onChange={(e) =>
                  setForm({ ...form, verificationMethod: e.target.value as AffiliationVerificationMethod })
                }
                disabled={createMutation.isPending}
              >
                {(Object.keys(VERIFICATION_METHOD_LABELS) as AffiliationVerificationMethod[]).map((method) => (
                  <option key={method} value={method}>
                    {VERIFICATION_METHOD_LABELS[method]}
                  </option>
                ))}
              </select>
              <span className="field-hint">
                Opening the drive link never proves a candidate studies there. Email-domain
                verification needs the institution's official domain on record.
              </span>
            </label>

            <fieldset className="field">
              <legend>Job for this drive</legend>
              <div className="segmented-control">
                <button
                  type="button"
                  className={`btn btn-sm ${form.jobSource === "existing" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setForm({ ...form, jobSource: "existing" })}
                  disabled={createMutation.isPending}
                >
                  Use existing job
                </button>
                <button
                  type="button"
                  className={`btn btn-sm ${form.jobSource === "new" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setForm({ ...form, jobSource: "new" })}
                  disabled={createMutation.isPending}
                >
                  + Add new job
                </button>
              </div>

              {form.jobSource === "existing" ? (
                <label className="field">
                  <span>Job</span>
                  <select
                    required
                    value={form.jobId}
                    onChange={(e) => setForm({ ...form, jobId: e.target.value })}
                    disabled={createMutation.isPending}
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
                  {(jobsQuery.data?.length ?? 0) === 0 && (
                    <span className="field-hint">No jobs yet — switch to "+ Add new job" instead.</span>
                  )}
                </label>
              ) : (
                <>
                  <label className="field">
                    <span>New job title</span>
                    <input
                      required
                      value={form.newJobTitle}
                      onChange={(e) => setForm({ ...form, newJobTitle: e.target.value })}
                      disabled={createMutation.isPending}
                    />
                  </label>
                  <label className="field">
                    <span>New job description</span>
                    <textarea
                      required
                      rows={3}
                      value={form.newJobDescription}
                      onChange={(e) => setForm({ ...form, newJobDescription: e.target.value })}
                      disabled={createMutation.isPending}
                    />
                  </label>
                </>
              )}
            </fieldset>

            <label className="field">
              <span>Description</span>
              <textarea
                rows={3}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                disabled={createMutation.isPending}
              />
            </label>

            <div className="field-row">
              <label className="field">
                <span>Registration deadline</span>
                <input
                  type="date"
                  value={form.registrationDeadline}
                  onChange={(e) => setForm({ ...form, registrationDeadline: e.target.value })}
                  disabled={createMutation.isPending}
                />
              </label>

            </div>

            <fieldset className="field">
              <legend>Assessment mode</legend>
              <div className="stack-sm">
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <input
                    type="radio"
                    name="assessment-mode"
                    checked={form.assessmentMode === "none"}
                    onChange={() => setForm({ ...form, assessmentMode: "none", defaultAssessmentId: "" })}
                    disabled={createMutation.isPending}
                    style={{ width: "auto" }}
                  />
                  Registration only
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <input
                    type="radio"
                    name="assessment-mode"
                    checked={form.assessmentMode === "assessment"}
                    onChange={() => setForm({ ...form, assessmentMode: "assessment" })}
                    disabled={createMutation.isPending}
                    style={{ width: "auto" }}
                  />
                  Registration + Assessment
                </label>
              </div>

              {form.assessmentMode === "assessment" && (
                <label className="field" style={{ marginTop: "0.75rem" }}>
                  <span>Select assessment</span>
                  <select
                    required
                    value={form.defaultAssessmentId}
                    onChange={(e) => setForm({ ...form, defaultAssessmentId: e.target.value })}
                    disabled={createMutation.isPending}
                  >
                    <option value="" disabled>
                      Select an existing assessment…
                    </option>
                    {publishedAssessments.map((assessment) => (
                      <option key={assessment.id} value={assessment.id}>
                        {assessment.title}
                      </option>
                    ))}
                  </select>
                  <span className="field-hint">
                    Candidates are automatically invited to it right after they register.{" "}
                    {publishedAssessments.length === 0
                      ? "You don't have any published assessments yet — "
                      : "Don't see the one you need? "}
                    <a href="/recruiter/assessments" target="_blank" rel="noreferrer">
                      create a new assessment
                    </a>{" "}
                    first, then come back here to select it.
                  </span>
                </label>
              )}
            </fieldset>

            {formError && <Alert>{formError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={createMutation.isPending || !isFormValid}
              >
                {createMutation.isPending ? <Spinner label="Creating…" /> : "Create drive"}
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
        <Modal title="Delete campus drive" onClose={closeDeleteModal}>
          <form onSubmit={handleDeleteSubmit}>
            <p>
              <strong>Drive:</strong> {pendingDelete.name}
            </p>
            <p className="muted">
              This removes "{pendingDelete.name}" from your active drives list. Registrations and
              applications sourced from it are preserved, and this action is recorded in
              Activities.
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
                placeholder="e.g. Drive cancelled"
              />
            </label>

            {deleteError && <Alert>{deleteError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-danger" disabled={deleteMutation.isPending}>
                {deleteMutation.isPending ? <Spinner label="Deleting…" /> : "Delete drive"}
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
