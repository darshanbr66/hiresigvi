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
  it("leaves an inner 'Job Description' line plain and bolds the statement after it", async () => {
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
    expect(within(body).getByText("Apply only if you meet all the requirements and accept all the terms.").tagName).toBe("STRONG");
    expect(within(body).getByRole("heading", { level: 3, name: "Benefits" })).toBeInTheDocument();
  });

  it("bolds the lead statement and each line's leading label, leaving the rest regular", async () => {
    vi.spyOn(careersApi, "getOpenJob").mockResolvedValue({
      ...baseJob,
      description:
        "Apply only if you meet all the requirements and accept all the terms.\n\nBenefits\n- Health insurance.\n\nInterview Process: Two rounds.\nBond: 2 years.",
    });

    renderPage();

    await screen.findByRole("heading", { level: 3, name: "Benefits" });
    const bold = Array.from(document.querySelectorAll(".job-description-body strong")).map((el) => el.textContent);
    expect(bold).toEqual([
      "Apply only if you meet all the requirements and accept all the terms.",
      "Interview Process:",
      "Bond:",
    ]);
    // the text after each label is not bold
    expect(screen.getByText(/Two rounds\./).tagName).not.toBe("STRONG");
    expect(screen.getByText("Health insurance.").closest("strong")).toBeNull();
  });
});
