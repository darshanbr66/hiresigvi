import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { triggerBlobDownload } from "../../../lib/downloadBlob";
import { Alert } from "../../../shared/components/Alert";
import { Avatar } from "../../../shared/components/Avatar";
import { BackLink } from "../../../shared/components/BackLink";
import { Collapsible } from "../../../shared/components/Collapsible";
import { Icon } from "../../../shared/components/Icon";
import { PipelineTrack } from "../../../shared/components/PipelineTrack";
import { EmptyState } from "../../../shared/components/EmptyState";
import { SkeletonCard } from "../../../shared/components/Skeleton";
import { statusTone, toneBadgeClass } from "../../../shared/lib/statusTone";
import { ResumePreviewModal } from "../../../shared/components/ResumePreviewModal";
import { useAuth } from "../../auth/AuthContext";
import { downloadResume, listApplicationsForCandidate } from "../applications/api";
import { getCandidate, getReapplyStatus } from "./api";
import { AddResumeAndRoleModal } from "./AddResumeAndRoleModal";
import { CandidateJourney } from "./CandidateJourney";
import { GrantReapplyModal } from "./GrantReapplyModal";
import { canGrantReapply as hasReapplyGrantRole } from "./reapplyPermission";

function formatDay(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function CandidateDetailPage() {
  const { candidateId = "" } = useParams<{ candidateId: string }>();
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const navigate = useNavigate();
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState<{ applicationId: string; filename: string } | null>(null);
  const [showGrantReapply, setShowGrantReapply] = useState(false);
  const [showAddResume, setShowAddResume] = useState(false);
  const canGrantReapply = hasReapplyGrantRole(user?.roles);

  const candidateQuery = useQuery({
    queryKey: ["recruiter", "candidates", candidateId],
    queryFn: () => getCandidate(candidateId, token),
    enabled: accessToken !== null,
  });

  const applicationsQuery = useQuery({
    queryKey: ["recruiter", "applications", "candidate", candidateId],
    queryFn: () => listApplicationsForCandidate(candidateId, token),
    enabled: accessToken !== null,
  });

  const reapplyQuery = useQuery({
    queryKey: ["recruiter", "candidates", candidateId, "reapply-status"],
    queryFn: () => getReapplyStatus(candidateId, token),
    enabled: accessToken !== null,
  });

  async function handleDownload(applicationId: string, filename: string) {
    setDownloadError(null);
    setDownloadingId(applicationId);
    try {
      const { blob, filename: serverFilename } = await downloadResume(applicationId, token);
      triggerBlobDownload(blob, serverFilename ?? filename);
    } catch (err) {
      setDownloadError(err instanceof ApiError ? err.message : "Could not download the resume.");
    } finally {
      setDownloadingId(null);
    }
  }

  if (candidateQuery.isPending) {
    return (
      <div className="stack-lg" role="status" aria-label="Loading candidate">
        <SkeletonCard lines={2} />
        <SkeletonCard lines={5} />
      </div>
    );
  }

  if (candidateQuery.isError || !candidateQuery.data) {
    return (
      <Alert>
        {candidateQuery.error instanceof ApiError
          ? candidateQuery.error.message
          : "Candidate not found."}
      </Alert>
    );
  }

  const candidate = candidateQuery.data;
  // Distinct roles this candidate has applied for — separate from
  // `current_title` (their professional title today), one row per unique
  // job, each linking to that application (SIGVITAS platform overhaul § 14).
  const appliedRoles: [string, string][] = applicationsQuery.data
    ? [...new Map(applicationsQuery.data.map((a) => [a.job_title, a.id])).entries()]
    : [];

  // The self-apply cooldown in the recruiter's words. HR adding a role for
  // this candidate is never restricted — only the candidate's own
  // applications are, which is what this row reports on.
  const reapply = reapplyQuery.data;
  let reapplyNote: ReactNode = "—";
  if (reapply) {
    if (reapply.open_grant) {
      reapplyNote = (
        <>
          <span className="badge badge-active">Reapply allowed</span>
          <br />
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            Granted by {reapply.open_grant.granted_by_name ?? "a team member"} on{" "}
            {formatDay(reapply.open_grant.created_at)}
          </span>
        </>
      );
    } else if (reapply.can_self_apply_now) {
      reapplyNote = reapply.last_self_applied_at
        ? `Can apply now (last applied ${formatDay(reapply.last_self_applied_at)})`
        : "Can apply now";
    } else if (reapply.eligible_from) {
      reapplyNote = `Can apply again from ${formatDay(reapply.eligible_from)} (${reapply.cooldown_months}-month window)`;
    }
  }

  return (
    <div className="stack-lg">
      <BackLink to="/recruiter/candidates">Back to candidates</BackLink>

      <div className="page-header profile-header">
        <div className="profile-identity">
          <Avatar name={candidate.full_name} large />
          <div>
            <h1>{candidate.full_name}</h1>
            <p className="muted">{candidate.email}</p>
            <ul className="profile-facts" aria-label="Candidate summary">
              {candidate.current_title && candidate.current_company && (
                <li className="chip">
                  <Icon name="jobs" size={13} />
                  {candidate.current_title} at {candidate.current_company}
                </li>
              )}
              {candidate.location && (
                <li className="chip">
                  <Icon name="pin" size={13} />
                  {candidate.location}
                </li>
              )}
              {candidate.years_experience !== null && (
                <li className="chip">
                  <Icon name="clock" size={13} />
                  {candidate.years_experience} yrs experience
                </li>
              )}
              {candidate.immediate_joiner ? (
                <li className="chip">Immediate joiner</li>
              ) : (
                candidate.notice_period_days !== null && (
                  <li className="chip">{candidate.notice_period_days}-day notice</li>
                )
              )}
              {candidate.linkedin_url && (
                <li>
                  <a className="chip" href={candidate.linkedin_url} target="_blank" rel="noopener noreferrer">
                    LinkedIn
                  </a>
                </li>
              )}
              {candidate.github_url && (
                <li>
                  <a className="chip" href={candidate.github_url} target="_blank" rel="noopener noreferrer">
                    GitHub
                  </a>
                </li>
              )}
            </ul>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.6rem" }}>
          <span className={`badge ${candidate.is_active ? "badge-active" : "badge-inactive"}`}>
            {candidate.is_active ? "Active" : "Inactive"}
          </span>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}
            onClick={() => navigate(`/recruiter/ai?candidateId=${candidate.id}`)}
          >
            <Icon name="sparkles" size={14} />
            Analyze with AI
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}
            onClick={() => setShowAddResume(true)}
          >
            <Icon name="plus" size={14} />
            Add resume &amp; role
          </button>
          {/* Only offered while there is a restriction to lift — granting is
              refused server-side otherwise, so a button that always showed
              would mostly be a dead end. */}
          {canGrantReapply && reapplyQuery.data && !reapplyQuery.data.can_self_apply_now && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowGrantReapply(true)}>
              Grant Reapply Permission
            </button>
          )}
        </div>
      </div>

      <div className="detail-grid">
        <section className="card">
          <h2>Applications</h2>
          {applicationsQuery.isPending && <p role="status">Loading applications…</p>}
          {applicationsQuery.isError && <Alert>Could not load applications.</Alert>}
          {downloadError && <Alert>{downloadError}</Alert>}

          {applicationsQuery.isSuccess &&
            (applicationsQuery.data.length === 0 ? (
              <EmptyState icon="applications" title="No applications yet" compact>
                This candidate hasn't applied to any roles yet.
              </EmptyState>
            ) : (
              <div className="stack-lg" style={{ gap: "0.75rem" }}>
                {applicationsQuery.data.map((application) => (
                  <div key={application.id} className="detail-row" style={{ alignItems: "center" }}>
                    <div>
                      <div>{application.job_title}</div>
                      <div className="muted" style={{ fontSize: "0.8rem" }}>
                        Applied {new Date(application.applied_at).toLocaleDateString()}
                      </div>
                      <PipelineTrack status={application.status} compact />
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                      <span className={toneBadgeClass(statusTone(application.status))}>{application.status}</span>
                      {application.resume_id && (
                        <>
                          <button
                            type="button"
                            className="link-button"
                            onClick={() =>
                              setPreviewing({
                                applicationId: application.id,
                                filename: application.resume_filename ?? "resume",
                              })
                            }
                          >
                            Preview Resume
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            disabled={downloadingId === application.id}
                            onClick={() =>
                              void handleDownload(
                                application.id,
                                application.resume_filename ?? "resume",
                              )
                            }
                          >
                            {downloadingId === application.id ? "Downloading…" : "Download"}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ))}
        </section>

        <section className="card">
          <h2>Profile</h2>
          <Collapsible collapsedHeight={360} label="Candidate profile details">
            <div className="detail-row">
              <span className="detail-row-label">Mobile</span>
              <span>{candidate.phone ?? "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Email verification</span>
              <span>
                {candidate.email_verified_at ? (
                  <span className="badge badge-active">
                    Verified {new Date(candidate.email_verified_at).toLocaleDateString()}
                  </span>
                ) : (
                  <span className="badge badge-inactive">Not verified</span>
                )}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Date of birth</span>
              <span>
                {candidate.date_of_birth
                  ? new Date(`${candidate.date_of_birth}T00:00:00`).toLocaleDateString()
                  : "—"}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Place of birth</span>
              <span>{candidate.place_of_birth ?? "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Languages</span>
              <span>{candidate.languages.length > 0 ? candidate.languages.join(", ") : "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Location</span>
              <span>{candidate.location ?? "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Current title</span>
              <span>{candidate.current_title ?? "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Applied roles</span>
              <span>
                {appliedRoles.length === 0 ? (
                  "—"
                ) : (
                  <span style={{ display: "flex", flexDirection: "column", gap: "0.15rem" }}>
                    {appliedRoles.map(([jobTitle, applicationId]) => (
                      <Link key={applicationId} to={`/recruiter/applications/${applicationId}`}>
                        {jobTitle}
                      </Link>
                    ))}
                  </span>
                )}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Experience</span>
              <span>
                {candidate.years_experience !== null ? `${candidate.years_experience} yrs` : "—"}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Candidate type</span>
              <span>
                {candidate.candidate_type === null
                  ? "—"
                  : candidate.candidate_type === "FRESHER"
                    ? "Fresher"
                    : "Experienced"}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Current company</span>
              <span>{candidate.current_company ?? "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Preferred location</span>
              <span>{candidate.preferred_location ?? "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Notice period</span>
              <span>
                {candidate.immediate_joiner
                  ? "Immediate joiner"
                  : candidate.notice_period_days !== null
                    ? `${candidate.notice_period_days} days`
                    : "—"}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Qualification</span>
              <span>{candidate.qualification ?? "—"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">LinkedIn</span>
              <span>
                {candidate.linkedin_url ? (
                  <a href={candidate.linkedin_url} target="_blank" rel="noopener noreferrer">
                    {candidate.linkedin_url}
                  </a>
                ) : (
                  "—"
                )}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">GitHub</span>
              <span>
                {candidate.github_url ? (
                  <a href={candidate.github_url} target="_blank" rel="noopener noreferrer">
                    {candidate.github_url}
                  </a>
                ) : (
                  "—"
                )}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Source</span>
              <span>{candidate.source}</span>
            </div>
            <div className="detail-row">
              <span className="detail-row-label">Self-apply</span>
              <span style={{ textAlign: "right" }}>{reapplyNote}</span>
            </div>
          </Collapsible>
        </section>
      </div>

      <CandidateJourney candidateId={candidate.id} />

      {showGrantReapply && (
        <GrantReapplyModal candidate={candidate} onClose={() => setShowGrantReapply(false)} />
      )}

      {showAddResume && (
        <AddResumeAndRoleModal
          candidateId={candidate.id}
          candidateName={candidate.full_name}
          onClose={() => setShowAddResume(false)}
        />
      )}

      {previewing && (
        <ResumePreviewModal
          filename={previewing.filename}
          fetchResume={() => downloadResume(previewing.applicationId, token)}
          onClose={() => setPreviewing(null)}
        />
      )}
    </div>
  );
}
