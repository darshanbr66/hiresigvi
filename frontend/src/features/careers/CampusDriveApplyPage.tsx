import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { PublicHeader } from "../public/PublicHeader";
import { getCampusDriveByToken } from "./api";
import { JobApplicationForm } from "./JobApplicationForm";

/** A campus drive's public link. Applying uses the same form and the same
 * candidate identity rules as the careers site (email one-time code, every
 * field mandatory, unique email and mobile, one application per person, AI
 * screening against the drive's job). */
export function CampusDriveApplyPage() {
  const { token = "" } = useParams<{ token: string }>();
  const driveQuery = useQuery({
    queryKey: ["public", "campus-drive", token],
    queryFn: () => getCampusDriveByToken(token),
  });

  return (
    <div className="public-page">
      <PublicHeader title={driveQuery.data?.kind === "drive" ? driveQuery.data.organization_name : "Careers"} />
      <div className="public-shell">
        {driveQuery.isPending && <p role="status">Loading drive details…</p>}
        {driveQuery.isError && (
          <Alert>
            {driveQuery.error instanceof ApiError
              ? driveQuery.error.message
              : "This campus drive link is no longer valid."}
          </Alert>
        )}

        {driveQuery.isSuccess && driveQuery.data.kind === "unavailable" && (
          <section className="card stack-sm" style={{ textAlign: "center" }}>
            <h1>This Recruitment Drive Is No Longer Available</h1>
            <p className="muted">{driveQuery.data.message}</p>
            <Link to="/" className="btn btn-primary" style={{ alignSelf: "center" }}>
              Back to Careers
            </Link>
          </section>
        )}

        {driveQuery.isSuccess && driveQuery.data.kind === "drive" && (
          <>
            <h1>{driveQuery.data.name}</h1>
            <p className="muted">
              {driveQuery.data.organization_name} · {driveQuery.data.college_name}
            </p>
            <div className="job-card-meta" style={{ marginBottom: "1.5rem" }}>
              <span>Hiring for: {driveQuery.data.job_title}</span>
              {driveQuery.data.registration_deadline && (
                <span>
                  Register by {new Date(driveQuery.data.registration_deadline).toLocaleDateString()}
                </span>
              )}
            </div>

            {driveQuery.data.description && (
              <section style={{ marginBottom: "1.5rem" }}>
                <h2>About this drive</h2>
                <p className="job-description">{driveQuery.data.description}</p>
              </section>
            )}

            <section style={{ marginBottom: "2rem" }}>
              <h2>About the role</h2>
              <p className="job-description">{driveQuery.data.job_description}</p>
            </section>

            {driveQuery.data.status !== "ACTIVE" ? (
              <Alert>This campus drive is not currently accepting applications.</Alert>
            ) : (
              <>
                {driveQuery.data.has_assessment && (
                  <p className="field-hint" style={{ marginBottom: "1rem" }}>
                    This drive includes an assessment — once your application is accepted, you'll be
                    directed to take it.
                  </p>
                )}
                <JobApplicationForm
                  campusToken={token}
                  title="Apply for this drive"
                  contactEmail={driveQuery.data.careers_contact_email}
                />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
