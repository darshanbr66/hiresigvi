import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { CandidateResponse } from "../../../types/recruitment";
import * as jobsApi from "../jobs/api";
import * as candidatesApi from "./api";
import { CandidatesPage } from "./CandidatesPage";

const auth = vi.hoisted(() => ({ roles: ["RECRUITER"] as string[] }));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: auth.roles } }),
}));

const candidate = {
  id: "cand-1",
  organization_id: "org-1",
  email: "priya@example.com",
  full_name: "Priya Candidate",
  phone: "+919876543210",
  location: "Chennai",
  current_title: "Software Engineer",
  years_experience: 3,
  source: "PORTAL",
  deleted_at: null,
} as CandidateResponse;

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>
          <CandidatesPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("CandidatesPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(candidatesApi, "listCandidates").mockResolvedValue([candidate]);
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([]);
  });

  it("lists candidates and offers Reapply Permission to recruiters", async () => {
    auth.roles = ["RECRUITER"];
    renderPage();

    expect(await screen.findByText("Priya Candidate")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Reapply Permission" })).toBeInTheDocument();
  });

  it("hides Reapply Permission from roles that can't grant it", async () => {
    auth.roles = ["INTERVIEWER"];
    renderPage();

    expect(await screen.findByText("Priya Candidate")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Reapply Permission" })).not.toBeInTheDocument();
  });
});
