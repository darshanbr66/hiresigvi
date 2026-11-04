import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { ProgramSettingsResponse } from "../../../types/program";
import * as api from "./api";
import { ProgramSettingsPage } from "./ProgramSettingsPage";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: ["ORG_ADMIN"] } }),
}));

const settings: ProgramSettingsResponse = {
  training_duration_options: [6, 12],
  training_fee_amount: "50000.00",
  currency: "INR",
  company_funding_type: "PERCENTAGE",
  company_funding_value: "50.00",
  funding_requires_independent_approval: true,
  internship_duration_months: 6,
  bond_required: true,
  bond_duration_months: 36,
  eligible_education_levels: ["MTECH", "PHD"],
  study_year_rules: { MTECH: [2] },
  updated_by_user_id: null,
  updated_by_name: null,
  updated_at: new Date().toISOString(),
  preview_company_contribution: "25000.00",
  preview_candidate_payable: "25000.00",
  can_edit: true,
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>
          <ProgramSettingsPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("ProgramSettingsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the defaults with the fee split and saves the whole settings document", async () => {
    vi.spyOn(api, "getProgramSettings").mockResolvedValue(settings);
    const save = vi.spyOn(api, "updateProgramSettings").mockResolvedValue(settings);

    renderPage();

    expect(await screen.findByTestId("settings-funding-preview")).toHaveTextContent(
      "company INR 25,000.00, candidate pays INR 25,000.00 of INR 50,000.00",
    );
    expect(screen.getByLabelText("Eligible study years for M.Tech")).toHaveValue("2");

    fireEvent.change(screen.getByPlaceholderText("e.g. 6, 12"), { target: { value: "3, 9, 9" } });
    fireEvent.click(screen.getByLabelText("Funded participants agree to a commitment (bond)"));
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        {
          training_duration_options: [3, 9],
          training_fee_amount: "50000.00",
          currency: "INR",
          company_funding_type: "PERCENTAGE",
          company_funding_value: "50.00",
          funding_requires_independent_approval: true,
          internship_duration_months: 6,
          bond_required: false,
          bond_duration_months: null,
          eligible_education_levels: ["MTECH", "PHD"],
          study_year_rules: { MTECH: [2] },
        },
        "test-token",
      ),
    );
  });

  it("refuses out-of-range durations before calling the server", async () => {
    vi.spyOn(api, "getProgramSettings").mockResolvedValue(settings);
    const save = vi.spyOn(api, "updateProgramSettings");

    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("e.g. 6, 12"), { target: { value: "61" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("between 1 and 60"))).toBe(true);
    expect(save).not.toHaveBeenCalled();
  });

  it("is read-only for users who can't edit", async () => {
    vi.spyOn(api, "getProgramSettings").mockResolvedValue({ ...settings, can_edit: false });

    renderPage();

    expect(await screen.findByText(/Only organization administrators can change/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g. 6, 12")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save settings" })).not.toBeInTheDocument();
  });
});
