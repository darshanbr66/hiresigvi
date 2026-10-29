import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { PublicProgramResult, PublicProgramView } from "../../types/program";
import { ThemeProvider } from "../theme/ThemeContext";
import * as careersApi from "./api";
import { ProgramApplyPage } from "./ProgramApplyPage";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/programs/prog-1"]}>
          <Routes>
            <Route path="/programs/:programId" element={<ProgramApplyPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

/** A published program whose fee and scholarship amounts are not finalized:
 * only HR's approved wording is configured. */
const unfinalized: PublicProgramView = {
  kind: "program",
  id: "prog-1",
  title: "MTech/PhD Scholarship & Paid Training",
  description: "Train with us, then build a career here.",
  organization_name: "SIGVITAS",
  careers_contact_email: null,
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
  fee_disclosure: "The training fee will be communicated before enrolment.",
  scholarship_amount_min: null,
  scholarship_amount_max: null,
  scholarship_currency: null,
  scholarship_eligibility_rules: null,
  scholarship_disbursement_timing: null,
  scholarship_disclosure: "Scholarship amounts are being finalized.",
  employment_pathway: null,
  bond_duration_months: 36,
  bond_terms_summary: null,
  company_funding_type: null,
  company_funding_value: null,
  has_assessment: true,
  interview_stages: ["Technical interview"],
  required_documents: [],
  candidate_disclosures: "Selection is subject to the company's approved process.",
  terms_version: "v1",
  disclosure_text: "Selection is subject to the company's approved process.",
  application_opens_at: null,
  application_closes_at: null,
  accepting_applications: true,
  not_accepting_reason: null,
};

describe("ProgramApplyPage", () => {
  it("discloses terms without inventing unfinalized amounts", async () => {
    vi.spyOn(careersApi, "getPublicProgram").mockResolvedValue(unfinalized);

    renderPage();

    expect(await screen.findByRole("heading", { name: unfinalized.title })).toBeInTheDocument();
    expect(screen.getByText("Duration: 6 months or 12 months. The training fee is paid by the participant.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(unfinalized.fee_disclosure as string)).toBeInTheDocument();
    expect(screen.getByText(unfinalized.scholarship_disclosure as string)).toBeInTheDocument();
    // No amount line at all while the business hasn't set one.
    expect(screen.queryByText(/^Training fee:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Scholarship:/)).not.toBeInTheDocument();
    // Eligibility vs award, and no employment guarantee, are stated plainly.
    expect(screen.getByText(/not the same as receiving a scholarship/)).toBeInTheDocument();
    expect(screen.getByText(/does not by itself\s+guarantee employment/)).toBeInTheDocument();
    expect(screen.getByText(/36-month commitment/)).toBeInTheDocument();
  });

  it("shows finalized amounts when HR has set them", async () => {
    vi.spyOn(careersApi, "getPublicProgram").mockResolvedValue({
      ...unfinalized,
      training_fee_amount: "50000.00",
      training_fee_currency: "INR",
      scholarship_amount_min: "10000.00",
      scholarship_amount_max: "20000.00",
      scholarship_currency: "INR",
    });

    renderPage();

    expect(await screen.findByText(/^Training fee: INR/)).toBeInTheDocument();
    expect(screen.getByText(/^Scholarship: INR .* to INR/)).toBeInTheDocument();
  });

  it("lists what the declaration still lacks until the terms are accepted", async () => {
    vi.spyOn(careersApi, "getPublicProgram").mockResolvedValue(unfinalized);

    renderPage();

    expect(await screen.findByText("Read and accept the program terms.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Education level"), { target: { value: "MASTERS" } });
    expect(screen.getByText("This program isn't open to that education level.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Education level"), { target: { value: "MTECH" } });
    fireEvent.change(screen.getByLabelText(/Current year of study/), { target: { value: "1" } });
    expect(screen.getByText("This program is open to year 2 students.")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("I have read and accept these program terms."));
    expect(screen.queryByText("Read and accept the program terms.")).not.toBeInTheDocument();
  });

  it("shows only the closed message for an unavailable program", async () => {
    const unavailable: PublicProgramResult = {
      kind: "unavailable",
      message: "This program is no longer accepting applications.",
    };
    vi.spyOn(careersApi, "getPublicProgram").mockResolvedValue(unavailable);

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "This Program Is No Longer Accepting Applications" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Apply for this program" })).not.toBeInTheDocument();
  });
});
