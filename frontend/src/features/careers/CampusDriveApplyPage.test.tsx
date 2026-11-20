import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { PublicCampusDriveResult } from "../../types/publicCampusDrive";
import { ThemeProvider } from "../theme/ThemeContext";
import { CampusDriveApplyPage } from "./CampusDriveApplyPage";
import * as careersApi from "./api";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/campus-drive/tok123"]}>
          <Routes>
            <Route path="/campus-drive/:token" element={<CampusDriveApplyPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("CampusDriveApplyPage lifecycle states", () => {
  it("shows drive details and the apply form for an active drive", async () => {
    const active: PublicCampusDriveResult = {
      kind: "drive",
      name: "Fall Drive",
      college_name: "MIT",
      description: null,
      job_title: "Graduate Engineer",
      job_description: "Entry-level role.",
      organization_name: "Zodiac Patent",
      registration_deadline: null,
      status: "ACTIVE",
      has_assessment: false,
      careers_contact_email: "hr@zodiacpatent.com",
    };
    vi.spyOn(careersApi, "getCampusDriveByToken").mockResolvedValue(active);

    renderPage();

    expect(await screen.findByRole("heading", { name: "Fall Drive" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Apply for this drive" })).toBeInTheDocument();
    // Same mandatory, email-verified form as the careers site.
    for (const label of [/^Email/, /Mobile number/, /Date of birth/, /Place of birth/, /Highest qualification/]) {
      expect(screen.getByLabelText(label)).toBeRequired();
    }
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Your details");
    // the stepper's second step (its label is just "Verify")
    expect(screen.getByRole("list", { name: "Application steps" })).toHaveTextContent("Verify");
  });

  it("shows the unavailable panel and no recruitment content for a closed drive", async () => {
    const unavailable: PublicCampusDriveResult = {
      kind: "unavailable",
      message:
        "This campus recruitment drive has been closed and is no longer accepting applications.",
    };
    vi.spyOn(careersApi, "getCampusDriveByToken").mockResolvedValue(unavailable);

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "This Recruitment Drive Is No Longer Available" }),
    ).toBeInTheDocument();
    expect(screen.getByText(unavailable.message)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Careers" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("heading", { name: "Apply for this drive" })).not.toBeInTheDocument();
  });
});
