import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { Icon } from "../../shared/components/Icon";
import { Skeleton, SkeletonCard } from "../../shared/components/Skeleton";
import { PublicHeader } from "../public/PublicHeader";
import { getOpenJob } from "./api";
import { JobApplicationForm } from "./JobApplicationForm";
import { jobChips, jobDetailFacts } from "./jobFacts";

export function JobDetailPage() {
  const { slug = "", jobId = "" } = useParams<{ slug: string; jobId: string }>();
  const jobQuery = useQuery({
    queryKey: ["public", "job", slug, jobId],
    queryFn: () => getOpenJob(slug, jobId),
  });

  return (
    <div className="public-page">
      <PublicHeader title={jobQuery.data?.organization.name ?? "Careers"} />
      <main className="public-shell">
        <Link to={`/org/${slug}`} className="back-link">
          <Icon name="arrow-right" size={16} className="back-link-icon" />
          Back to open roles
        </Link>

        {jobQuery.isPending && (
          <div className="stack-lg" role="status" aria-label="Loading role details">
            <div aria-hidden="true" className="stack-sm">
              <Skeleton height="2rem" width="60%" style={{ borderRadius: 8 }} />
              <Skeleton height="0.9rem" width="30%" />
            </div>
            <SkeletonCard lines={5} />
          </div>
        )}
        {jobQuery.isError && (
          <Alert>
            {jobQuery.error instanceof ApiError
              ? jobQuery.error.message
              : "This role could not be found."}
          </Alert>
        )}

        {jobQuery.isSuccess && (
          <div className="stack-lg">
            <header className="job-hero">
              <h1>{jobQuery.data.title}</h1>
              <p className="muted">Careers at {jobQuery.data.organization.name}</p>
              <div className="role-card-meta">
                {jobQuery.data.department && <span className="chip">{jobQuery.data.department}</span>}
                {jobQuery.data.location && (
                  <span className="chip">
                    <Icon name="pin" size={13} />
                    {jobQuery.data.location}
                  </span>
                )}
                {jobChips(jobQuery.data).map((chip) => (
                  <span key={chip} className="chip">
                    {chip}
                  </span>
                ))}
                <span className="chip">{jobQuery.data.openings_count} opening(s)</span>
              </div>
              <a href="#apply" className="btn btn-primary job-hero-cta">
                Apply for this role
                <Icon name="arrow-right" size={16} />
              </a>
            </header>

            {jobDetailFacts(jobQuery.data).length > 0 && (
              <section className="card">
                <h2>Role details</h2>
                <dl className="stack-sm" style={{ margin: 0 }}>
                  {jobDetailFacts(jobQuery.data).map((fact) => (
                    <div key={fact.label}>
                      <dt className="muted">{fact.label}</dt>
                      <dd style={{ margin: 0, whiteSpace: "pre-line" }}>{fact.value}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}

            {jobQuery.data.description && (
              <section className="card">
                <h2>Job Description</h2>
                <p className="job-description">{jobQuery.data.description}</p>
              </section>
            )}

            <div id="apply">
              <JobApplicationForm
                slug={slug}
                jobId={jobId}
                contactEmail={jobQuery.data.organization.careers_contact_email}
              />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
