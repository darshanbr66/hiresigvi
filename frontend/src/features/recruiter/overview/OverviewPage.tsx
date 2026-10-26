import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { AnimatedNumber } from "../../../shared/components/AnimatedNumber";
import { BarList } from "../../../shared/components/BarList";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Icon, type IconName } from "../../../shared/components/Icon";
import { SkeletonCard, SkeletonList, SkeletonStatGrid } from "../../../shared/components/Skeleton";
import {
  humanizeStatus,
  statusTone,
  toneBadgeClass,
  toneColor,
  type Tone,
} from "../../../shared/lib/statusTone";
import { useAuth } from "../../auth/AuthContext";
import { listApplications } from "../applications/api";
import { getReportOverview } from "../reports/api";
import { greetingName } from "./greeting";

const QUICK_LINKS: { to: string; title: string; description: string; icon: IconName }[] = [
  { to: "/recruiter/jobs", title: "Post a job", description: "Create and publish a new requisition.", icon: "jobs" },
  { to: "/recruiter/applications", title: "Review applications", description: "Move candidates through your pipeline.", icon: "applications" },
  { to: "/recruiter/assessments", title: "Assessments", description: "Send skills tests to candidates.", icon: "assessments" },
  { to: "/recruiter/campus-drives", title: "Campus Drives", description: "Run a mass-hiring event for a college.", icon: "campus" },
];

function StatCard({
  label,
  value,
  caption,
  icon,
  tone,
}: {
  label: string;
  value: number;
  caption?: string;
  icon: IconName;
  tone: Tone;
}) {
  return (
    <div className={`stat-card stat-tone-${tone}`}>
      <div className="stat-head">
        <span className="stat-label">{label}</span>
        <span className="stat-icon" aria-hidden="true">
          <Icon name={icon} size={18} />
        </span>
      </div>
      <span className="stat-value">
        <AnimatedNumber value={value} />
      </span>
      {caption && <span className="stat-caption">{caption}</span>}
    </div>
  );
}

function shareOf(part: number, whole: number): string | undefined {
  return whole > 0 ? `${Math.round((part / whole) * 100)}% of candidates` : undefined;
}

/**
 * Recruiter dashboard. Every number here comes from
 * `GET /api/v1/recruiter/reports/overview` (real, tenant-scoped, computed
 * at request time — CLAUDE.md § 2: "Reports != hardcoded numbers"), the
 * same source of truth the Reports page uses, so the two never disagree.
 * Captions under the figures are derived from those same numbers, never
 * invented.
 *
 * Deliberately shows no tenancy/architecture details (organization id,
 * phase numbers, etc.) — this is a normal recruiter's home screen, not a
 * developer view. Platform-level detail belongs on the SUPER_ADMIN-only
 * /admin surface instead.
 */
export function OverviewPage() {
  const { user, accessToken } = useAuth();

  const reportQuery = useQuery({
    queryKey: ["recruiter", "reports", "overview"],
    queryFn: () => getReportOverview(accessToken as string),
    enabled: accessToken !== null,
  });

  const applicationsQuery = useQuery({
    queryKey: ["recruiter", "applications", "all"],
    queryFn: () => listApplications(accessToken as string),
    enabled: accessToken !== null,
  });

  const recentApplications = useMemo(() => {
    if (!applicationsQuery.data) return [];
    return [...applicationsQuery.data]
      .sort((a, b) => new Date(b.applied_at).getTime() - new Date(a.applied_at).getTime())
      .slice(0, 6);
  }, [applicationsQuery.data]);

  const report = reportQuery.data;
  const today = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" }).format(new Date());

  return (
    <div className="stack-lg">
      <section className="command-header">
        <div className="command-header-copy">
          <p className="eyebrow">{today}</p>
          <h1>Welcome back, {greetingName(user)}</h1>
          <p className="muted">Here's what's happening with your hiring pipeline.</p>
        </div>
        <Link to="/recruiter/jobs" className="btn btn-primary command-header-cta">
          <Icon name="plus" size={16} />
          Post a job
        </Link>
      </section>

      {reportQuery.isError && (
        <Alert>
          {reportQuery.error instanceof ApiError ? reportQuery.error.message : "Could not load your overview."}
        </Alert>
      )}

      {reportQuery.isPending && <SkeletonStatGrid count={6} />}

      {report && (
        <section className="stat-grid" aria-label="Key figures">
          <StatCard
            label="Candidates"
            value={report.total_candidates}
            caption={`${report.total_applications} ${report.total_applications === 1 ? "application" : "applications"}`}
            icon="candidates"
            tone="brand"
          />
          <StatCard
            label="Selected Candidates"
            value={report.selected_candidates}
            caption={shareOf(report.selected_candidates, report.total_candidates)}
            icon="check"
            tone="success"
          />
          <StatCard
            label="Rejected Candidates"
            value={report.rejected_candidates}
            caption={shareOf(report.rejected_candidates, report.total_candidates)}
            icon="x"
            tone="danger"
          />
          <StatCard
            label="Hired Candidates"
            value={report.hired_candidates}
            caption={shareOf(report.hired_candidates, report.total_candidates)}
            icon="user"
            tone="accent"
          />
          <StatCard
            label="Assessment invitations"
            value={report.assessments.total_invitations}
            caption={`${report.assessments.submitted} submitted`}
            icon="send"
            tone="info"
          />
          <StatCard
            label="Campus drives"
            value={report.campus_drives.length}
            caption={`${report.campus_drives.reduce((sum, drive) => sum + drive.application_count, 0)} applications`}
            icon="campus"
            tone="neutral"
          />
        </section>
      )}

      <div className="detail-grid">
        <section className="card">
          <h2>Pipeline by stage</h2>
          {reportQuery.isPending && <SkeletonCard lines={4} />}
          {report &&
            (report.applications_by_status.length === 0 ? (
              <EmptyState icon="applications" title="No applications yet" compact>
                Once candidates apply, their stages show up here.
              </EmptyState>
            ) : (
              <BarList
                rows={report.applications_by_status.map((row) => ({
                  id: row.status,
                  label: humanizeStatus(row.status),
                  count: row.count,
                }))}
                total={report.total_applications}
                tone={(status) => toneColor(statusTone(status))}
                showShare
              />
            ))}
        </section>

        <section className="card">
          <h2>Recent activity</h2>
          {applicationsQuery.isPending && <SkeletonList rows={4} />}
          {applicationsQuery.isSuccess &&
            (recentApplications.length === 0 ? (
              <EmptyState icon="activities" title="Nothing yet" compact>
                New applications appear here as they arrive.
              </EmptyState>
            ) : (
              <div className="timeline stagger">
                {recentApplications.map((application, index) => (
                  <div
                    key={application.id}
                    className="timeline-item"
                    style={{ "--i": index } as React.CSSProperties}
                  >
                    <span className="timeline-dot" style={{ borderColor: toneColor(statusTone(application.status)) }} />
                    <div>
                      <Link to={`/recruiter/applications/${application.id}`}>
                        {application.candidate_full_name}
                      </Link>{" "}
                      applied to <strong>{application.job_title}</strong>
                      <div className="muted">
                        {new Date(application.applied_at).toLocaleString()} ·{" "}
                        <span className={toneBadgeClass(statusTone(application.status))}>
                          {humanizeStatus(application.status)}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ))}
        </section>
      </div>

      <section>
        <h2>Quick actions</h2>
        <div className="quick-grid">
          {QUICK_LINKS.map((link) => (
            <Link key={link.to} to={link.to} className="module-card">
              <span className="module-icon" aria-hidden="true">
                <Icon name={link.icon} size={18} />
              </span>
              <span className="module-title">{link.title}</span>
              <span className="module-phase">{link.description}</span>
              <Icon name="arrow-right" size={16} className="module-arrow" />
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
