import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import type { ReapplySearchResult } from "../../../types/recruitment";
import * as candidatesApi from "./api";
import { ReapplyPermissionPanel } from "./ReapplyPermissionPanel";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: ["RECRUITER"] } }),
}));

const blocked: ReapplySearchResult = {
  id: "cand-1",
  full_name: "Maya Rao",
  email: "maya@example.com",
  phone: "+919876543210",
  last_self_applied_at: "2026-09-01T10:00:00Z",
  eligible_from: "2026-12-01T10:00:00Z",
  can_self_apply_now: false,
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReapplyPermissionPanel />
    </QueryClientProvider>,
  );
}

function search(term: string) {
  fireEvent.change(screen.getByLabelText("Search candidate by name, email or mobile"), {
    target: { value: term },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
}

describe("ReapplyPermissionPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["email", "maya@example.com"],
    ["name", "Maya Rao"],
    ["mobile", "98765 43210"],
  ])("finds a candidate by %s and offers the grant", async (_by, term) => {
    const searchApi = vi
      .spyOn(candidatesApi, "searchCandidatesForReapply")
      .mockResolvedValue([blocked]);
    renderPanel();
    search(term);

    const results = await screen.findByRole("list", { name: "Matching candidates" });
    expect(searchApi).toHaveBeenCalledWith(term, "test-token");
    expect(within(results).getByText("Maya Rao")).toBeInTheDocument();
    expect(within(results).getByText("maya@example.com")).toBeInTheDocument();
    expect(within(results).getByText("+919876543210")).toBeInTheDocument();
    expect(
      within(results).getByRole("button", { name: "Grant Reapply Permission" }),
    ).toBeInTheDocument();
  });

  it("says so when no candidate matches", async () => {
    vi.spyOn(candidatesApi, "searchCandidatesForReapply").mockResolvedValue([]);
    renderPanel();
    search("nobody@example.com");

    expect(await screen.findByText("Candidate not found.")).toBeInTheDocument();
  });

  it("doesn't offer the grant to a candidate who can already apply", async () => {
    vi.spyOn(candidatesApi, "searchCandidatesForReapply").mockResolvedValue([
      { ...blocked, can_self_apply_now: true, eligible_from: null },
    ]);
    renderPanel();
    search("maya");

    expect(await screen.findByText(/Can already apply/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Grant Reapply Permission" })).not.toBeInTheDocument();
  });

  it("asks for confirmation, and Cancel changes nothing", async () => {
    vi.spyOn(candidatesApi, "searchCandidatesForReapply").mockResolvedValue([blocked]);
    const grant = vi.spyOn(candidatesApi, "grantReapplyPermission");
    renderPanel();
    search("maya");
    fireEvent.click(await screen.findByRole("button", { name: "Grant Reapply Permission" }));

    const dialog = await screen.findByRole("dialog", { name: "Grant reapply permission?" });
    expect(
      within(dialog).getByText(/will remove the candidate's previous application data/),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(grant).not.toHaveBeenCalled();
  });

  it("grants on confirmation and confirms the candidate can apply again", async () => {
    vi.spyOn(candidatesApi, "searchCandidatesForReapply").mockResolvedValue([blocked]);
    const grant = vi.spyOn(candidatesApi, "grantReapplyPermission").mockResolvedValue({
      candidate_id: "cand-1",
      full_name: "Maya Rao",
      email: "maya@example.com",
      cleared_application_count: 1,
    });
    renderPanel();
    search("maya");
    fireEvent.click(await screen.findByRole("button", { name: "Grant Reapply Permission" }));
    const dialog = await screen.findByRole("dialog", { name: "Grant reapply permission?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Grant Reapply Permission" }));

    const done = await screen.findByRole("dialog", { name: "Reapply permission granted" });
    expect(grant).toHaveBeenCalledWith("cand-1", "test-token");
    expect(within(done).getByText(/Previous application data for/)).toBeInTheDocument();
    expect(
      within(done).getByText(/can now apply again using the same email and mobile number/),
    ).toBeInTheDocument();
  });

  it("shows the server's message when the grant is refused", async () => {
    vi.spyOn(candidatesApi, "searchCandidatesForReapply").mockResolvedValue([blocked]);
    vi.spyOn(candidatesApi, "grantReapplyPermission").mockRejectedValue(
      new ApiError(
        "This candidate can already apply again — there is no reapply restriction to lift.",
        409,
        "reapply_not_restricted",
      ),
    );
    renderPanel();
    search("maya");
    fireEvent.click(await screen.findByRole("button", { name: "Grant Reapply Permission" }));
    const dialog = await screen.findByRole("dialog", { name: "Grant reapply permission?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Grant Reapply Permission" }));

    expect(await within(dialog).findByText(/can already apply again/)).toBeInTheDocument();
  });

  it("asks for a longer search term rather than searching on one character", () => {
    const searchApi = vi.spyOn(candidatesApi, "searchCandidatesForReapply");
    renderPanel();
    search("m");

    expect(screen.getByText("Enter at least 2 characters to search.")).toBeInTheDocument();
    expect(searchApi).not.toHaveBeenCalled();
  });
});
