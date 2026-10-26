import { humanizeStatus, statusTone, toneBadgeClass, toneColor } from "../../../shared/lib/statusTone";
import { normalizePipelineStats } from "./presentation";

/** Structured rendering of a pipeline-summary answer's `pipeline_stats`.
 * Accepts `unknown` and validates via normalizePipelineStats, so an
 * unexpected payload degrades to a one-line note, never raw JSON. */
export function PipelineStatsPanel({ stats }: { stats: unknown }) {
  const normalized = normalizePipelineStats(stats);

  if (!normalized) {
    return (
      <p className="ai-pipeline-fallback" role="note">
        Detailed pipeline figures aren't available for this answer.
      </p>
    );
  }

  const { statusCounts, totalApplications, openJobs, topJobs } = normalized;
  const maxStatusCount = Math.max(1, ...(statusCounts ?? []).map((s) => s.count));

  return (
    <div className="ai-pipeline-stats" aria-label="Pipeline summary">
      {(openJobs !== null || totalApplications !== null) && (
        <div className="ai-pipeline-kpis">
          {openJobs !== null && (
            <div className="stat-card">
              <span className="stat-label">Open jobs</span>
              <span className="stat-value">{openJobs}</span>
            </div>
          )}
          {totalApplications !== null && (
            <div className="stat-card">
              <span className="stat-label">Applications</span>
              <span className="stat-value">{totalApplications}</span>
            </div>
          )}
        </div>
      )}

      {statusCounts !== null && (
        <section className="stat-card ai-pipeline-section">
          <h3 className="stat-label">Applications by status</h3>
          {statusCounts.length === 0 ? (
            <p className="ai-pipeline-empty">No applications yet.</p>
          ) : (
            <ul className="ai-pipeline-list">
              {statusCounts.map(({ status, count }) => {
                const tone = statusTone(status);
                return (
                  <li key={status} className="ai-pipeline-row">
                    <span className={toneBadgeClass(tone)}>{humanizeStatus(status)}</span>
                    <span className="ai-pipeline-bar" aria-hidden="true">
                      <span
                        style={{ width: `${(count / maxStatusCount) * 100}%`, background: toneColor(tone) }}
                      />
                    </span>
                    <span className="ai-pipeline-count">{count}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      {topJobs !== null && (
        <section className="stat-card ai-pipeline-section">
          <h3 className="stat-label">Top jobs by applications</h3>
          {topJobs.length === 0 ? (
            <p className="ai-pipeline-empty">No jobs have applications yet.</p>
          ) : (
            <ol className="ai-pipeline-list">
              {topJobs.map(({ title, count }, index) => (
                <li key={`${title}-${index}`} className="ai-pipeline-row ai-pipeline-job">
                  <span className="ai-pipeline-job-title">{title}</span>
                  <span className="ai-pipeline-count">
                    {count} {count === 1 ? "application" : "applications"}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </div>
  );
}
