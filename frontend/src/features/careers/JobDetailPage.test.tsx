import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
  organization: { name: "SIGVITAS", slug: "sigvitas", careers_contact_email: "careers@example.com" },
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/org/sigvitas/jobs/job-1"]}>
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
