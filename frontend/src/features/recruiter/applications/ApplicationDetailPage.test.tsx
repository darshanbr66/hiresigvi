import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { AssessmentInvitationResponse } from "../../../types/assessment";
import type { ApplicationResponse } from "../../../types/recruitment";
import * as assessmentsApi from "../assessments/api";
import * as notesApi from "../notes/api";
import * as screeningApi from "../screening/api";
import * as api from "./api";
import { ApplicationDetailPage } from "./ApplicationDetailPage";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: ["ORG_ADMIN"] } }),
}));

const application: ApplicationResponse = {
  id: "app-1",
  organization_id: "org-1",
  candidate_id: "cand-1",
  candidate_full_name: "Jane Candidate",
  candidate_email: "jane@example.com",
  candidate_phone: null,
  job_id: "job-1",
  job_title: "Backend Engineer",
  campus_drive_id: null,
  status: "ASSESSMENT_COMPLETED",
  source: "PORTAL",
  applied_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  resume_id: null,
  resume_filename: null,
  deleted_at: null,
};

const submittedInvitation: AssessmentInvitationResponse = {
  id: "inv-1",
  assessment_id: "assess-1",
  assessment_title: "Python Basics",
  application_id: "app-1",
  candidate_full_name: "Jane Candidate",
  status: "SUBMITTED",
  expires_at: new Date().toISOString(),
  started_at: new Date().toISOString(),
  submitted_at: new Date().toISOString(),
  emailed_at: null,
  attempt_number: 1,
  retest_reason: null,
  result: {
    score: 3,
    max_score: 3,
    percentage: 100,
    passed: true,
    grading_status: "FINAL",
    evaluated_at: new Date().toISOString(),
  },
  invitation_link: null,
};

const retestInvitation: AssessmentInvitationResponse = {
  ...submittedInvitation,
  id: "inv-2",
  status: "SENT",
  attempt_number: 2,
  retest_reason: "Network interruption",
  result: null,
  invitation_link: "https://app.example.test/assessment/retest-token",
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/recruiter/applications/app-1"]}>
          <Routes>
            <Route path="/recruiter/applications/:applicationId" element={<ApplicationDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

async function openRetestModal() {
  fireEvent.click(await screen.findByRole("button", { name: "Give Retest" }));
  await screen.findByRole("dialog");
  fireEvent.change(screen.getByLabelText("Reason for retest"), {
    target: { value: "Network interruption" },
  });
}

/** Resolves after a real delay, like a network call. It matters: the page
 * re-renders (button -> "Sending…") while the request is in flight, and
 * TanStack Query hands the in-flight mutation that latest render's callbacks —
 * so `onSuccess` runs with `isPending` still true. A mock that resolves
 * instantly finishes before that re-render and hides the bug this guards. */
function afterNetworkDelay<T>(value: T): () => Promise<T> {
  return () => new Promise<T>((resolve) => setTimeout(() => resolve(value), 30));
}

describe("ApplicationDetailPage – Send Retest", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getApplication").mockResolvedValue(application);
    vi.spyOn(screeningApi, "listScreeningRuns").mockResolvedValue([]);
    vi.spyOn(notesApi, "listNotes").mockResolvedValue([]);
    vi.spyOn(assessmentsApi, "listAssessments").mockResolvedValue([]);
    vi.spyOn(assessmentsApi, "listApplicationAssessmentAttempts").mockResolvedValue([]);
    vi.spyOn(assessmentsApi, "listApplicationAssessmentEvents").mockResolvedValue([]);
    vi.spyOn(assessmentsApi, "getApplicationAssessment").mockResolvedValue(submittedInvitation);
  });

  it("closes the modal, shows the success toast and refreshes the data when the retest succeeds", async () => {
    const retest = vi
      .spyOn(assessmentsApi, "retestCandidate")
      .mockImplementation(afterNetworkDelay(retestInvitation));
    renderPage();
    await openRetestModal();
    const loadsBefore = vi.mocked(assessmentsApi.getApplicationAssessment).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Send Retest" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("Retest created — the new assessment link is ready.")).toBeInTheDocument();
    // The new invitation link is now visible on the page, no longer hidden behind the modal.
    expect(screen.getByText("https://app.example.test/assessment/retest-token")).toBeInTheDocument();
    // Exactly one request, with what the recruiter entered.
    expect(retest).toHaveBeenCalledTimes(1);
    expect(retest).toHaveBeenCalledWith(
      expect.objectContaining({
        application_id: "app-1",
        reason: "Network interruption",
        assessment_choice: "SAME",
      }),
      "test-token",
    );
    // The assessment data is refetched.
    await waitFor(() =>
      expect(vi.mocked(assessmentsApi.getApplicationAssessment).mock.calls.length).toBeGreaterThan(
        loadsBefore,
      ),
    );
  });

  it("keeps the modal open and shows the error when the retest fails, and a retry can then succeed", async () => {
    const retest = vi
      .spyOn(assessmentsApi, "retestCandidate")
      .mockRejectedValueOnce(new ApiError("The current attempt is not submitted yet.", 409, "conflict"))
      .mockImplementationOnce(afterNetworkDelay(retestInvitation));
    renderPage();
    await openRetestModal();

    fireEvent.click(screen.getByRole("button", { name: "Send Retest" }));

    expect(await screen.findByText("The current attempt is not submitted yet.")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText("Retest created — the new assessment link is ready.")).not.toBeInTheDocument();
    // What the recruiter typed is kept, and Send is usable again.
    expect(screen.getByLabelText("Reason for retest")).toHaveValue("Network interruption");
    const send = screen.getByRole("button", { name: "Send Retest" });
    expect(send).toBeEnabled();

    fireEvent.click(send);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(retest).toHaveBeenCalledTimes(2);
  });

  it("cannot be closed with Cancel while the request is in flight, and sends only once", async () => {
    let finish: (value: AssessmentInvitationResponse) => void = () => {};
    const retest = vi.spyOn(assessmentsApi, "retestCandidate").mockReturnValue(
      new Promise<AssessmentInvitationResponse>((resolve) => {
        finish = resolve;
      }),
    );
    renderPage();
    await openRetestModal();

    fireEvent.submit(screen.getByRole("button", { name: "Send Retest" }).closest("form") as HTMLFormElement);
    await waitFor(() => expect(retest).toHaveBeenCalledTimes(1));
    // A second submit while pending is ignored.
    fireEvent.submit(screen.getByRole("dialog").querySelector("form") as HTMLFormElement);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(retest).toHaveBeenCalledTimes(1);

    finish(retestInvitation);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("still closes without sending when the recruiter cancels", async () => {
    const retest = vi.spyOn(assessmentsApi, "retestCandidate").mockResolvedValue(retestInvitation);
    renderPage();
    await openRetestModal();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(retest).not.toHaveBeenCalled();
  });
});
