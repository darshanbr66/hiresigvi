import { Link } from "react-router-dom";
import { Icon } from "../../shared/components/Icon";
import type { SigviJobCard as JobCard } from "../../types/sigvi";

/** A compact pointer to one public role — enough to decide, then hand off to
 * the real careers page (it does not duplicate it). Every field shown is one
 * the anonymous careers page already shows; nothing is added or inferred. */
export function SigviJobCard({ job, onNavigate }: { job: JobCard; onNavigate: () => void }) {
  const where = [job.location, job.employment_type].filter(Boolean).join(" · ");
  return (
    <article className="sigvi-job" aria-label={`Role: ${job.title}`}>
      <div className="sigvi-job-top">
        <span className="sigvi-job-eyebrow">Open role</span>
        {job.department && <span className="sigvi-job-dept">{job.department}</span>}
      </div>
      <h3 className="sigvi-job-title">{job.title}</h3>
      {where && (
        <p className="sigvi-job-where">
          <Icon name="pin" size={13} />
          {where}
        </p>
      )}
      {job.summary && <p className="sigvi-job-summary">{job.summary}</p>}
      <div className="sigvi-job-actions">
        <Link to={job.view_path} className="sigvi-job-cta" onClick={onNavigate}>
          View role
          <Icon name="arrow-right" size={14} />
        </Link>
        <Link to={job.apply_path} className="sigvi-job-apply" onClick={onNavigate}>
          Apply
        </Link>
      </div>
    </article>
  );
}
