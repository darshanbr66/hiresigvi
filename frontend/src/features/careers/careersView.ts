import type { PublicJobSummary } from "../../types/careers";

/**
 * Views of the careers page (`/org/<slug>?view=<id>`). Every view is a filter
 * over the same public jobs/programs data — nothing is listed that the API
 * didn't return.
 *
 * The page's tabs are only **All** (no `view`: every published role and
 * program) and **Open Roles** (`jobs`). The other views have no tab; they
 * remain because the homepage's opportunity cards link to them:
 *
 * - `jobs`: open roles that aren't internships (full-time, part-time, untyped)
 * - `internships`: open roles of type Internship
 * - `programs`: published training/scholarship programs
 * - `campus`: everything All shows. Campus drives have no public listing
 *   (each is applied to through its own private link), and students and
 *   graduates can apply to any published role or program directly.
 *
 * An unknown `view` is treated as All.
 */
export type CareersView = "jobs" | "internships" | "campus" | "programs";

const VIEWS: readonly CareersView[] = ["jobs", "internships", "campus", "programs"];

/** The tabs shown on the page. `null` is All. */
export const CAREERS_TABS: { id: CareersView | null; label: string }[] = [
  { id: null, label: "All" },
  { id: "jobs", label: "Open Roles" },
];

export function parseCareersView(value: string | null): CareersView | null {
  return VIEWS.includes(value as CareersView) ? (value as CareersView) : null;
}

/** Which open roles a view lists; `null` means the view lists no roles. */
export function jobsForView(jobs: PublicJobSummary[], view: CareersView | null): PublicJobSummary[] | null {
  switch (view) {
    case null:
    case "campus":
      return jobs;
    case "jobs":
      return jobs.filter((job) => job.employment_type !== "INTERNSHIP");
    case "internships":
      return jobs.filter((job) => job.employment_type === "INTERNSHIP");
    case "programs":
      return null;
  }
}

/** Whether a view lists published programs alongside (or instead of) roles. */
export function showsProgramsInView(view: CareersView | null): boolean {
  return view === null || view === "campus" || view === "programs";
}
