import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineStatsPanel } from "./PipelineStatsPanel";

const stats = {
  applications_by_status: { APPLIED: 1, SCREENING: 1, AI_SCREENED_OUT: 2 },
  open_jobs: 3,
  top_jobs_by_applications: [
    { title: "Data Analyst", application_count: 2 },
    { title: "Senior Principal Machine Learning Infrastructure Engineer — Platform", application_count: 1 },
  ],
};

describe("PipelineStatsPanel", () => {
  it("renders statuses, open jobs and top jobs as readable UI, never raw JSON", () => {
    const { container } = render(<PipelineStatsPanel stats={stats} />);

    expect(screen.getByText("Open jobs").nextSibling).toHaveTextContent("3");
    expect(screen.getByText("Applications").nextSibling).toHaveTextContent("4");

    const statusSection = screen.getByRole("heading", { name: "Applications by status" }).closest("section")!;
    const statusRows = within(statusSection).getAllByRole("listitem");
    expect(statusRows.map((row) => row.textContent)).toEqual(["AI screened out2", "Applied1", "Screening1"]);

    const jobsSection = screen.getByRole("heading", { name: "Top jobs by applications" }).closest("section")!;
    const jobRows = within(jobsSection).getAllByRole("listitem");
    expect(jobRows[0]).toHaveTextContent("Data Analyst");
    expect(jobRows[0]).toHaveTextContent("2 applications");
    expect(jobRows[1]).toHaveTextContent("1 application");

    expect(container.textContent).not.toMatch(/[{}[\]"]/);
    expect(container.textContent).not.toContain("application_count");
  });

  it("shows only the sections that are present", () => {
    render(<PipelineStatsPanel stats={{ open_jobs: 0 }} />);
    expect(screen.getByText("Open jobs")).toBeInTheDocument();
    expect(screen.queryByText("Applications by status")).not.toBeInTheDocument();
    expect(screen.queryByText("Top jobs by applications")).not.toBeInTheDocument();
  });

  it("shows empty-state copy for empty sections", () => {
    render(<PipelineStatsPanel stats={{ applications_by_status: {}, open_jobs: 0, top_jobs_by_applications: [] }} />);
    expect(screen.getByText("No applications yet.")).toBeInTheDocument();
    expect(screen.getByText("No jobs have applications yet.")).toBeInTheDocument();
  });

  it.each([
    ["a serialized JSON string", JSON.stringify(stats)],
    ["a malformed object", { applications_by_status: [1, 2], top_jobs_by_applications: "Data Analyst" }],
  ])("falls back to a clean note for %s", (_label, raw) => {
    const { container } = render(<PipelineStatsPanel stats={raw} />);
    expect(screen.getByRole("note")).toHaveTextContent(/aren't available/i);
    expect(container.textContent).not.toMatch(/[{}[\]]/);
  });
});
