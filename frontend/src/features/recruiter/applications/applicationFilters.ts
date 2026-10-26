/**
 * The search / filter / sort / page state of an application list, shared by the
 * Applications page and a Campus Drive's "Candidates in this drive". It lives in
 * the URL (so refresh and back/forward keep it) and is sent to the server as
 * query parameters — nothing here filters data in the browser.
 */
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { humanizeStatus } from "../../../shared/lib/statusTone";
import { APPLICATION_STATUSES, type ApplicationStatus } from "../../../types/recruitment";

/** Rows per page, for both lists. The server caps a page at 100. */
export const PAGE_SIZE = 25;

export type CandidateType = "FRESHER" | "EXPERIENCED";
export const CANDIDATE_TYPES: { value: CandidateType; label: string }[] = [
  { value: "FRESHER", label: "Fresher" },
  { value: "EXPERIENCED", label: "Experienced" },
];

/** The sources this system actually records (not free text). */
export type ApplicationSource = "PORTAL" | "RECRUITER_ADDED" | "CAMPUS_IMPORT" | "REFERRAL" | "OTHER";
export const APPLICATION_SOURCES: { value: ApplicationSource; label: string }[] = [
  { value: "PORTAL", label: "Career site" },
  { value: "RECRUITER_ADDED", label: "Added by recruiter" },
  { value: "CAMPUS_IMPORT", label: "Campus import" },
  { value: "REFERRAL", label: "Referral" },
  { value: "OTHER", label: "Other" },
];

/** "Up to N days" — the server compares against the candidate's notice period. */
export const NOTICE_PERIODS: { value: string; label: string }[] = [
  { value: "0", label: "Immediate (0 days)" },
  { value: "15", label: "Up to 15 days" },
  { value: "30", label: "Up to 30 days" },
  { value: "60", label: "Up to 60 days" },
  { value: "90", label: "Up to 90 days" },
];

export type SortKey = "newest" | "oldest" | "name_asc" | "name_desc" | "status";
export const SORT_OPTIONS: { value: SortKey; label: string; by: string; dir: "asc" | "desc" }[] = [
  { value: "newest", label: "Newest first", by: "applied_at", dir: "desc" },
  { value: "oldest", label: "Oldest first", by: "applied_at", dir: "asc" },
  { value: "name_asc", label: "Name A–Z", by: "candidate_name", dir: "asc" },
  { value: "name_desc", label: "Name Z–A", by: "candidate_name", dir: "desc" },
  { value: "status", label: "Pipeline stage", by: "status", dir: "asc" },
];

/** Every value is a string ("" = not set) so it round-trips through the URL and
 * form inputs unchanged. */
export interface ApplicationFilters {
  q: string;
  job: string;
  status: ApplicationStatus | "";
  candidateType: CandidateType | "";
  currentTitle: string;
  currentCompany: string;
  location: string;
  preferredLocation: string;
  qualification: string;
  minExperience: string;
  maxExperience: string;
  maxNotice: string;
  immediateJoiner: "true" | "false" | "";
  appliedFrom: string;
  appliedTo: string;
  source: ApplicationSource | "";
  campusDrive: string;
  institution: string;
}

export interface ApplicationListState {
  filters: ApplicationFilters;
  sort: SortKey;
  /** 1-based. */
  page: number;
}

export const EMPTY_FILTERS: ApplicationFilters = {
  q: "",
  job: "",
  status: "",
  candidateType: "",
  currentTitle: "",
  currentCompany: "",
  location: "",
  preferredLocation: "",
  qualification: "",
  minExperience: "",
  maxExperience: "",
  maxNotice: "",
  immediateJoiner: "",
  appliedFrom: "",
  appliedTo: "",
  source: "",
  campusDrive: "",
  institution: "",
};

export const DEFAULT_LIST_STATE: ApplicationListState = {
  filters: EMPTY_FILTERS,
  sort: "newest",
  page: 1,
};

/** filter key -> the short name it has in the browser URL (kept human-readable
 * so a shared link makes sense). */
const URL_KEYS: Record<keyof ApplicationFilters, string> = {
  q: "q",
  job: "job",
  status: "status",
  candidateType: "type",
  currentTitle: "title",
  currentCompany: "company",
  location: "location",
  preferredLocation: "preferred",
  qualification: "qualification",
  minExperience: "exp_min",
  maxExperience: "exp_max",
  maxNotice: "notice",
  immediateJoiner: "immediate",
  appliedFrom: "from",
  appliedTo: "to",
  source: "source",
  campusDrive: "drive",
  institution: "institution",
};

/** filter key -> the query parameter the API expects. */
const API_KEYS: Record<keyof ApplicationFilters, string> = {
  q: "q",
  job: "job_id",
  status: "status",
  candidateType: "candidate_type",
  currentTitle: "current_title",
  currentCompany: "current_company",
  location: "location",
  preferredLocation: "preferred_location",
  qualification: "qualification",
  minExperience: "min_experience",
  maxExperience: "max_experience",
  maxNotice: "max_notice_period_days",
  immediateJoiner: "immediate_joiner",
  appliedFrom: "applied_from",
  appliedTo: "applied_to",
  source: "source",
  campusDrive: "campus_drive_id",
  institution: "institution_id",
};

const FILTER_KEYS = Object.keys(EMPTY_FILTERS) as (keyof ApplicationFilters)[];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const DIGITS = /^\d{1,3}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | "" {
  return allowed.includes(value as T) ? (value as T) : "";
}

/** Reads list state out of a URL. Anything that isn't a value this page could
 * have produced (a hand-edited or stale link) is dropped rather than sent to
 * the server. */
export function parseListState(params: URLSearchParams): ApplicationListState {
  const text = (key: keyof ApplicationFilters) => (params.get(URL_KEYS[key]) ?? "").trim();
  const digits = (key: keyof ApplicationFilters) => (DIGITS.test(text(key)) ? text(key) : "");
  const date = (key: keyof ApplicationFilters) => (ISO_DATE.test(text(key)) ? text(key) : "");
  const uuid = (key: keyof ApplicationFilters) => (UUID.test(text(key)) ? text(key) : "");
  const page = Number.parseInt(params.get("page") ?? "1", 10);

  return {
    filters: {
      q: text("q"),
      job: text("job"),
      status: oneOf(params.get(URL_KEYS.status), APPLICATION_STATUSES),
      candidateType: oneOf(
        params.get(URL_KEYS.candidateType),
        CANDIDATE_TYPES.map((t) => t.value),
      ),
      currentTitle: text("currentTitle"),
      currentCompany: text("currentCompany"),
      location: text("location"),
      preferredLocation: text("preferredLocation"),
      qualification: text("qualification"),
      minExperience: digits("minExperience"),
      maxExperience: digits("maxExperience"),
      maxNotice: digits("maxNotice"),
      immediateJoiner: oneOf(params.get(URL_KEYS.immediateJoiner), ["true", "false"] as const),
      appliedFrom: date("appliedFrom"),
      appliedTo: date("appliedTo"),
      source: oneOf(
        params.get(URL_KEYS.source),
        APPLICATION_SOURCES.map((s) => s.value),
      ),
      campusDrive: uuid("campusDrive"),
      institution: uuid("institution"),
    },
    sort: oneOf(
      params.get("sort"),
      SORT_OPTIONS.map((o) => o.value),
    ) || "newest",
    page: Number.isFinite(page) && page >= 1 ? page : 1,
  };
}

/** The inverse of `parseListState`. Defaults are omitted, so a fresh page has a
 * clean URL. */
export function serializeListState(state: ApplicationListState): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = state.filters[key].trim();
    if (value) params.set(URL_KEYS[key], value);
  }
  if (state.sort !== "newest") params.set("sort", state.sort);
  if (state.page > 1) params.set("page", String(state.page));
  return params;
}

/** The API query string for a page of results. `extra` carries parameters the
 * list is scoped by but the user doesn't set (e.g. `campus_drive_id`). */
export function toApiQuery(state: ApplicationListState, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  for (const key of FILTER_KEYS) {
    const value = state.filters[key].trim();
    if (value) params.set(API_KEYS[key], value);
  }
  const sort = SORT_OPTIONS.find((option) => option.value === state.sort) ?? SORT_OPTIONS[0];
  params.set("sort_by", sort.by);
  params.set("sort_dir", sort.dir);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String((state.page - 1) * PAGE_SIZE));
  return params.toString();
}

/** A message when the filters can't be sent as they are (the server enforces
 * the same rules), else null. */
export function validateFilters(filters: ApplicationFilters): string | null {
  const min = filters.minExperience === "" ? null : Number(filters.minExperience);
  const max = filters.maxExperience === "" ? null : Number(filters.maxExperience);
  if (min !== null && max !== null && min > max) {
    return "Minimum experience can't be greater than maximum experience.";
  }
  if (filters.appliedFrom && filters.appliedTo && filters.appliedFrom > filters.appliedTo) {
    return "“Applied from” can't be after “Applied to”.";
  }
  return null;
}

export interface FilterChip {
  /** Filter keys this chip clears. */
  keys: (keyof ApplicationFilters)[];
  label: string;
}

function formatDate(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${day}-${month}-${year}`;
}

/** What is currently narrowing the list, one chip per setting. The search text
 * is not a chip: it is already visible in the search box. */
export function activeFilterChips(
  filters: ApplicationFilters,
  jobs: { id: string; title: string }[] = [],
  lookups: {
    campusDrives?: { id: string; name: string }[];
    institutions?: { id: string; name: string }[];
  } = {},
): FilterChip[] {
  const chips: FilterChip[] = [];
  if (filters.job) {
    const title = jobs.find((job) => job.id === filters.job)?.title ?? "Selected job";
    chips.push({ keys: ["job"], label: `Job: ${title}` });
  }
  if (filters.status) chips.push({ keys: ["status"], label: `Status: ${humanizeStatus(filters.status)}` });
  if (filters.candidateType) {
    const label = CANDIDATE_TYPES.find((t) => t.value === filters.candidateType)?.label;
    chips.push({ keys: ["candidateType"], label: `Type: ${label}` });
  }
  if (filters.currentTitle) chips.push({ keys: ["currentTitle"], label: `Title: ${filters.currentTitle}` });
  if (filters.currentCompany) {
    chips.push({ keys: ["currentCompany"], label: `Company: ${filters.currentCompany}` });
  }
  if (filters.location) chips.push({ keys: ["location"], label: `Location: ${filters.location}` });
  if (filters.preferredLocation) {
    chips.push({ keys: ["preferredLocation"], label: `Preferred location: ${filters.preferredLocation}` });
  }
  if (filters.qualification) {
    chips.push({ keys: ["qualification"], label: `Qualification: ${filters.qualification}` });
  }
  if (filters.minExperience || filters.maxExperience) {
    const range =
      filters.minExperience && filters.maxExperience
        ? `${filters.minExperience}–${filters.maxExperience} yrs`
        : filters.minExperience
          ? `${filters.minExperience}+ yrs`
          : `up to ${filters.maxExperience} yrs`;
    chips.push({ keys: ["minExperience", "maxExperience"], label: `Experience: ${range}` });
  }
  if (filters.maxNotice) {
    const label = NOTICE_PERIODS.find((p) => p.value === filters.maxNotice)?.label ?? `${filters.maxNotice} days`;
    chips.push({ keys: ["maxNotice"], label: `Notice: ${label}` });
  }
  if (filters.immediateJoiner) {
    chips.push({
      keys: ["immediateJoiner"],
      label: `Immediate joiner: ${filters.immediateJoiner === "true" ? "Yes" : "No"}`,
    });
  }
  if (filters.appliedFrom) chips.push({ keys: ["appliedFrom"], label: `From: ${formatDate(filters.appliedFrom)}` });
  if (filters.appliedTo) chips.push({ keys: ["appliedTo"], label: `To: ${formatDate(filters.appliedTo)}` });
  if (filters.source) {
    const label = APPLICATION_SOURCES.find((s) => s.value === filters.source)?.label;
    chips.push({ keys: ["source"], label: `Source: ${label}` });
  }
  if (filters.campusDrive) {
    const name = lookups.campusDrives?.find((d) => d.id === filters.campusDrive)?.name ?? "Selected drive";
    chips.push({ keys: ["campusDrive"], label: `Campus drive: ${name}` });
  }
  if (filters.institution) {
    const name = lookups.institutions?.find((i) => i.id === filters.institution)?.name ?? "Selected institution";
    chips.push({ keys: ["institution"], label: `Institution: ${name}` });
  }
  return chips;
}

/** Search text or any filter is set (sort and page don't count). */
export function hasActiveCriteria(filters: ApplicationFilters): boolean {
  return FILTER_KEYS.some((key) => filters[key].trim() !== "");
}

/** The list state, kept in the URL query string. */
export function useApplicationListState(): readonly [
  ApplicationListState,
  (next: ApplicationListState) => void,
] {
  const [searchParams, setSearchParams] = useSearchParams();
  const state = useMemo(() => parseListState(searchParams), [searchParams]);
  const setState = useCallback(
    (next: ApplicationListState) => setSearchParams(serializeListState(next)),
    [setSearchParams],
  );
  return [state, setState] as const;
}
