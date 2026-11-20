import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PublicJobSummary } from "../../types/careers";
import type { PublicProgramSummary } from "../../types/program";
import { ThemeProvider } from "../theme/ThemeContext";
import * as careersApi from "./api";
import { CareersPage } from "./CareersPage";

function job(overrides: Partial<PublicJobSummary>): PublicJobSummary {
  return {
    id: "job-1",
    title: "Patent Drafting Associate",
    department: null,
    location: null,
    employment_type: "FULL_TIME",
    openings_count: 1,
    created_at: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

const JOBS = [
  job({ id: "ft", title: "Patent Analyst", employment_type: "FULL_TIME" }),
  job({ id: "pt", title: "Illustrator", employment_type: "PART_TIME" }),
  job({ id: "in", title: "Patent Research Intern", employment_type: "INTERNSHIP" }),
];

const PROGRAM: PublicProgramSummary = {
  id: "program-1",
  title: "Patent Analyst Training Program",
  training_duration_options: [],
  eligible_education_levels: [],
  application_closes_at: null,
  accepting_applications: true,
};

function renderAt(url: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/org/:slug" element={<CareersPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(careersApi, "getPublicOrganization").mockResolvedValue({
    name: "Zodiac Patent",
    slug: "zodiac-patent",
    careers_contact_email: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockData(jobs: PublicJobSummary[], programs: PublicProgramSummary[]) {
  vi.spyOn(careersApi, "listOpenJobs").mockResolvedValue(jobs);
  vi.spyOn(careersApi, "listPublicPrograms").mockResolvedValue(programs);
}

describe("CareersPage views", () => {
  it("shows only the All and Open Roles tabs", async () => {
    mockData(JOBS, [PROGRAM]);
    renderAt("/org/zodiac-patent");

    const tabs = within(screen.getByRole("navigation", { name: "Opportunity type" })).getAllByRole("link");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["All", "Open Roles"]);
    expect(tabs[0]).toHaveAttribute("href", "/org/zodiac-patent");
    expect(tabs[1]).toHaveAttribute("href", "/org/zodiac-patent?view=jobs");
  });

  it("All lists every published role — internships included — and every program, as cards", async () => {
    mockData(JOBS, [PROGRAM]);
    renderAt("/org/zodiac-patent");

    expect(await screen.findByText("Patent Analyst")).toBeInTheDocument();
    expect(screen.getByText("Illustrator")).toBeInTheDocument();
    expect(screen.getByText("Patent Research Intern")).toBeInTheDocument();
    const program = await screen.findByRole("link", { name: new RegExp(PROGRAM.title) });
    expect(program).toHaveAttribute("href", "/programs/program-1");
    const cards = within(screen.getByRole("list", { name: "Open positions" })).getAllByRole("link");
    expect(cards).toHaveLength(4);
    expect(screen.getByText("4 opportunities")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Explore Career Opportunities");
    expect(screen.getByRole("link", { name: "All" })).toHaveAttribute("aria-current", "page");
  });

  it("Open Roles lists job openings only — no internships, no programs", async () => {
    mockData(JOBS, [PROGRAM]);
    renderAt("/org/zodiac-patent?view=jobs");

    expect(await screen.findByText("Patent Analyst")).toBeInTheDocument();
    expect(screen.getByText("Illustrator")).toBeInTheDocument();
    expect(screen.queryByText("Patent Research Intern")).toBeNull();
    expect(screen.queryByText(PROGRAM.title)).toBeNull();
    expect(screen.getByRole("link", { name: "Open Roles" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("2 open positions")).toBeInTheDocument();
  });

  it("has no campus explanation or contact section, even with a published contact", async () => {
    vi.spyOn(careersApi, "getPublicOrganization").mockResolvedValue({
      name: "Zodiac Patent",
      slug: "zodiac-patent",
      careers_contact_email: "hr@zodiacpatent.com",
    });
    mockData(JOBS, []);
    renderAt("/org/zodiac-patent");

    expect(await screen.findByText("Patent Analyst")).toBeInTheDocument();
    expect(screen.queryByText(/hr@zodiacpatent\.com/)).toBeNull();
    expect(screen.queryByText(/Apply with a drive link|Same review process|Clear updates/)).toBeNull();
    expect(screen.queryAllByRole("heading", { level: 2 })).toHaveLength(0);
  });

  it("shows no 'How we hire' section and lays each role out as a card with its facts", async () => {
    mockData(
      [
        job({
          id: "pay",
          title: "Patent Analyst",
          department: "Drafting",
          location: "Bengaluru",
          employment_type: "FULL_TIME",
          work_mode: "HYBRID",
          compensation_min: "500000",
          compensation_max: "700000",
          compensation_currency: "INR",
          compensation_period: "ANNUAL",
        }),
      ],
      [],
    );
    renderAt("/org/zodiac-patent?view=jobs");

    const card = await screen.findByRole("link", { name: /Patent Analyst/ });
    expect(card).toHaveAttribute("href", "/org/zodiac-patent/jobs/pay");
    expect(card).toHaveTextContent("Drafting");
    expect(card).toHaveTextContent("Bengaluru");
    expect(card).toHaveTextContent("Full-time");
    expect(card).toHaveTextContent("Hybrid");
    // pay is never on a careers card — it is shown on the job's own page only
    expect(card).not.toHaveTextContent(/INR|500|700|per year/);
    expect(card).toHaveTextContent("View role");
    expect(screen.getByText("1 open position")).toBeInTheDocument();
    expect(screen.queryByText(/How we hire|How applications are reviewed/i)).toBeNull();
    expect(screen.getByRole("img", { name: "Zodiac Patent" })).toBeInTheDocument(); // header logo
  });

  it("view=internships lists internships only, with an honest empty state", async () => {
    mockData(JOBS, []);
    const { unmount } = renderAt("/org/zodiac-patent?view=internships");

    expect(await screen.findByText("Patent Research Intern")).toBeInTheDocument();
    expect(screen.queryByText("Patent Analyst")).toBeNull();
    // a homepage-only view: no tab of its own, so neither tab is current
    expect(screen.getByRole("link", { name: "All" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Open Roles" })).not.toHaveAttribute("aria-current");
    unmount();

    vi.restoreAllMocks();
    vi.spyOn(careersApi, "getPublicOrganization").mockResolvedValue({
      name: "Zodiac Patent",
      slug: "zodiac-patent",
      careers_contact_email: null,
    });
    mockData([JOBS[0]], []);
    renderAt("/org/zodiac-patent?view=internships");
    expect(await screen.findByText("No internships open right now")).toBeInTheDocument();
  });

  it("view=programs lists programs and no roles", async () => {
    mockData(JOBS, [PROGRAM]);
    renderAt("/org/zodiac-patent?view=programs");

    const program = await screen.findByRole("link", { name: new RegExp(PROGRAM.title) });
    expect(program).toHaveAttribute("href", "/programs/program-1");
    expect(screen.queryByText("Patent Analyst")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Training Programs");
  });

  it("view=programs says so when no program is open", async () => {
    mockData(JOBS, []);
    renderAt("/org/zodiac-patent?view=programs");

    expect(await screen.findByText("No programs open right now")).toBeInTheDocument();
  });

  it("view=campus (the homepage's Campus Hiring link) lists everything All lists", async () => {
    mockData(JOBS, [PROGRAM]);
    renderAt("/org/zodiac-patent?view=campus");

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Campus Hiring");
    expect(await screen.findByText("Patent Analyst")).toBeInTheDocument();
    expect(screen.getByText("Patent Research Intern")).toBeInTheDocument();
    expect(await screen.findByText(PROGRAM.title)).toBeInTheDocument();
    expect(screen.queryByText("Apply with a drive link")).toBeNull();
  });

  it("view=all (where three homepage cards lead) is the All view", async () => {
    mockData(JOBS, [PROGRAM]);
    renderAt("/org/zodiac-patent?view=all");

    expect(await screen.findByText("Patent Research Intern")).toBeInTheDocument();
    expect(screen.getByText("Illustrator")).toBeInTheDocument();
    expect(await screen.findByText(PROGRAM.title)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Explore Career Opportunities");
  });

  it("treats an unknown view as the full page", async () => {
    mockData(JOBS, []);
    renderAt("/org/zodiac-patent?view=bogus");

    expect(await screen.findByText("Patent Research Intern")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Explore Career Opportunities");
  });
});
