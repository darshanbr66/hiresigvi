import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { EnrollmentResponse, FundingRevisionResponse, ProgramResponse } from "../../../types/program";
import * as api from "./api";
import { EnrollmentPanel } from "./EnrollmentPanel";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { id: "user-1", roles: ["ORG_ADMIN"] } }),
}));

const program = {
  id: "prog-1",
  title: "Scholarship Program",
  status: "PUBLISHED",
  training_duration_options: [6, 12],
  training_fee_amount: "100000.00",
  training_fee_currency: "INR",
  scholarship_currency: "INR",
  company_funding_type: "PERCENTAGE",
  company_funding_value: "40",
  assessment_id: "assess-1",
  assessment_title: "Selection test",
  interview_stages: [],
  tracks_fee_payments: true,
  tracks_training: false,
  tracks_employment: false,
  requires_agreement: false,
} as unknown as ProgramResponse;

const ALL = ["interview.manage", "interview.read", "program.employment.approve", "program.finance.manage", "program.manage", "program.read"];

/** Selected after interview, eligible for funding consideration, nothing
 * proposed yet. */
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
  latest_result: { percentage: 80, passed: true, grading_status: "FINAL", attempt_number: 1, pass_score_applied: 60 },
  interview_completed_at: new Date().toISOString(),
  scholarship_eligibility: "ELIGIBLE",
  scholarship_eligibility_decided_at: new Date().toISOString(),
  scholarship_status: "PENDING",
  approved_scholarship_amount: null,
  approved_scholarship_currency: null,
  scholarship_decided_at: null,
  scholarship_decision_note: null,
  affiliation: null,
  training_status: "NOT_ENROLLED",
  training_duration_months: null,
  training_start_date: null,
  training_end_date: null,
  training_enrolled_at: null,
  training_completed_on: null,
  training_outcome_note: null,
  milestones: [],
  milestones_overdue: 0,
  next_milestone_due: null,
  employment_review_status: "NOT_STARTED",
  employment_role_title: null,
  employment_department_id: null,
  employment_proposed_start_date: null,
  employment_decided_at: null,
  employment_decision_note: null,
  employee_id: null,
  offer_status: "NOT_OFFERED",
  offer_made_at: null,
  joined_on: null,
  agreement_status: "NOT_REQUIRED",
  agreement_reference: null,
  agreement_version: null,
  agreement_terms_snapshot: null,
  agreement_terms_current: null,
  agreement_sent_at: null,
  agreement_signed_on: null,
  agreement_effective_date: null,
  agreement_expiry_date: null,
  agreement_status_note: null,
  agreement_document_filename: null,
  agreement_document_uploaded_at: null,
  bond_duration_months: null,
  bond_start_date: null,
  bond_end_date: null,
  bond_applies_reason: null,
  interviews: [],
  viewer_permissions: ALL,
  finance_visible: true,
  fee_amount: null,
  fee_currency: null,
  fee_due_date: null,
  company_contribution_amount: null,
  candidate_payable_amount: null,
  balance_due: null,
  payment_status: "NOT_APPLICABLE",
  funding_terms_accepted_revision: null,
  funding_terms_accepted_at: null,
  fee_paid_total: "0",
  disbursed_total: "0",
  fee_payments: [],
  disbursements: [],
  funding_revisions: [],
};

const proposed: FundingRevisionResponse = {
  id: "rev-1",
  revision_number: 1,
  kind: "INITIAL",
  status: "PROPOSED",
  fee_amount: "100000.00",
  currency: "INR",
  funding_type: "PERCENTAGE",
  funding_value: "40.00",
  company_contribution_amount: "40000.00",
  candidate_payable_amount: "60000.00",
  reason: null,
  proposed_by_user_id: "user-2",
  decided_by_user_id: null,
  decided_at: null,
  decision_note: null,
  created_at: new Date().toISOString(),
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
  });

  it("previews the fee split and submits a funding proposal — never an approval", async () => {
    vi.spyOn(api, "getEnrollment").mockResolvedValue(selected);
    const action = vi.spyOn(api, "enrollmentAction").mockResolvedValue({
      ...selected, scholarship_status: "PROPOSED", funding_revisions: [proposed],
    });

    renderPanel();

    // Pre-filled from the program's configured basis (40%); 100000 -> 40000 / 60000.
    expect(await screen.findByTestId("proposal-preview")).toHaveTextContent(
      "Fee INR 100,000.00 → company INR 40,000.00, candidate pays INR 60,000.00.",
    );
    fireEvent.change(screen.getByLabelText("Percentage"), { target: { value: "120" } });
    expect(screen.getByRole("alert")).toHaveTextContent("can't be more than 100%");
    expect(screen.getByRole("button", { name: "Submit for approval" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Percentage"), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit for approval" }));

    await waitFor(() =>
      expect(action).toHaveBeenCalledWith(
        "prog-1", "enr-1", "funding-proposals",
        { funding_type: "PERCENTAGE", value: "40", reason: null },
        "test-token",
      ),
    );
  });

  it("lets a finance approver decide an open proposal; rejection needs a note", async () => {
    vi.spyOn(api, "getEnrollment").mockResolvedValue({
      ...selected, scholarship_status: "PROPOSED", funding_revisions: [proposed],
    });
    const action = vi.spyOn(api, "enrollmentAction").mockResolvedValue(selected);

    renderPanel();

    expect(await screen.findByText("Revision 1 is awaiting approval.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Approve funding" }));
    await waitFor(() =>
      expect(action).toHaveBeenCalledWith(
        "prog-1", "enr-1", "funding-proposals/rev-1/decision", { decision: "APPROVED", note: null }, "test-token",
      ),
    );
  });

  it("shows the fee obligation, payable amount and payment status to finance", async () => {
    vi.spyOn(api, "getEnrollment").mockResolvedValue({
      ...selected,
      scholarship_status: "APPROVED",
      fee_amount: "100000.00",
      fee_currency: "INR",
      company_contribution_amount: "40000.00",
      candidate_payable_amount: "60000.00",
      fee_paid_total: "5000.00",
      balance_due: "55000.00",
      payment_status: "OVERDUE",
      funding_revisions: [{ ...proposed, status: "APPROVED" }],
    });

    renderPanel();

    expect(await screen.findByText("Balance due")).toBeInTheDocument();
    expect(screen.getAllByText("INR 60,000.00").length).toBeGreaterThan(0);
    expect(screen.getByText("INR 55,000.00")).toBeInTheDocument();
    expect(screen.getByText("Overdue")).toBeInTheDocument();
    // A payment needs a confirming reference before it can be recorded.
    fireEvent.change(screen.getByLabelText("Amount (INR)"), { target: { value: "1000" } });
    fireEvent.change(screen.getByLabelText("Paid on"), { target: { value: "2027-01-01" } });
    expect(screen.getByRole("button", { name: "Record payment" })).toBeDisabled();
  });

  it("gives a recruiter proposals but no approval and no money details", async () => {
    vi.spyOn(api, "getEnrollment").mockResolvedValue({
      ...selected,
      scholarship_status: "PROPOSED",
      funding_revisions: [proposed],
      viewer_permissions: ["interview.manage", "interview.read", "program.manage", "program.read"],
      finance_visible: false,
    });

    renderPanel();

    expect(await screen.findByRole("button", { name: "Withdraw proposal" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve funding" })).not.toBeInTheDocument();
    expect(screen.queryByText("Training fee and payments")).not.toBeInTheDocument();
  });

  it("is read-only for a viewer with only program.read", async () => {
    vi.spyOn(api, "getEnrollment").mockResolvedValue({
      ...selected, viewer_permissions: ["program.read"], finance_visible: false,
    });

    renderPanel();

    expect(await screen.findByText("Asha Rao — Scholarship Program")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Not eligible" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit for approval" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Record/ })).not.toBeInTheDocument();
  });

  it("blocks eligibility until the institution membership is verified", async () => {
    vi.spyOn(api, "getEnrollment").mockResolvedValue({
      ...selected,
      application_status: "UNDER_REVIEW",
      eligibility_status: "PENDING",
      affiliation: {
        id: "aff-1", status: "PENDING", method: null, institution_id: null,
        reviewed_by_user_id: null, reviewed_at: null, review_note: null,
      },
    });
    const action = vi.spyOn(api, "enrollmentAction").mockResolvedValue(selected);

    renderPanel();

    expect(await screen.findByRole("button", { name: "Mark eligible" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Verify membership" }));
    await waitFor(() =>
      expect(action).toHaveBeenCalledWith(
        "prog-1", "enr-1", "affiliation",
        { status: "VERIFIED", method: "STUDENT_ID_DOCUMENT", note: null },
        "test-token",
      ),
    );
  });
});
