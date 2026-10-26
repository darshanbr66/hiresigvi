import { describe, expect, it } from "vitest";
import {
  DEFAULT_LIST_STATE,
  EMPTY_FILTERS,
  PAGE_SIZE,
  activeFilterChips,
  hasActiveCriteria,
  parseListState,
  serializeListState,
  toApiQuery,
  validateFilters,
  type ApplicationListState,
} from "./applicationFilters";

const parse = (query: string) => parseListState(new URLSearchParams(query));

describe("parseListState / serializeListState", () => {
  it("a fresh page has no state and a clean URL", () => {
    expect(parse("")).toEqual(DEFAULT_LIST_STATE);
    expect(serializeListState(DEFAULT_LIST_STATE).toString()).toBe("");
  });

  it("round-trips every filter through the URL", () => {
    const state: ApplicationListState = {
      filters: {
        q: "jane doe",
        job: "job-1",
        status: "SHORTLISTED",
        candidateType: "EXPERIENCED",
        currentTitle: "Analyst",
        currentCompany: "Acme & Sons",
        location: "Pune",
        preferredLocation: "Remote",
        qualification: "B.Tech",
        minExperience: "2",
        maxExperience: "9",
        maxNotice: "30",
        immediateJoiner: "false",
        appliedFrom: "2026-09-01",
        appliedTo: "2026-09-21",
        source: "REFERRAL",
        campusDrive: "5b0c6d0e-1f2a-4b3c-8d4e-5f6a7b8c9d0e",
        institution: "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
      },
      sort: "name_asc",
      page: 3,
    };

    const url = serializeListState(state).toString();
    expect(parse(url)).toEqual(state);
    // Readable, shareable names — not the internal field names.
    expect(url).toContain("exp_min=2");
    expect(url).toContain("from=2026-09-01");
  });

  it("omits defaults so URLs stay short", () => {
    expect(serializeListState({ ...DEFAULT_LIST_STATE, page: 1, sort: "newest" }).toString()).toBe("");
    expect(serializeListState({ ...DEFAULT_LIST_STATE, page: 2 }).toString()).toBe("page=2");
  });

  it("drops anything this page could not have produced", () => {
    const state = parse(
      "status=BOGUS&type=ROBOT&source=LINKEDIN&immediate=maybe&exp_min=abc&exp_max=-1&notice=99999" +
        "&from=yesterday&to=2026-9-1&sort=password&page=0",
    );
    expect(state).toEqual(DEFAULT_LIST_STATE);
    expect(parse("page=abc").page).toBe(1);
    expect(parse("page=-4").page).toBe(1);
  });

  it("trims whitespace from typed values", () => {
    expect(parse("q=%20%20jane%20&company=%20Acme%20").filters).toMatchObject({
      q: "jane",
      currentCompany: "Acme",
    });
  });
});

describe("toApiQuery", () => {
  it("sends only what is set, plus sort and the page window", () => {
    const query = new URLSearchParams(toApiQuery(DEFAULT_LIST_STATE));
    expect(Object.fromEntries(query)).toEqual({
      sort_by: "applied_at",
      sort_dir: "desc",
      limit: String(PAGE_SIZE),
      offset: "0",
    });
  });

  it("maps every filter to the API's parameter names", () => {
    const state = parse(
      "q=jane&job=j1&status=INTERVIEW&type=FRESHER&title=Dev&company=Acme&location=Pune&preferred=Goa" +
        "&qualification=MCA&exp_min=1&exp_max=4&notice=15&immediate=true&from=2026-09-01&to=2026-09-30" +
        "&source=PORTAL&sort=oldest&page=3",
    );
    expect(Object.fromEntries(new URLSearchParams(toApiQuery(state)))).toEqual({
      q: "jane",
      job_id: "j1",
      status: "INTERVIEW",
      candidate_type: "FRESHER",
      current_title: "Dev",
      current_company: "Acme",
      location: "Pune",
      preferred_location: "Goa",
      qualification: "MCA",
      min_experience: "1",
      max_experience: "4",
      max_notice_period_days: "15",
      immediate_joiner: "true",
      applied_from: "2026-09-01",
      applied_to: "2026-09-30",
      source: "PORTAL",
      sort_by: "applied_at",
      sort_dir: "asc",
      limit: "25",
      offset: String(2 * PAGE_SIZE),
    });
  });

  it("carries scoping parameters the user does not set", () => {
    const query = new URLSearchParams(toApiQuery(DEFAULT_LIST_STATE, { campus_drive_id: "drive-1" }));
    expect(query.get("campus_drive_id")).toBe("drive-1");
  });

  it("encodes text safely", () => {
    const state = parse("company=" + encodeURIComponent("A&B = C%"));
    expect(new URLSearchParams(toApiQuery(state)).get("current_company")).toBe("A&B = C%");
  });
});

describe("validateFilters", () => {
  it("accepts an empty or consistent set", () => {
    expect(validateFilters(EMPTY_FILTERS)).toBeNull();
    expect(validateFilters({ ...EMPTY_FILTERS, minExperience: "2", maxExperience: "2" })).toBeNull();
    expect(validateFilters({ ...EMPTY_FILTERS, appliedFrom: "2026-09-01", appliedTo: "2026-09-01" })).toBeNull();
  });

  it("rejects inverted ranges", () => {
    expect(validateFilters({ ...EMPTY_FILTERS, minExperience: "5", maxExperience: "2" })).toMatch(
      /Minimum experience/,
    );
    expect(validateFilters({ ...EMPTY_FILTERS, appliedFrom: "2026-09-21", appliedTo: "2026-09-01" })).toMatch(
      /Applied from/,
    );
  });

  it("does not treat one open end as an error", () => {
    expect(validateFilters({ ...EMPTY_FILTERS, minExperience: "5" })).toBeNull();
    expect(validateFilters({ ...EMPTY_FILTERS, appliedTo: "2026-09-01" })).toBeNull();
  });
});

describe("activeFilterChips / hasActiveCriteria", () => {
  const jobs = [{ id: "j1", title: "Data Analyst" }];

  it("has no chips and no criteria when nothing is set", () => {
    expect(activeFilterChips(EMPTY_FILTERS, jobs)).toEqual([]);
    expect(hasActiveCriteria(EMPTY_FILTERS)).toBe(false);
  });

  it("describes each filter in words a recruiter would use", () => {
    const filters = parse(
      "job=j1&status=UNDER_REVIEW&type=FRESHER&exp_min=2&exp_max=5&notice=30&immediate=true" +
        "&from=2026-09-01&source=CAMPUS_IMPORT",
    ).filters;

    expect(activeFilterChips(filters, jobs).map((chip) => chip.label)).toEqual([
      "Job: Data Analyst",
      "Status: Under review",
      "Type: Fresher",
      "Experience: 2–5 yrs",
      "Notice: Up to 30 days",
      "Immediate joiner: Yes",
      "From: 01-09-2026",
      "Source: Campus import",
    ]);
  });

  it("words one-sided experience ranges naturally and clears both ends at once", () => {
    expect(activeFilterChips({ ...EMPTY_FILTERS, minExperience: "3" })[0].label).toBe("Experience: 3+ yrs");
    expect(activeFilterChips({ ...EMPTY_FILTERS, maxExperience: "7" })[0].label).toBe(
      "Experience: up to 7 yrs",
    );
    expect(activeFilterChips({ ...EMPTY_FILTERS, minExperience: "1", maxExperience: "2" })[0].keys).toEqual([
      "minExperience",
      "maxExperience",
    ]);
  });

  it("does not show the search text as a chip, but counts it as criteria", () => {
    const filters = { ...EMPTY_FILTERS, q: "jane" };
    expect(activeFilterChips(filters, jobs)).toEqual([]);
    expect(hasActiveCriteria(filters)).toBe(true);
  });

  it("falls back gracefully for a job that is not in the list", () => {
    expect(activeFilterChips({ ...EMPTY_FILTERS, job: "gone" }, jobs)[0].label).toBe("Job: Selected job");
  });

  it("names the campus drive and institution filters, and scopes the API query by them", () => {
    const driveId = "5b0c6d0e-1f2a-4b3c-8d4e-5f6a7b8c9d0e";
    const institutionId = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
    const filters = { ...EMPTY_FILTERS, campusDrive: driveId, institution: institutionId };
    const labels = activeFilterChips(filters, [], {
      campusDrives: [{ id: driveId, name: "IIT Drive 2026" }],
      institutions: [{ id: institutionId, name: "IIT Bombay" }],
    }).map((chip) => chip.label);
    expect(labels).toEqual(["Campus drive: IIT Drive 2026", "Institution: IIT Bombay"]);

    const query = new URLSearchParams(toApiQuery({ ...DEFAULT_LIST_STATE, filters }));
    expect(query.get("campus_drive_id")).toBe(driveId);
    expect(query.get("institution_id")).toBe(institutionId);
  });

  it("drops a campus drive or institution id that is not a UUID", () => {
    const state = parse("drive=not-a-uuid&institution=1%3B%20drop");
    expect(state.filters.campusDrive).toBe("");
    expect(state.filters.institution).toBe("");
  });
});
