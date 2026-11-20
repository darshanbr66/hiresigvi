import type { CareersView } from "../careers/careersView";

/**
 * The organization whose careers site this deployment's homepage presents.
 * The value is the organization's careers-site slug (`/org/<slug>`), used for
 * the homepage's opportunity links.
 *
 * Set `VITE_CAREERS_ORG_SLUG` at build time to point the homepage at a
 * different organization record.
 */
export const CAREERS_ORG_SLUG: string = import.meta.env.VITE_CAREERS_ORG_SLUG?.trim() || "zodiac-patent";

export const careersPath = `/org/${CAREERS_ORG_SLUG}`;

/** The careers page opened on one view (see careers/careersView.ts).
 * `all` is the All view: the careers page reads any value it doesn't list as
 * a filter as All, so `?view=all` shows every published role and program
 * with the All tab selected. */
export function careersViewPath(view: CareersView | "all"): string {
  return `${careersPath}?view=${view}`;
}

/**
 * Careers-site slugs an organization used before it was renamed. Old links
 * (`/org/<old>` and `/org/<old>/jobs/<id>`, e.g. in emails already sent or
 * bookmarks) redirect to the current slug — see LegacyOrgRedirect in
 * app/routes.tsx. The backend keeps the same aliases for its public API
 * (`ORGANIZATION_SLUG_ALIASES`, app/core/config.py).
 */
export const LEGACY_ORG_SLUGS: Record<string, string> = {
  sigvitas: "zodiac-patent",
};
