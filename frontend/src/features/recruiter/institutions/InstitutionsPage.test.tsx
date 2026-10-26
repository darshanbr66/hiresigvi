import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { InstitutionResponse } from "../../../types/institution";
import * as api from "./api";
import { InstitutionsPage } from "./InstitutionsPage";

const auth = vi.hoisted(() => ({ roles: ["RECRUITER"] as string[] }));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: auth.roles } }),
}));

const institution: InstitutionResponse = {
  id: "inst-1",
  name: "College of Engineering, Pune",
  institution_type: null,
  city: "Pune",
  state: "Maharashtra",
  country: "India",
  website: null,
  email_domains: [],
  placement_contact_name: null,
  placement_contact_email: null,
  placement_contact_phone: null,
  notes: null,
  is_active: true,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  campus_drive_count: 2,
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>
          <InstitutionsPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("InstitutionsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    auth.roles = ["RECRUITER"];
  });

  it("registers an institution without requiring an email domain", async () => {
    vi.spyOn(api, "listInstitutions").mockResolvedValue([institution]);
    const create = vi.spyOn(api, "createInstitution").mockResolvedValue({ ...institution, id: "inst-2", name: "IIT Madras" });

    renderPage();
    expect(await screen.findByText(institution.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ New institution" }));

    const dialog = await screen.findByRole("dialog", { name: "Register an institution" });
    fireEvent.change(within(dialog).getByLabelText(/Institution name/), { target: { value: "  IIT Madras " } });
    fireEvent.click(within(dialog).getByRole("button", { name: /Save|Register/ }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ name: "IIT Madras", email_domains: [], website: null }),
        "test-token",
      ),
    );
  });

  it("refuses to save without a name", async () => {
    vi.spyOn(api, "listInstitutions").mockResolvedValue([]);
    const create = vi.spyOn(api, "createInstitution");

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "+ New institution" }));
    const dialog = await screen.findByRole("dialog", { name: "Register an institution" });
    fireEvent.click(within(dialog).getByRole("button", { name: /Save|Register/ }));

    expect(within(dialog).getByLabelText(/Institution name/)).toBeRequired();
    expect(create).not.toHaveBeenCalled();
  });

  it("shows the registry read-only to a role that cannot manage campus drives", async () => {
    auth.roles = ["HIRING_MANAGER"];
    vi.spyOn(api, "listInstitutions").mockResolvedValue([institution]);

    renderPage();

    expect(await screen.findByText(institution.name)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ New institution" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
  });
});
