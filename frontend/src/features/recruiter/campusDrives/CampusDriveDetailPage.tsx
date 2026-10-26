import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { BackLink } from "../../../shared/components/BackLink";
import { ConfirmDialog } from "../../../shared/components/ConfirmDialog";
import { Modal } from "../../../shared/components/Modal";
import { Pagination } from "../../../shared/components/Pagination";
import { QrCode } from "../../../shared/components/QrCode";
import { useToast } from "../../../shared/components/ToastContext";
import {
  VERIFICATION_METHOD_LABELS,
  type AffiliationVerificationMethod,
  type CampusDriveFunnelCounts,
  type CampusDriveResponse,
  type CampusDriveStatus,
} from "../../../types/campusDrive";
import { useAuth } from "../../auth/AuthContext";
import { listInstitutions } from "../institutions/api";
import { AffiliationVerificationPanel } from "./AffiliationVerificationPanel";
import { listApplicationsPage } from "../applications/api";
import {
  PAGE_SIZE,
  hasActiveCriteria,
  toApiQuery,
  useApplicationListState,
} from "../applications/applicationFilters";
import { ApplicationListControls, type FilterField } from "../applications/ApplicationListControls";
import { getCampusDrive, getCampusDriveFunnel, regenerateCampusDriveLink, updateCampusDrive } from "./api";

/** What a recruiter can narrow a drive's candidates by. A drive has one job and
 * its own source, so those filters would be noise here. */
const DRIVE_CANDIDATE_FILTERS: FilterField[] = ["status", "qualification", "applied"];

const WORKFLOW_STAGES = ["Registration", "Assessment", "Submitted", "Screening", "Shortlisted"];

const NEXT_ACTIONS: Record<CampusDriveStatus, { label: string; status: CampusDriveStatus; confirm?: boolean }[]> = {
  DRAFT: [{ label: "Activate", status: "ACTIVE" }],
  ACTIVE: [
    { label: "Pause", status: "PAUSED" },
    { label: "Close", status: "CLOSED", confirm: true },
  ],
  PAUSED: [
    { label: "Resume", status: "ACTIVE" },
    { label: "Close", status: "CLOSED", confirm: true },
  ],
  CLOSED: [{ label: "Reopen", status: "ACTIVE" }],
};

const STATUS_BADGE: Record<CampusDriveStatus, string> = {
  DRAFT: "badge-inactive",
  ACTIVE: "badge-active",
  PAUSED: "badge-warn",
  CLOSED: "badge-inactive",
};

const FUNNEL_STAGES: { key: keyof CampusDriveFunnelCounts; label: string }[] = [
  { key: "registered", label: "Registered" },
  { key: "screening", label: "Screened" },
  { key: "assessment_invited", label: "Assessment invited" },
  { key: "assessment_completed", label: "Assessment completed" },
  { key: "assessment_passed", label: "Assessment passed" },
  { key: "assessment_failed", label: "Assessment failed" },
  { key: "shortlisted", label: "Shortlisted" },
  { key: "interview", label: "Interview" },
  { key: "selected", label: "Selected" },
  { key: "rejected", label: "Rejected" },
  { key: "hired", label: "Hired" },
];

const VERIFICATION_STAGES: { key: keyof CampusDriveFunnelCounts; label: string }[] = [
  { key: "verification_pending", label: "Verification pending" },
  { key: "verified", label: "Institution verified" },
  { key: "verification_rejected", label: "Verification rejected" },
];

interface EditFormState {
  name: string;
  institutionId: string;
  verificationMethod: AffiliationVerificationMethod;
  description: string;
  registrationDeadline: string;
}

function driveToEditForm(drive: CampusDriveResponse): EditFormState {
  return {
    name: drive.name,
    institutionId: drive.institution_id,
    verificationMethod: drive.affiliation_verification_method,
    description: drive.description ?? "",
    registrationDeadline: drive.registration_deadline ?? "",
  };
}

export function CampusDriveDetailPage() {
  const { driveId = "" } = useParams<{ driveId: string }>();
  const { accessToken } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [confirmingClose, setConfirmingClose] = useState(false);
  const [regeneratedLink, setRegeneratedLink] = useState<string | null>(null);
  const [showEditForm, setShowEditForm] = useState(false);
  const [editForm, setEditForm] = useState<EditFormState | null>(null);
  const [editError, setEditError] = useState<string | null>(null);

  const driveQueryKey = ["recruiter", "campus-drives", driveId];

  const driveQuery = useQuery({
    queryKey: driveQueryKey,
    queryFn: () => getCampusDrive(driveId, token),
    enabled: accessToken !== null,
  });

  const funnelQuery = useQuery({
    queryKey: ["recruiter", "campus-drives", driveId, "funnel"],
    queryFn: () => getCampusDriveFunnel(driveId, token),
    enabled: accessToken !== null,
  });

  const institutionsQuery = useQuery({
    queryKey: ["recruiter", "institutions", "active"],
    queryFn: () => listInstitutions(token, { activeOnly: true }),
    enabled: accessToken !== null && showEditForm,
  });

  // Search, filters and page live in the URL and are applied by the server —
  // the drive's candidates are never all downloaded to be filtered here.
  const [listState, setListState] = useApplicationListState();
  const apiQuery = toApiQuery(listState, { campus_drive_id: driveId });

  const applicationsQuery = useQuery({
    queryKey: ["recruiter", "applications", "drive", driveId, apiQuery],
    // The page number rides along with the rows so the summary always
    // describes the rows on screen (see ApplicationsPage).
    queryFn: async () => ({
      ...(await listApplicationsPage(apiQuery, token)),
      page: listState.page,
    }),
    enabled: accessToken !== null,
    placeholderData: keepPreviousData,
  });
  const candidates = applicationsQuery.data?.items ?? [];
  const totalCandidates = applicationsQuery.data?.total ?? 0;
  const shownPage = applicationsQuery.data?.page ?? listState.page;
  const criteriaActive = hasActiveCriteria(listState.filters);
  const lastPage = Math.max(1, Math.ceil(totalCandidates / PAGE_SIZE));

  // A page that no longer exists (stale link, or rows removed) -> the last real one.
  useEffect(() => {
    if (
      applicationsQuery.isSuccess &&
      !applicationsQuery.isPlaceholderData &&
      listState.page > lastPage
    ) {
      setListState({ ...listState, page: lastPage });
    }
  }, [applicationsQuery.isSuccess, applicationsQuery.isPlaceholderData, listState, lastPage, setListState]);

  const statusMutation = useMutation({
    mutationFn: (status: CampusDriveStatus) => updateCampusDrive(driveId, { status }, token),
    onSuccess: (_data, status) => {
      showToast(`Drive status updated to ${status}.`, "success");
      setConfirmingClose(false);
      void queryClient.invalidateQueries({ queryKey: driveQueryKey });
    },
    onError: (err) => {
      showToast(err instanceof ApiError ? err.message : "Could not update the drive.", "error");
    },
  });

  const editMutation = useMutation({
    mutationFn: () => {
      if (!editForm) throw new Error("No changes to save.");
      return updateCampusDrive(
        driveId,
        {
          name: editForm.name,
          ...(editForm.institutionId !== driveQuery.data?.institution_id
            ? { institution_id: editForm.institutionId }
            : {}),
          affiliation_verification_method: editForm.verificationMethod,
          description: editForm.description || null,
          registration_deadline: editForm.registrationDeadline || null,
        },
        token,
      );
    },
    onSuccess: () => {
      showToast("Campus drive updated.", "success");
      setShowEditForm(false);
      setEditError(null);
      void queryClient.invalidateQueries({ queryKey: driveQueryKey });
    },
    onError: (err) => {
      setEditError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openEditForm() {
    if (!driveQuery.data) return;
    setEditForm(driveToEditForm(driveQuery.data));
    setEditError(null);
    setShowEditForm(true);
  }

  function handleEditSubmit(event: FormEvent) {
    event.preventDefault();
    editMutation.mutate();
  }

  const regenerateMutation = useMutation({
    mutationFn: () => regenerateCampusDriveLink(driveId, token),
    onSuccess: (drive) => {
      showToast("A new application link was generated. The old link no longer works.", "success");
      if (drive.application_link) setRegeneratedLink(drive.application_link);
      void queryClient.invalidateQueries({ queryKey: driveQueryKey });
    },
    onError: (err) => {
      showToast(err instanceof ApiError ? err.message : "Could not regenerate the link.", "error");
    },
  });

  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      showToast("Link copied to clipboard.", "success");
    } catch {
      showToast("Could not copy the link automatically — copy it manually.", "error");
    }
  }

  if (driveQuery.isPending) return <p role="status">Loading campus drive…</p>;
  if (driveQuery.isError || !driveQuery.data) {
    return (
      <Alert>
        {driveQuery.error instanceof ApiError ? driveQuery.error.message : "Campus drive not found."}
      </Alert>
    );
  }

  const drive = driveQuery.data;

  return (
    <div className="stack-lg">
      <p>
        <BackLink to="/recruiter/campus-drives">Back to campus drives</BackLink>
      </p>

      <div className="page-header">
        <div>
          <h1>{drive.name}</h1>
          <p className="muted">
            {drive.job_title} · {drive.institution_name}
          </p>
          <p className="field-hint">
            Institution verification: {VERIFICATION_METHOD_LABELS[drive.affiliation_verification_method]}
          </p>
          <span className={`badge ${STATUS_BADGE[drive.status]}`}>{drive.status}</span>
        </div>
        <div className="btn-group">
          <button type="button" className="btn btn-ghost btn-sm" onClick={openEditForm}>
            Edit Drive
          </button>
          {NEXT_ACTIONS[drive.status].map((action) => (
            <button
              key={action.status}
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={statusMutation.isPending}
              onClick={() =>
                action.confirm ? setConfirmingClose(true) : statusMutation.mutate(action.status)
              }
            >
              {action.label}
            </button>
          ))}
        </div>
      </div>

      <section className="card">
        <div className="workflow-stepper">
          {WORKFLOW_STAGES.map((stage, index) => (
            <div key={stage} className="workflow-step">
              <span className="workflow-step-dot">{index + 1}</span>
              <span className="workflow-step-label">{stage}</span>
              {index < WORKFLOW_STAGES.length - 1 && <span className="workflow-step-arrow">&rarr;</span>}
            </div>
          ))}
        </div>
        <p className="field-hint" style={{ marginTop: "0.75rem" }}>
          {drive.default_assessment_title
            ? "Candidates register, then are automatically taken to the assessment before moving into screening."
            : "This drive is registration-only — candidates go straight into screening after they register."}
        </p>
      </section>

      {drive.description && (
        <section className="card">
          <h2>Description</h2>
          <p>{drive.description}</p>
        </section>
      )}

      <section className="card stack-sm">
        <div className="page-header">
          <h2>Candidate registration link</h2>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={regenerateMutation.isPending}
            onClick={() => regenerateMutation.mutate()}
          >
            {regenerateMutation.isPending ? "Generating…" : "Regenerate Link"}
          </button>
        </div>
        {regeneratedLink ? (
          <>
            <p className="muted">Share this link or scan the QR code to register candidates:</p>
            <div className="link-copy-row">
              <code className="link-copy-value">{regeneratedLink}</code>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => copyLink(regeneratedLink)}>
                Copy Registration Link
              </button>
              <a className="btn btn-ghost btn-sm" href={regeneratedLink} target="_blank" rel="noreferrer">
                Open link
              </a>
            </div>
            <QrCode value={regeneratedLink} filename={`${drive.name}-registration-qr.png`} />
          </>
        ) : (
          <p className="muted">
            The link was shown once when this drive was created (or last regenerated). Use
            "Regenerate Link" to get a fresh, shareable link and QR code — this invalidates the
            previous one.
          </p>
        )}
        {drive.registration_deadline && (
          <p className="muted">Registration deadline: {new Date(drive.registration_deadline).toLocaleDateString()}</p>
        )}
        {drive.default_assessment_title && (
          <p className="muted">Candidates are auto-invited to: {drive.default_assessment_title}</p>
        )}
      </section>

      <section className="card">
        <h2>Hiring funnel</h2>
        {funnelQuery.isPending && <p role="status">Loading funnel…</p>}
        {funnelQuery.isSuccess && (
          <div className="funnel-grid">
            {[
              ...FUNNEL_STAGES,
              ...(drive.affiliation_verification_method !== "NONE" ? VERIFICATION_STAGES : []),
            ].map((stage) => (
              <div key={stage.key} className="funnel-stat">
                <span className="funnel-stat-value">{funnelQuery.data[stage.key]}</span>
                <span className="funnel-stat-label">{stage.label}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {drive.affiliation_verification_method !== "NONE" && (
        <AffiliationVerificationPanel drive={drive} accessToken={token} />
      )}

      <section className="card">
        <h2>Candidates in this drive</h2>
        {applicationsQuery.isPending && <p role="status">Loading…</p>}
        {applicationsQuery.isError && (
          <Alert>
            {applicationsQuery.error instanceof ApiError
              ? applicationsQuery.error.message
              : "Could not load this drive's candidates."}
          </Alert>
        )}
        {/* Nothing to search until the drive has at least one candidate. */}
        {((applicationsQuery.isSuccess && (totalCandidates > 0 || criteriaActive)) ||
          applicationsQuery.isError) && (
          <ApplicationListControls
            state={listState}
            onChange={setListState}
            fields={DRIVE_CANDIDATE_FILTERS}
            searchLabel="Search candidates in this drive"
            searchPlaceholder="Search by name, email or phone…"
          />
        )}
        {applicationsQuery.isSuccess && totalCandidates === 0 && !criteriaActive && (
          <p className="muted">
            No applications yet. Candidates who apply via this drive's link appear here automatically.
          </p>
        )}
        {applicationsQuery.isSuccess && totalCandidates === 0 && criteriaActive && (
          <p className="muted">No candidates in this drive match these filters.</p>
        )}
        {applicationsQuery.isSuccess && candidates.length > 0 && (
          <>
            <div
              className={
                applicationsQuery.isPlaceholderData ? "table-scroll is-refreshing" : "table-scroll"
              }
              aria-busy={applicationsQuery.isPlaceholderData}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Candidate</th>
                    <th>Status</th>
                    <th>Applied</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((application) => (
                    <tr
                      key={application.id}
                      className="clickable-row"
                      onClick={() => navigate(`/recruiter/applications/${application.id}`)}
                    >
                      <td>
                        {application.candidate_full_name}
                        <span className="cell-sub">
                          {[application.candidate_email, application.candidate_phone]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </td>
                      <td>
                        <span className="badge badge-active">{application.status}</span>
                      </td>
                      <td>{new Date(application.applied_at).toLocaleDateString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={shownPage}
              pageSize={PAGE_SIZE}
              total={totalCandidates}
              noun={totalCandidates === 1 ? "candidate" : "candidates"}
              disabled={applicationsQuery.isPlaceholderData}
              onPageChange={(page) => setListState({ ...listState, page })}
            />
          </>
        )}
      </section>

      {confirmingClose && (
        <ConfirmDialog
          title="Close this campus drive?"
          message={`This closes "${drive.name}" at ${drive.college_name}. Existing applications are unaffected, and the drive stays in your history — you can reopen it at any time.`}
          confirmLabel="Close drive"
          isConfirming={statusMutation.isPending}
          onCancel={() => setConfirmingClose(false)}
          onConfirm={() => statusMutation.mutate("CLOSED")}
        />
      )}

      {showEditForm && editForm && (
        <Modal title={`Edit ${drive.name}`} onClose={() => setShowEditForm(false)}>
          <form onSubmit={handleEditSubmit}>
            <label className="field">
              <span>Drive name</span>
              <input
                required
                value={editForm.name}
                onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                disabled={editMutation.isPending}
              />
            </label>
            <label className="field">
              <span>Institution</span>
              <select
                required
                value={editForm.institutionId}
                onChange={(e) => setEditForm({ ...editForm, institutionId: e.target.value })}
                disabled={editMutation.isPending || drive.application_count > 0}
              >
                <option value={drive.institution_id}>{drive.institution_name}</option>
                {institutionsQuery.data
                  ?.filter((institution) => institution.id !== drive.institution_id)
                  .map((institution) => (
                    <option key={institution.id} value={institution.id}>
                      {institution.name}
                    </option>
                  ))}
              </select>
              {drive.application_count > 0 && (
                <span className="field-hint">
                  This drive already has applications, so its institution can't change.
                </span>
              )}
            </label>
            <label className="field">
              <span>Institution verification</span>
              <select
                value={editForm.verificationMethod}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    verificationMethod: e.target.value as AffiliationVerificationMethod,
                  })
                }
                disabled={editMutation.isPending}
              >
                {(Object.keys(VERIFICATION_METHOD_LABELS) as AffiliationVerificationMethod[]).map((method) => (
                  <option key={method} value={method}>
                    {VERIFICATION_METHOD_LABELS[method]}
                  </option>
                ))}
              </select>
              <span className="field-hint">Applies to applications received from now on.</span>
            </label>
            <label className="field">
              <span>Description</span>
              <textarea
                rows={3}
                value={editForm.description}
                onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                disabled={editMutation.isPending}
              />
            </label>
            <label className="field">
              <span>Registration deadline</span>
              <input
                type="date"
                value={editForm.registrationDeadline}
                onChange={(e) => setEditForm({ ...editForm, registrationDeadline: e.target.value })}
                disabled={editMutation.isPending}
              />
            </label>

            {editError && <Alert>{editError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={editMutation.isPending}>
                {editMutation.isPending ? "Saving…" : "Save changes"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowEditForm(false)}
                disabled={editMutation.isPending}
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
