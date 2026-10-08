import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { BarList } from "../../../shared/components/BarList";
import { useAuth } from "../../auth/AuthContext";
import { getReportOverview } from "./api";

/**
 * Every number here comes from `GET /api/v1/recruiter/reports/overview`,
 * which computes it from real tenant-scoped rows at request time
 * (CLAUDE.md § 2: "Reports != hardcoded numbers") — nothing on this page
 * is a placeholder.
 */
export function ReportsPage() {
  const { accessToken } = useAuth();
  const reportQuery = useQuery({
    queryKey: ["recruiter", "reports", "overview"],
    queryFn: () => getReportOverview(accessToken as string),
    enabled: accessToken !== null,
  });

  return (
    <div className="stack-lg">
      <section>
        <h1>Reports</h1>
        <p className="muted">A live snapshot of your hiring pipeline.</p>
      </section>

      {reportQuery.isPending && <p role="status">Loading report…</p>}
      {reportQuery.isError && (
        <Alert>
          {reportQuery.error instanceof ApiError
            ? reportQuery.error.message
            : "Could not load the report."}
        </Alert>
      )}

      {reportQuery.isSuccess && (
        <>
          <section className="card-grid">
            <div className="stat-card">
              <span className="stat-label">Total jobs</span>
              <span className="stat-value">{reportQuery.data.total_jobs}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Open jobs</span>
              <span className="stat-value">{reportQuery.data.open_jobs}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Candidates</span>
              <span className="stat-value">{reportQuery.data.total_candidates}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Applications</span>
              <span className="stat-value">{reportQuery.data.total_applications}</span>
            </div>
          </section>

          <div className="detail-grid">
            <section className="card">
              <h2>Applications by stage</h2>
              {reportQuery.data.applications_by_status.length === 0 ? (
                <p className="muted">No applications yet.</p>
              ) : (
                <BarList
                  rows={reportQuery.data.applications_by_status.map((row) => ({
                    id: row.status,
                    label: row.status,
                    count: row.count,
                  }))}
                  total={reportQuery.data.total_applications}
                />
              )}
            </section>

            <section className="card">
              <h2>Applications by job</h2>
              {reportQuery.data.applications_by_job.length === 0 ? (
                <p className="muted">No applications yet.</p>
              ) : (
                <BarList
                  rows={reportQuery.data.applications_by_job.map((row) => ({
                    id: row.job_id,
                    label: row.job_title,
                    count: row.count,
                  }))}
                  total={reportQuery.data.total_applications}
                />
              )}
            </section>
          </div>

          <div className="detail-grid">
            <section className="card">
              <h2>AI Screening</h2>
              {reportQuery.data.screening.total_runs === 0 ? (
                <p className="muted">No screening runs yet.</p>
              ) : (
                <div className="stack-lg" style={{ gap: "0.5rem" }}>
                  <div className="detail-row">
                    <span className="detail-row-label">Total runs</span>
                    <span>{reportQuery.data.screening.total_runs}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-row-label">Completed</span>
                    <span>{reportQuery.data.screening.completed}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-row-label">Failed</span>
                    <span>{reportQuery.data.screening.failed}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-row-label">Average score</span>
                    <span>{reportQuery.data.screening.average_score}</span>
                  </div>
                </div>
              )}
            </section>

            <section className="card">
              <h2>Assessments</h2>
              {reportQuery.data.assessments.total_invitations === 0 ? (
                <p className="muted">No assessment invitations sent yet.</p>
              ) : (
                <div className="stack-lg" style={{ gap: "0.5rem" }}>
                  <div className="detail-row">
                    <span className="detail-row-label">Invitations sent</span>
                    <span>{reportQuery.data.assessments.total_invitations}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-row-label">Submitted</span>
                    <span>{reportQuery.data.assessments.submitted}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-row-label">Passed</span>
                    <span>{reportQuery.data.assessments.passed}</span>
                  </div>
                </div>
              )}
            </section>
          </div>

          {reportQuery.data.campus_drives.length > 0 && (
            <section className="card">
              <h2>Campus drives</h2>
              <BarList
                rows={reportQuery.data.campus_drives.map((row) => ({
                  id: row.drive_id,
                  label: row.drive_name,
                  count: row.application_count,
                }))}
                total={reportQuery.data.total_applications}
              />
            </section>
          )}
        </>
      )}
    </div>
  );
}
