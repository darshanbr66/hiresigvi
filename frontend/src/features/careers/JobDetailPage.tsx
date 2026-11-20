import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { Icon } from "../../shared/components/Icon";
import { Skeleton, SkeletonCard } from "../../shared/components/Skeleton";
import type { PublicJobDetail } from "../../types/careers";
import { PublicHeader } from "../public/PublicHeader";
import { getOpenJob } from "./api";
import { JobApplicationForm } from "./JobApplicationForm";
import { parseJobDescription, splitLabel } from "./jobDescription";
import { formatCompensation, jobChips, jobDetailFacts } from "./jobFacts";

/** Text in which each line's leading "Label:" (e.g. "Interview Process:",
 * "Bond:") is emphasised; the rest stays regular. Same characters either
 * way — line breaks are kept and shown by `white-space: pre-line`. */
function Labelled({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => {
        const parts = splitLabel(line);
        return (
          <span key={i}>
            {i > 0 && "\n"}
            {parts ? (
              <>
                <strong>{parts.label}</strong>
                {parts.rest}
              </>
            ) : (
              line
            )}
          </span>
        );
      })}
    </>
  );
}

/** The employer's description, structured for reading (jobDescription.ts):
 * same words, same order — bold headings, a bold lead statement, bold
 * leading labels, regular body text and lists. */
function JobDescription({ text }: { text: string }) {
  return (
    <div className="job-description-body">
      {parseJobDescription(text).map((block, i) => {
        if (block.kind === "heading") return <h3 key={i}>{block.text}</h3>;
        if (block.kind === "paragraph") {
          return block.lead ? (
            <p key={i} className="job-lead">
              <strong>{block.text}</strong>
            </p>
          ) : (
            <p key={i}>
              <Labelled text={block.text} />
            </p>
          );
        }
        const List = block.ordered ? "ol" : "ul";
        return (
          <List key={i}>
            {block.items.map((item, j) => (
              <li key={j}>
                <Labelled text={item} />
              </li>
            ))}
          </List>
        );
      })}
    </div>
  );
}

/** The role's facts as pills on the hero band: department, location,
 * type/mode/duration/hours, openings — and pay, only when published, set
 * apart. (The careers list never shows pay; this page is where it lives.) */
function JobFacts({ job }: { job: PublicJobDetail }) {
  const pay = formatCompensation(job);
  const facts = jobChips(job).filter((chip) => chip !== pay);
  return (
    <ul className="job-facts" aria-label="Role summary">
      {job.department && <li className="job-pill">{job.department}</li>}
      {job.location && (
        <li className="job-pill">
          <Icon name="pin" size={13} />
          {job.location}
        </li>
      )}
      {facts.map((fact) => (
        <li key={fact} className="job-pill">
          {fact}
        </li>
      ))}
      {job.openings_count > 0 && (
        <li className="job-pill">
          {job.openings_count} {job.openings_count === 1 ? "opening" : "openings"}
        </li>
      )}
      {pay && (
        <li className="job-pill job-pill-pay">
          <span className="sr-only">Compensation: </span>
          {pay}
        </li>
      )}
    </ul>
  );
}

export function JobDetailPage() {
  const { slug = "", jobId = "" } = useParams<{ slug: string; jobId: string }>();
  const jobQuery = useQuery({
    queryKey: ["public", "job", slug, jobId],
    queryFn: () => getOpenJob(slug, jobId),
  });
  const job = jobQuery.data;
  const facts = job ? jobDetailFacts(job) : [];

  return (
    <div className="public-page careers-page job-page">
      <PublicHeader title={job?.organization.name ?? "Careers"} />
      <main className="careers">
        <div className="careers-hero job-hero-band">
          <div className="careers-container job-container">
            <Link to={`/org/${slug}`} className="job-back">
              <Icon name="arrow-right" size={16} className="back-link-icon" />
              Back to open roles
            </Link>

            {jobQuery.isPending && (
              <div className="job-hero-skeleton" role="status" aria-label="Loading role details">
                <Skeleton height="0.75rem" width="20%" />
                <Skeleton height="2rem" width="55%" style={{ borderRadius: 8 }} />
                <Skeleton height="1.4rem" width="45%" />
              </div>
            )}

            {job && (
              <>
                <p className="careers-eyebrow">{job.organization.name} Careers</p>
                <h1 className="careers-title job-title">{job.title}</h1>
                <JobFacts job={job} />
                <a href="#apply" className="btn btn-primary job-apply-btn">
                  Apply for this role
                  <Icon name="arrow-right" size={16} />
                </a>
              </>
            )}
          </div>
        </div>

        <div className="careers-container job-container job-body">
          {jobQuery.isPending && <SkeletonCard lines={5} />}
          {jobQuery.isError && (
            <Alert>
              {jobQuery.error instanceof ApiError ? jobQuery.error.message : "This role could not be found."}
            </Alert>
          )}

          {job && (
            <>
              {facts.length > 0 && (
                <section className="card job-section" aria-labelledby="role-details-title">
                  <h2 id="role-details-title">Role details</h2>
                  <dl className="job-details-list">
                    {facts.map((fact) => (
                      <div key={fact.label}>
                        <dt>{fact.label}</dt>
                        <dd>{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              )}

              {job.description && (
                <section className="card job-section" aria-labelledby="job-description-title">
                  <h2 id="job-description-title">Job Description</h2>
                  <JobDescription text={job.description} />
                </section>
              )}

              <div id="apply">
                <JobApplicationForm slug={slug} jobId={jobId} contactEmail={job.organization.careers_contact_email} />
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
