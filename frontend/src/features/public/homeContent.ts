/**
 * Copy for the public ZPHIREX homepage — a compact opportunity picker for
 * Zodiac Patent candidates (many arrive from a newspaper advertisement and
 * just need to choose a path). Four cards, nothing else: no marketing
 * sections, no long copy. Open Roles opens the careers page's All view
 * (`view=all`: every published role and program); Internships, Campus Hiring
 * and Training Programs open its Open Roles tab (`view=jobs`). Listings come
 * from live data only.
 */

export const ORGANIZATION_NAME = "Zodiac Patent";
export const OFFICIAL_SITE_URL = "https://zodiacpatent.com/";

/** Zodiac Patent's official logo, downloaded unaltered from the official
 * website (zodiacpatent.com/wp-content/uploads/2021/09/Group-322.png). Its
 * wordmark is white, so it is shown on the dark brand header only. */
export const ORGANIZATION_LOGO_SRC = "/brand/zodiac-patent-logo.png";

export type OpportunityGlyphName = "jobs" | "internships" | "campus" | "programs";

/** Which careers-page view a card opens: `jobs` is the Open Roles filter;
 * `all` is the All view (the careers page lists everything for it). */
export type OpportunityDestination = "jobs" | "all";

export interface OpportunityCard {
  id: string;
  destination: OpportunityDestination;
  glyph: OpportunityGlyphName;
  title: string;
  text: string;
  cta: string;
}

export const OPPORTUNITIES: OpportunityCard[] = [
  {
    id: "open-roles",
    destination: "all",
    glyph: "jobs",
    title: "Open Roles",
    text: "Explore current job opportunities.",
    cta: "View Open Roles",
  },
  {
    id: "internships",
    destination: "jobs",
    glyph: "internships",
    title: "Internships",
    text: "Explore internship opportunities.",
    cta: "Explore Internships",
  },
  {
    id: "campus",
    destination: "jobs",
    glyph: "campus",
    title: "Campus Hiring",
    text: "Explore opportunities for students and campus candidates.",
    cta: "Explore Campus Opportunities",
  },
  {
    id: "programs",
    destination: "jobs",
    glyph: "programs",
    title: "Training Programs",
    text: "Discover training and trainee opportunities.",
    cta: "Explore Programs",
  },
];
