import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { EmptyState } from "../../shared/components/EmptyState";
import { Icon, type IconName } from "../../shared/components/Icon";
import { NetworkBackdrop } from "../../shared/components/NetworkBackdrop";
import { Reveal } from "../../shared/components/Reveal";
import { Skeleton } from "../../shared/components/Skeleton";
import { PublicHeader } from "../public/PublicHeader";
import { getPublicOrganization, listOpenJobs, listPublicPrograms } from "./api";
import { jobChips } from "./jobFacts";
import { displayNameFromSlug } from "./orgName";

/** How applications are handled — the recruitment content that used to sit
 * on the company homepage (first HR meeting: recruitment lives on Careers). */
const HOW_WE_REVIEW: { icon: IconName; title: string; text: string }[] = [
  {
    icon: "email",
    title: "Verified, one application",
    text: "You confirm your email with a one-time code and apply once. Our team considers your profile for other suitable roles too.",
  },
  {
    icon: "sparkles",
    title: "AI assists, people decide",
    text: "AI helps our recruiters review each resume against the role's requirements. A person always makes the hiring decision.",
  },
  {
    icon: "lock",
    title: "Your data stays with your application",
    text: "Your profile and resume are used only for recruitment, and every review stays traceable to your application.",
  },
  {
    icon: "eye",
    title: "Monitoring is disclosed upfront",
    text: "If a role includes an assessment with monitoring, you're told exactly what is observed before you begin.",
  },
];

const CAMPUS: { icon: IconName; title: string; text: string }[] = [
  {
    icon: "campus",
    title: "Apply with a drive link",
    text: "If your college is running a campus drive with us, you'll get a direct application link from your placement office.",
  },
  {
    icon: "graph",
    title: "Same review process",
    text: "Campus applications go through the same review and assessment process as any other application.",
  },
  {
    icon: "email",
    title: "Clear updates",
    text: "You'll hear from us by email — and if a drive has closed, the link tells you so.",
  },
];

/**
 * An organization's careers site (CLAUDE.md § 1: "public career site:
 * anonymous job browse/search/details, entry point into candidate apply
 * flow") — reached deliberately from the company homepage's Careers link.
 */
export function CareersPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const jobsQuery = useQuery({
    queryKey: ["public", "jobs", slug],
    queryFn: () => listOpenJobs(slug),
  });
  const organizationQuery = useQuery({
    queryKey: ["public", "organization", slug],
    queryFn: () => getPublicOrganization(slug),
  });
  const programsQuery = useQuery({
    queryKey: ["public", "programs", slug],
    queryFn: () => listPublicPrograms(slug),
  });
  const orgName = organizationQuery.data?.name ?? displayNameFromSlug(slug);
  const contactEmail = organizationQuery.data?.careers_contact_email ?? null;

  return (
    <div className="public-page">
      <PublicHeader title={orgName} />
      <main className="public-shell">
        <header className="public-hero">
          <NetworkBackdrop seed={9} count={28} />
          <p className="section-eyebrow">{orgName} Careers</p>
          <h1>Open roles</h1>
          <p className="muted">
            Browse current openings, read the full job description, and apply — no account required.
          </p>
          {jobsQuery.isSuccess && jobsQuery.data.length > 0 && (
            <span className="chip public-hero-count">
              <span className="live-dot" aria-hidden="true" />
              {jobsQuery.data.length} open {jobsQuery.data.length === 1 ? "role" : "roles"}
            </span>
          )}
        </header>

        {jobsQuery.isPending && (
          <div className="role-list" role="status" aria-label="Loading open roles">
            {[0, 1, 2].map((i) => (
              <div key={i} className="role-card role-card-skeleton" aria-hidden="true">
                <Skeleton height="1.1rem" width="55%" />
                <Skeleton height="0.8rem" width="35%" />
              </div>
            ))}
          </div>
        )}

        {jobsQuery.isError && (
          <Alert>
            {jobsQuery.error instanceof ApiError
              ? jobsQuery.error.message
              : "Could not load open roles."}
          </Alert>
        )}

        {jobsQuery.isSuccess && jobsQuery.data.length === 0 && (
          <EmptyState icon="jobs" title="No open roles right now">
            Check back soon — new openings are posted regularly.
          </EmptyState>
        )}

        {jobsQuery.isSuccess && jobsQuery.data.length > 0 && (
          <div className="role-list">
            {jobsQuery.data.map((job, index) => (
              <Reveal key={job.id} delay={Math.min(index, 6) * 50}>
                <Link to={`/org/${slug}/jobs/${job.id}`} className="role-card">
                  <span className="role-card-title">{job.title}</span>
                  <span className="role-card-meta">
                    {job.department && <span className="chip">{job.department}</span>}
                    {job.location && (
                      <span className="chip">
                        <Icon name="pin" size={13} />
                        {job.location}
                      </span>
                    )}
                    {jobChips(job).map((chip) => (
                      <span key={chip} className="chip">
                        {chip}
                      </span>
                    ))}
                  </span>
                  <span className="role-card-cta">
                    View role <Icon name="arrow-right" size={16} />
                  </span>
                </Link>
              </Reveal>
            ))}
          </div>
        )}

        {programsQuery.isSuccess && programsQuery.data.length > 0 && (
          <section id="programs" className="careers-section" aria-labelledby="programs-title">
            <p className="section-eyebrow">Programs</p>
            <h2 id="programs-title">Scholarship &amp; training programs</h2>
            <div className="role-list">
              {programsQuery.data.map((program) => (
                <Link key={program.id} to={`/programs/${program.id}`} className="role-card">
                  <span className="role-card-title">{program.title}</span>
                  <span className="role-card-meta">
                    {program.training_duration_options.length > 0 && (
                      <span className="chip">
                        {program.training_duration_options.map((m) => `${m} months`).join(" / ")} training
                      </span>
                    )}
                    {!program.accepting_applications && <span className="chip">Not accepting applications</span>}
                    {program.application_closes_at && program.accepting_applications && (
                      <span className="chip">
                        Apply by {new Date(program.application_closes_at).toLocaleDateString()}
                      </span>
                    )}
                  </span>
                  <span className="role-card-cta">
                    View program <Icon name="arrow-right" size={16} />
                  </span>
                </Link>
              ))}
            </div>
          </section>
        )}

        <section id="how-we-review" className="careers-section" aria-labelledby="how-we-review-title">
          <p className="section-eyebrow">How we hire</p>
          <h2 id="how-we-review-title">How applications are reviewed</h2>
          <div className="principle-grid">
            {HOW_WE_REVIEW.map((item) => (
              <div key={item.title} className="principle-card">
                <span className="principle-icon">
                  <Icon name={item.icon} size={20} />
                </span>
                <h3>{item.title}</h3>
                <p>{item.text}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="campus" className="careers-section" aria-labelledby="campus-title">
          <p className="section-eyebrow">Campus</p>
          <h2 id="campus-title">Campus hiring</h2>
          <div className="principle-grid principle-grid-3">
            {CAMPUS.map((item) => (
              <div key={item.title} className="principle-card">
                <span className="principle-icon">
                  <Icon name={item.icon} size={20} />
                </span>
                <h3>{item.title}</h3>
                <p>{item.text}</p>
              </div>
            ))}
          </div>
        </section>

        {contactEmail && (
          <p className="muted careers-contact">
            Questions about a role or your application? Contact our recruitment team at{" "}
            <a href={`mailto:${contactEmail}`}>{contactEmail}</a>.
          </p>
        )}
      </main>
    </div>
  );
}
