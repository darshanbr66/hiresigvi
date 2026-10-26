import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { ProgramReport, ProgramResponse } from "../../../types/program";
import * as api from "./api";
import { ProgramDetailPage } from "./ProgramDetailPage";

const auth = vi.hoisted(() => ({ roles: ["ORG_ADMIN"] as string[] }));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: auth.roles } }),
}));

const draft: ProgramResponse = {
  id: "prog-1",
  title: "MTech/PhD Scholarship & Paid Training",
  description: "Train with us.",
  status: "DRAFT",
  job_id: "job-1",
  eligible_education_levels: ["MTECH", "PHD"],
  eligible_study_years: [2],
  eligible_specializations: [],
  eligible_institutions: [],
  expected_completion_from: null,
  expected_completion_to: null,
  eligibility_notes: null,
  training_duration_options: [6, 12],
  training_fee_amount: null,
  training_fee_currency: null,
  fee_payment_terms: null,
  fee_disclosure: null,
  scholarship_amount_min: null,
  scholarship_amount_max: null,
  scholarship_currency: null,
  scholarship_eligibility_rules: null,
  scholarship_disbursement_timing: null,
  scholarship_disclosure: null,
  employment_pathway: null,
  bond_duration_months: 36,
  bond_terms_summary: null,
  bond_start_rule: null,
  application_opens_at: null,
  application_closes_at: null,
  assessment_id: null,
  assessment_title: null,
  interview_stages: [],
  required_documents: [],
  candidate_disclosures: null,
  terms_version: null,
  tracks_fee_payments: false,
  tracks_training: false,
  tracks_employment: false,
  requires_agreement: true,
  published_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  publication_problems: ["Set the training fee or approved fee disclosure wording."],
  enrollment_count: 0,
};

const emptyReport = Object.fromEntries(
  [
    "applications", "eligibility_pending", "eligible", "not_eligible", "assessment_invited",
    "assessment_passed", "assessment_failed", "assessment_pending_review", "interview",
    "interview_completed", "selected", "not_selected", "scholarship_eligible", "scholarship_pending",
    "scholarship_approved", "scholarship_declined", "training_enrolled", "training_in_progress",
    "training_completed", "offers_made", "offers_accepted", "joined", "agreements_signed",
  ].map((key) => [key, 0]),
) as unknown as ProgramReport;

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/recruiter/programs/prog-1"]}>
          <Routes>
            <Route path="/recruiter/programs/:programId" element={<ProgramDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("ProgramDetailPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    auth.roles = ["ORG_ADMIN"];
    vi.spyOn(api, "getProgramReport").mockResolvedValue(emptyReport);
    vi.spyOn(api, "listEnrollments").mockResolvedValue([]);
  });

  it("shows unfinalized values as such and lists what blocks publication", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue(draft);

    renderPage();

    expect(await screen.findByRole("heading", { name: draft.title })).toBeInTheDocument();
    expect(screen.getByText(/fee not finalized/)).toBeInTheDocument();
    expect(screen.getByText("Amount not finalized")).toBeInTheDocument();
    expect(screen.getByText(/start rule not finalized/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Before this program can be published" })).toBeInTheDocument();
    expect(screen.getByText(draft.publication_problems[0])).toBeInTheDocument();
  });

  it("shows the server's publication problems when publishing is refused", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue(draft);
    const change = vi.spyOn(api, "changeProgramStatus").mockRejectedValue(
      new ApiError("Program is not ready to publish.", 422, "PROGRAM_NOT_PUBLISHABLE", [], {
        problems: ["Set the program terms version."],
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Publish" }));

    await waitFor(() => expect(change).toHaveBeenCalledWith("prog-1", "PUBLISHED", "test-token"));
    expect(await screen.findByText("Set the program terms version.")).toBeInTheDocument();
    expect(screen.getByText(/can't be published yet/)).toBeInTheDocument();
  });

  it("asks for confirmation before archiving", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue(draft);
    const change = vi.spyOn(api, "changeProgramStatus").mockResolvedValue({ ...draft, status: "ARCHIVED" });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));

    expect(change).not.toHaveBeenCalled();
    expect(await screen.findByText(/Archived programs can't be edited or reopened/)).toBeInTheDocument();
  });

  it("offers no management controls to a read-only role", async () => {
    auth.roles = ["HIRING_MANAGER"];
    vi.spyOn(api, "getProgram").mockResolvedValue(draft);

    renderPage();

    expect(await screen.findByRole("heading", { name: draft.title })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit configuration" })).not.toBeInTheDocument();
  });
});
