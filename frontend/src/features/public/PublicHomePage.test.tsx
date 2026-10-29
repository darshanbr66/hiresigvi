import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as careersApi from "../careers/api";
import { ThemeProvider } from "../theme/ThemeContext";
import { PublicHomePage } from "./PublicHomePage";

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("PublicHomePage", () => {
  it("presents HireZ with a text-only wordmark, never the old product name", () => {
    const { container } = renderWithProviders(<PublicHomePage />);

    const nav = container.querySelector(".landing-nav") as HTMLElement;
    expect(within(nav).getByRole("img", { name: "HireZ" })).toHaveTextContent("HireZ");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hiring, organized end to end.");
    expect(screen.queryByText(/ai recruitment platform/i)).not.toBeInTheDocument();
  });

  it("is a short product page: capabilities, how it works, campus, and a closing call to action", () => {
    renderWithProviders(<PublicHomePage />);

    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "Everything your hiring team needs, in one place",
      "From application to offer",
      "Built for hiring from campus",
      "Hire with clarity.",
    ]);
    for (const capability of ["Recruitment management", "Candidate screening", "Assessments", "Interviews", "Campus hiring"]) {
      expect(screen.getByRole("heading", { name: capability })).toBeInTheDocument();
    }
  });

  it("keeps internal operating rules off the public page", () => {
    renderWithProviders(<PublicHomePage />);

    for (const internal of [
      /approver/i,
      /approve their own/i,
      /audit/i,
      /organization boundaries/i,
      /pass mark/i,
      /funding/i,
      /separate approvals/i,
      /recorded decision/i,
    ]) {
      expect(screen.queryByText(internal)).not.toBeInTheDocument();
    }
  });

  it("makes no unsupported claims: no testimonials, customer logos or statistics", () => {
    const { container } = renderWithProviders(<PublicHomePage />);

    expect(screen.queryByText(/testimonial|trusted by|customers|\d+%/i)).not.toBeInTheDocument();
    expect(container.querySelector("blockquote")).toBeNull();
  });

  it("sends every call to action to a real destination and doesn't fetch jobs", () => {
    const listOpenJobs = vi.spyOn(careersApi, "listOpenJobs");
    renderWithProviders(<PublicHomePage />);

    const signIn = screen.getAllByRole("link", { name: /staff sign in/i });
    expect(signIn.length).toBeGreaterThan(0);
    for (const link of signIn) expect(link).toHaveAttribute("href", "/recruiter/login");
    const roles = screen.getAllByRole("link", { name: /open roles/i });
    expect(roles.length).toBeGreaterThan(0);
    for (const link of roles) expect(link).toHaveAttribute("href", "/org/sigvitas");
    expect(listOpenJobs).not.toHaveBeenCalled();
  });

  it("previews the product as one described image with real stages and no invented data", () => {
    const { container } = renderWithProviders(<PublicHomePage />);

    const preview = screen.getByRole("img", { name: /preview of a hirez application/i });
    expect(container.querySelector("canvas")).toBeNull();
    // the parts are presentation only for assistive tech
    for (const part of preview.children) expect(part).toHaveAttribute("aria-hidden", "true");
    for (const stage of ["Applied", "Screening", "Assessment", "Interview", "Selected", "Hired"]) {
      expect(within(preview).getAllByText(stage).length).toBeGreaterThan(0);
    }
    // no scores, counts or rates — and no personal names (placeholder lines instead)
    expect(preview.textContent).not.toMatch(/\d/);
  });
});
