import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { PublicJobDetail } from "../../types/careers";
import { ThemeProvider } from "../theme/ThemeContext";
import { JobDetailPage } from "./JobDetailPage";
import * as careersApi from "./api";

const baseJob: PublicJobDetail = {
  id: "job-1",
  title: "Backend Engineer",
  department: "Engineering",
  location: "Remote",
  employment_type: "FULL_TIME",
  openings_count: 1,
  created_at: new Date().toISOString(),
  description: "Own the recruitment platform's backend.",
  organization: { name: "Zodiac Patent", slug: "zodiac-patent", careers_contact_email: "careers@example.com" },
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/org/zodiac-patent/jobs/job-1"]}>
          <Routes>
            <Route path="/org/:slug/jobs/:jobId" element={<JobDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("JobDetailPage job description visibility", () => {
  it("renders the Job Description section when visible", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue(baseJob);

    renderPage();

    expect(await screen.findByRole("heading", { name: "Job Description" })).toBeInTheDocument();
    expect(screen.getByText(baseJob.description as string)).toBeInTheDocument();
  });

  it("omits the Job Description section entirely when hidden", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({ ...baseJob, description: null });

    renderPage();

    await screen.findByRole("heading", { name: "Backend Engineer" });
    expect(screen.queryByRole("heading", { name: "Job Description" })).not.toBeInTheDocument();
  });
});

describe("JobDetailPage role summary and description", () => {
  it("shows published pay on the job page, set apart from the other facts", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      work_mode: "HYBRID",
      compensation_min: "500000",
      compensation_max: "700000",
      compensation_currency: "INR",
      compensation_period: "ANNUAL",
    } as PublicJobDetail);

    renderPage();

    const summary = await screen.findByRole("list", { name: "Role summary" });
    expect(summary).toHaveTextContent("Engineering");
    expect(summary).toHaveTextContent("Remote");
    expect(summary).toHaveTextContent("Full-time");
    expect(summary).toHaveTextContent("Hybrid");
    expect(summary).toHaveTextContent("1 opening");
    expect(within(summary).getByText(/INR [\d,]+ – [\d,]+ per year/)).toHaveClass("job-pill-pay");
    expect(screen.getByRole("link", { name: /Apply for this role/ })).toHaveAttribute("href", "#apply");
  });

  it("does not invent pay when none is published", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue(baseJob);

    renderPage();

    const summary = await screen.findByRole("list", { name: "Role summary" });
    expect(summary.querySelector(".job-pill-pay")).toBeNull();
    expect(summary).not.toHaveTextContent(/INR|per year|per month|Compensation/);
  });

  it("structures the description into headings and lists without changing its words", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      description: "About the role\nOwn the backend.\n\nKey responsibilities\n- Design APIs.\n- Review code.",
    });

    renderPage();

    expect(await screen.findByRole("heading", { level: 3, name: "About the role" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Key responsibilities" })).toBeInTheDocument();
    expect(screen.getByText("Own the backend.")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(expect.arrayContaining(["Design APIs.", "Review code."]));
  });
});

describe("JobDetailPage description emphasis", () => {
  it("leaves an inner 'Job Description' line plain and shows the instruction after it as a callout", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      description:
        "Job Description\nApply only if you meet all the requirements and accept all the terms.\n\nBenefits\n- Health insurance.",
    });

    renderPage();

    // the card's own heading stays the only "Job Description" heading
    expect(await screen.findAllByRole("heading", { name: /Job Description/ })).toHaveLength(1);
    const body = document.querySelector(".job-description-body") as HTMLElement;
    const inner = within(body).getByText("Job Description");
    expect(inner.tagName).not.toMatch(/^H\d$/);
    expect(inner.closest("strong")).toBeNull();
    const notice = within(body).getByRole("note");
    expect(notice).toHaveClass("job-notice");
    expect(notice).toHaveTextContent(/^Apply only if you meet all the requirements and accept all the terms\.$/);
    expect(within(body).getByRole("heading", { level: 3, name: "Benefits" })).toBeInTheDocument();
  });

  it("bolds each line's leading label, leaving the rest regular", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      description:
        "Apply only if you meet all the requirements and accept all the terms.\n\nBenefits\n- Health insurance.\n\nInterview Process: Two rounds.\nBond: 2 years.",
    });

    renderPage();

    await screen.findByRole("heading", { level: 3, name: "Benefits" });
    const bold = Array.from(document.querySelectorAll(".job-description-body strong")).map((el) => el.textContent);
    expect(bold).toEqual([
      "Interview Process:",
      "Bond:",
    ]);
    // the text after each label is not bold
    expect(screen.getByText(/Two rounds\./).tagName).not.toBe("STRONG");
    expect(screen.getByText("Health insurance.").closest("strong")).toBeNull();
  });
});

describe("JobDetailPage role details", () => {
  it("shows each eligibility requirement and term as its own bullet under a bold label", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      min_experience_years: 0,
      max_experience_years: 2,
      eligibility_criteria: "- B.Tech / B.E. - 2024 batch - No active backlogs\n- 60% throughout",
      employment_terms: "Bond: 2 years\nNotice period: 1 month",
    } as PublicJobDetail);

    renderPage();

    const details = await screen.findByRole("region", { name: "Role details" });
    const entry = (label: string) => within(details).getByText(label).closest(".job-detail") as HTMLElement;

    expect(within(entry("Experience")).getByText("0–2 years").closest("p")).not.toBeNull();
    expect(within(entry("Experience")).queryByRole("list")).toBeNull();
    expect(entry("Experience")).not.toHaveClass("job-detail-wide");

    const eligibility = within(entry("Eligibility")).getAllByRole("listitem").map((li) => li.textContent);
    expect(eligibility).toEqual(["B.Tech / B.E.", "2024 batch", "No active backlogs", "60% throughout"]);
    expect(entry("Eligibility")).toHaveClass("job-detail-wide");

    const terms = within(entry("Terms")).getAllByRole("listitem");
    expect(terms.map((li) => li.textContent)).toEqual(["Bond: 2 years", "Notice period: 1 month"]);
    expect(within(terms[0]).getByText("Bond:").tagName).toBe("STRONG");
  });
});

const INSTRUCTION = "Apply only if you meet the requirements and are comfortable with the stated employment terms.";

describe("JobDetailPage formatting regressions", () => {
  it("renders the salary range as one bullet with a bold label and the values intact", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      description:
        "Compensation & Benefits\n- Annual salary: ₹6,00,000 - ₹8,00,000 per annum.\n- Health insurance.\n- Paid time off.\n- Provident Fund (PF).",
    });

    renderPage();

    const heading = await screen.findByRole("heading", { level: 3, name: "Compensation & Benefits" });
    const list = heading.nextElementSibling as HTMLElement;
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "Annual salary: ₹6,00,000 - ₹8,00,000 per annum.",
      "Health insurance.",
      "Paid time off.",
      "Provident Fund (PF).",
    ]);
    expect(within(items[0]).getByText("Annual salary:").tagName).toBe("STRONG");
    expect(within(items[0]).queryByRole("list")).toBeNull();
  });

  it("keeps schedules, ranges and hyphenated terms whole in Role details", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      min_experience_years: 1,
      max_experience_years: 3,
      work_schedule: "Mon - Fri, 9:30 AM - 6:30 PM",
      eligibility_criteria: "B.Tech / B.E. (2023 - 2025 batch)",
      employment_terms: "Employment Type: Full-time.\nWork Mode: On-site.",
    } as PublicJobDetail);

    renderPage();

    const details = await screen.findByRole("region", { name: "Role details" });
    const entry = (label: string) => within(details).getByText(label).closest(".job-detail") as HTMLElement;
    expect(within(entry("Experience")).getByText("1–3 years")).toBeInTheDocument();
    for (const [label, value] of [
      ["Schedule", "Mon - Fri, 9:30 AM - 6:30 PM"],
      ["Eligibility", "B.Tech / B.E. (2023 - 2025 batch)"],
    ]) {
      expect(within(entry(label)).getByText(value).closest("p")).not.toBeNull();
      expect(within(entry(label)).queryByRole("list")).toBeNull();
    }
    expect(within(entry("Terms")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Employment Type: Full-time.",
      "Work Mode: On-site.",
    ]);
  });

  it("shows the application instruction once, as a callout, even when Terms repeats it", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      employment_terms: `Bond: 2 years
${INSTRUCTION}`,
      description: `${INSTRUCTION}

Responsibilities
- Draft patent applications.`,
    } as PublicJobDetail);

    renderPage();

    await screen.findByRole("heading", { level: 3, name: "Responsibilities" });
    const notes = screen.getAllByRole("note");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toHaveTextContent(INSTRUCTION);
    expect(notes[0].closest(".job-description-body")).not.toBeNull();
    expect(screen.getAllByText(INSTRUCTION)).toHaveLength(1);
    expect(screen.getByText("Bond:")).toBeInTheDocument();
  });

  it("shows the instruction in Role details when only Terms carries it", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      employment_terms: INSTRUCTION,
    } as PublicJobDetail);

    renderPage();

    const details = await screen.findByRole("region", { name: "Role details" });
    expect(within(details).getByRole("note")).toHaveTextContent(INSTRUCTION);
  });

  it("renders nested requirement lists inside their parent item", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      description: "Requirements\n- Education\n  - B.Tech in CSE\n  - M.Tech\n- 60% throughout",
    });

    renderPage();

    const heading = await screen.findByRole("heading", { level: 3, name: "Requirements" });
    const list = heading.nextElementSibling as HTMLElement;
    const top = Array.from(list.children);
    expect(top).toHaveLength(2);
    expect(within(top[0] as HTMLElement).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "B.Tech in CSE",
      "M.Tech",
    ]);
  });
});
