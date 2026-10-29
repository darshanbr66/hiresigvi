import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { InterviewResponse } from "../../../types/program";
import type { ApplicationStatus } from "../../../types/recruitment";
import * as authApi from "../../auth/api";
import * as api from "./api";
import { ApplicationInterviews } from "./ApplicationInterviews";

const scheduled: InterviewResponse = {
  id: "iv-1",
  application_id: "app-1",
  stage: "Technical",
  scheduled_at: "2027-01-10T10:00:00Z",
  duration_minutes: null,
  location: null,
  interviewer_user_id: "user-7",
  interviewer_name: "Ravi",
  status: "SCHEDULED",
  outcome: null,
  rating: null,
  feedback: null,
  feedback_restricted: false,
  cancellation_reason: null,
  recorded_at: null,
  created_at: "2027-01-01T00:00:00Z",
};

function renderCard(status: ApplicationStatus = "SHORTLISTED", userId = "user-1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ApplicationInterviews applicationId="app-1" applicationStatus={status} token="t" userId={userId} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("ApplicationInterviews", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authApi, "listUsers").mockResolvedValue([]);
  });

  it("lets an interview manager schedule a shortlisted candidate, sending a UTC instant", async () => {
    vi.spyOn(api, "listInterviews").mockResolvedValue({ items: [], viewer_permissions: ["interview.manage", "interview.read"] });
    const schedule = vi.spyOn(api, "scheduleInterview").mockResolvedValue(scheduled);

    renderCard();

    expect(await screen.findByText("No interviews scheduled yet.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Stage"), { target: { value: "Technical" } });
    fireEvent.change(screen.getByLabelText("Date and time"), { target: { value: "2027-01-10T10:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Schedule" }));
    await waitFor(() => expect(schedule).toHaveBeenCalled());
    const payload = schedule.mock.calls[0][1];
    expect(payload.stage).toBe("Technical");
    expect(payload.scheduled_at).toMatch(/Z$/);
  });

  it("doesn't offer scheduling before the shortlist", async () => {
    vi.spyOn(api, "listInterviews").mockResolvedValue({ items: [], viewer_permissions: ["interview.manage"] });

    renderCard("UNDER_REVIEW");

    expect(await screen.findByText("Interviews can be scheduled once the candidate is shortlisted.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Schedule" })).not.toBeInTheDocument();
  });

  it("gives an interviewer the outcome form only on their own interview", async () => {
    vi.spyOn(api, "listInterviews").mockResolvedValue({ items: [scheduled], viewer_permissions: ["interview.read"] });

    const { unmount } = renderCard("INTERVIEW", "someone-else");
    expect(await screen.findByText("Technical")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record outcome" })).not.toBeInTheDocument();
    unmount();

    const record = vi.spyOn(api, "recordInterviewOutcome").mockResolvedValue({ ...scheduled, status: "COMPLETED", outcome: "FAIL" });
    renderCard("INTERVIEW", "user-7");
    const button = await screen.findByRole("button", { name: "Record outcome" });
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "FAIL" } });
    expect(button).toBeDisabled(); // a non-pass needs feedback
    fireEvent.change(screen.getByLabelText(/Feedback/), { target: { value: "Weak fundamentals." } });
    fireEvent.click(button);
    await waitFor(() =>
      expect(record).toHaveBeenCalledWith("app-1", "iv-1", { outcome: "FAIL", feedback: "Weak fundamentals.", rating: null }, "t"),
    );
    expect(screen.queryByRole("button", { name: "Cancel interview" })).not.toBeInTheDocument();
  });

  it("renders nothing for a viewer without interview permission", async () => {
    vi.spyOn(api, "listInterviews").mockRejectedValue(
      Object.assign(Object.create(ApiError.prototype), { status: 403, message: "Forbidden" }),
    );

    const { container } = renderCard();

    await waitFor(() => expect(container.querySelector("section")).toBeNull());
  });
});
