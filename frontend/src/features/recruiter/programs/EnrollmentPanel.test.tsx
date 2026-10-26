import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { EnrollmentResponse, ProgramResponse } from "../../../types/program";
import * as api from "./api";
import { EnrollmentPanel } from "./EnrollmentPanel";

const auth = vi.hoisted(() => ({ roles: ["ORG_ADMIN"] as string[] }));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: auth.roles } }),
}));

const program = {
  id: "prog-1",
  title: "Scholarship Program",
  status: "PUBLISHED",
  training_duration_options: [6, 12],
  training_fee_currency: null,
  scholarship_currency: "INR",
  assessment_id: "assess-1",
  assessment_title: "Selection test",
  interview_stages: [],
  tracks_fee_payments: false,
  tracks_training: false,
  tracks_employment: false,
  requires_agreement: false,
} as unknown as ProgramResponse;

/** Selected after interview, eligible for scholarship consideration, with
 * the decision itself still pending. */
const selected: EnrollmentResponse = {
  id: "enr-1",
  program_id: "prog-1",
  application_id: "app-1",
  candidate_id: "cand-1",
  candidate_full_name: "Asha Rao",
  candidate_email: "asha@example.test",
  application_status: "SELECTED",
  stage: "Selected",
  applied_at: new Date().toISOString(),
  education_level: "MTECH",
  study_year: 2,
  specialization: "Robotics",
  institution_id: null,
  institution_name: "COEP",
  expected_completion_date: null,
  terms_version_accepted: "v1",
  terms_accepted_at: new Date().toISOString(),
  eligibility_status: "ELIGIBLE",
  eligibility_reviewed_at: new Date().toISOString(),
  eligibility_note: null,
  latest_result: { percentage: 80, passed: true, grading_status: "FINAL", attempt_number: 1 },
  interview_completed_at: new Date().toISOString(),
  scholarship_eligibility: "ELIGIBLE",
  scholarship_eligibility_decided_at: new Date().toISOString(),
  scholarship_status: "PENDING",
  approved_scholarship_amount: null,
  approved_scholarship_currency: null,
  scholarship_decided_at: null,
  scholarship_decision_note: null,
  training_status: "NOT_ENROLLED",
  training_duration_months: null,
  training_start_date: null,
  training_end_date: null,
  offer_status: "NOT_OFFERED",
  offer_made_at: null,
  joined_on: null,
  agreement_status: "NOT_REQUIRED",
  agreement_reference: null,
  agreement_signed_on: null,
  agreement_document_filename: null,
  agreement_document_uploaded_at: null,
  bond_duration_months: null,
  bond_start_date: null,
  bond_end_date: null,
  fee_paid_total: "0",
  disbursed_total: "0",
  fee_payments: [],
  disbursements: [],
};

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>
          <EnrollmentPanel program={program} enrollmentId="enr-1" onClose={() => {}} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("EnrollmentPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getEnrollment").mockResolvedValue(selected);
  });

  it("lets finance approve a pending scholarship with an explicit amount", async () => {
    auth.roles = ["ORG_ADMIN"];
    const action = vi
      .spyOn(api, "enrollmentAction")
      .mockResolvedValue({ ...selected, scholarship_status: "APPROVED", approved_scholarship_amount: "25000" });

    renderPanel();

    const approve = await screen.findByRole("button", { name: "Approve" });
    // No amount -> nothing to approve; the platform never invents one.
    expect(approve).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Approved amount"), { target: { value: "25000" } });
    fireEvent.click(approve);

    await waitFor(() =>
      expect(action).toHaveBeenCalledWith(
        "prog-1",
        "enr-1",
        "scholarship-decision",
        { decision: "APPROVED", approved_amount: "25000", currency: "INR" },
        "test-token",
      ),
    );
  });

  it("gives a recruiter workflow controls but no scholarship decision", async () => {
    auth.roles = ["RECRUITER"];

    renderPanel();

    expect(await screen.findByRole("button", { name: "Not eligible" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Decline" })).not.toBeInTheDocument();
  });

  it("is read-only for a role with only program.read", async () => {
    auth.roles = ["HIRING_MANAGER"];

    renderPanel();

    expect(await screen.findByText("Asha Rao — Scholarship Program")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Not eligible" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Record/ })).not.toBeInTheDocument();
  });
});
