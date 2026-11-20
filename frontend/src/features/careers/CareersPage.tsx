import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { EmptyState } from "../../shared/components/EmptyState";
import { Icon } from "../../shared/components/Icon";
import { Skeleton } from "../../shared/components/Skeleton";
import type { PublicJobSummary } from "../../types/careers";
import type { PublicProgramSummary } from "../../types/program";
import { PublicHeader } from "../public/PublicHeader";
import { getPublicOrganization, listOpenJobs, listPublicPrograms } from "./api";
import { CAREERS_TABS, jobsForView, parseCareersView, showsProgramsInView, type CareersView } from "./careersView";
import { jobCardChips } from "./jobFacts";
import { displayNameFromSlug } from "./orgName";

/** Hero copy per view; `all` is the page with no `view`. */
const VIEW_HERO: Record<CareersView | "all", { title: string; text: string; empty: string }> = {
  all: {
    title: "Explore Career Opportunities",
    text: "Discover opportunities to grow your career and make an impact.",
    empty: "No open opportunities right now",
  },
  jobs: {
    title: "Explore Career Opportunities",
    text: "Discover opportunities to grow your career and make an impact.",
    empty: "No open roles right now",
  },
  internships: {
    title: "Internship Opportunities",
    text: "Explore current internships and start building your professional experience.",
    empty: "No internships open right now",
  },
  campus: {
    title: "Campus Hiring",
    text: "Students and graduates can apply directly to any of the opportunities below.",
    empty: "No open opportunities right now",
  },
  programs: {
    title: "Training Programs",
    text: "Training, trainee and scholarship programs currently published.",
    empty: "No programs open right now",
  },
};

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** One open role. Department leads as a small label; the facts follow as
 * pills (location, then type/mode/duration/hours). No pay on the card —
 * compensation is shown on the job's own page only (jobCardChips). */
function JobCard({ slug, job }: { slug: string; job: PublicJobSummary }) {
  const facts = jobCardChips(job);
  return (
    <Link to={`/org/${slug}/jobs/${job.id}`} className="opening-card">
      {job.department && <span className="opening-card-kicker">{job.department}</span>}
      <span className="opening-card-title">{job.title}</span>
      <span className="opening-card-meta">
        {job.location && (
          <span className="opening-pill">
            <Icon name="pin" size={13} />
            {job.location}
          </span>
        )}
        {facts.map((fact) => (
          <span key={fact} className="opening-pill">
            {fact}
          </span>
        ))}
      </span>
      <span className="opening-card-cta">
        View role <Icon name="arrow-right" size={16} />
      </span>
    </Link>
  );
}

/** One published program, in the same card as a role. */
function ProgramCard({ program }: { program: PublicProgramSummary }) {
  return (
    <Link to={`/programs/${program.id}`} className="opening-card">
      <span className="opening-card-kicker">Program</span>
      <span className="opening-card-title">{program.title}</span>
      <span className="opening-card-meta">
        {program.training_duration_options.length > 0 && (
          <span className="opening-pill">
            {program.training_duration_options.map((m) => `${m} months`).join(" / ")} training
          </span>
        )}
        {!program.accepting_applications && <span className="opening-pill">Not accepting applications</span>}
        {program.application_closes_at && program.accepting_applications && (
          <span className="opening-pill">Apply by {new Date(program.application_closes_at).toLocaleDateString()}</span>
        )}
      </span>
      <span className="opening-card-cta">
        View program <Icon name="arrow-right" size={16} />
      </span>
    </Link>
  );
}

function CardSkeletons({ label }: { label: string }) {
  return (
    <div className="opening-grid" role="status" aria-label={label}>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="opening-card opening-card-skeleton" aria-hidden="true">
          <Skeleton height="0.7rem" width="25%" />
          <Skeleton height="1.15rem" width="60%" />
          <Skeleton height="0.85rem" width="80%" />
        </div>
      ))}
    </div>
  );
}

/**
 * An organization's careers site (CLAUDE.md § 1: "public career site:
 * anonymous job browse/search/details, entry point into candidate apply
 * flow"). Header, a compact navy hero, the All / Open Roles tabs lifted over
 * its edge, and the published opportunities as cards — nothing else. See
 * careersView.ts for what each view lists.
 */
export function CareersPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const [searchParams] = useSearchParams();
  const view = parseCareersView(searchParams.get("view"));
  const hero = VIEW_HERO[view ?? "all"];
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

  const visibleJobs = jobsQuery.isSuccess ? jobsForView(jobsQuery.data, view) : jobsForView([], view);
  const showsJobs = visibleJobs !== null;
  const showsPrograms = showsProgramsInView(view);
  const programsOnly = view === "programs";

  // Programs alongside roles are a bonus: if they fail to load, the roles
  // still show. Only the programs-only view reports a programs failure.
  const pending = (showsJobs && jobsQuery.isPending) || (programsOnly && programsQuery.isPending);
  const jobsFailed = showsJobs && jobsQuery.isError;
  const programsFailed = programsOnly && programsQuery.isError;
  const programs = showsPrograms && programsQuery.isSuccess ? programsQuery.data : [];
  const jobs = visibleJobs ?? [];
  const total = jobs.length + programs.length;
  const settled = !pending && !jobsFailed && !programsFailed;

  let count: string | null = null;
  if (settled && total > 0) {
    count =
      programs.length === 0
        ? plural(total, "open position", "open positions")
        : jobs.length === 0
          ? plural(total, "program", "programs")
          : plural(total, "opportunity", "opportunities");
  }

  return (
    <div className="public-page careers-page">
      <PublicHeader title={orgName} />
      <main className="careers">
        <div className="careers-hero">
          <div className="careers-container">
            <p className="careers-eyebrow">{orgName} Careers</p>
            <h1 className="careers-title">{hero.title}</h1>
            <p className="careers-subtitle">{hero.text}</p>
            {count && (
              <span className="careers-count">
                <span className="careers-count-dot" aria-hidden="true" />
                {count}
              </span>
            )}
          </div>
        </div>

        <div className="careers-container careers-body">
          <nav className="careers-views" aria-label="Opportunity type">
            {CAREERS_TABS.map((tab) => (
              <Link
                key={tab.label}
                to={tab.id ? `/org/${slug}?view=${tab.id}` : `/org/${slug}`}
                className="careers-view-link"
                aria-current={view === tab.id ? "page" : undefined}
              >
                {tab.label}
              </Link>
            ))}
          </nav>

          {pending && <CardSkeletons label={programsOnly ? "Loading programs" : "Loading open roles"} />}

          {jobsFailed && (
            <Alert>
              {jobsQuery.error instanceof ApiError ? jobsQuery.error.message : "Could not load open roles."}
            </Alert>
          )}

          {programsFailed && (
            <Alert>
              {programsQuery.error instanceof ApiError ? programsQuery.error.message : "Could not load programs."}
            </Alert>
          )}

          {settled && total === 0 && (
            <EmptyState icon="jobs" title={hero.empty}>
              Check back soon — new openings are posted regularly.
            </EmptyState>
          )}

          {settled && total > 0 && (
            <ul className="opening-grid" aria-label="Open positions">
              {jobs.map((job) => (
                <li key={`job-${job.id}`}>
                  <JobCard slug={slug} job={job} />
                </li>
              ))}
              {programs.map((program) => (
                <li key={`program-${program.id}`}>
                  <ProgramCard program={program} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </main>
    </div>
  );
}
