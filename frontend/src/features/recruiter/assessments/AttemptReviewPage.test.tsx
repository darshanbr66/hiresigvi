import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { AttemptAnswerView, AttemptReviewResponse } from "../../../types/assessment";
import * as api from "./api";
import { AttemptReviewPage } from "./AttemptReviewPage";

const auth = vi.hoisted(() => ({ roles: ["RECRUITER"] as string[] }));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: auth.roles } }),
}));

const descriptive: AttemptAnswerView = {
  question_id: "q-desc",
  order: 0,
  prompt: "Explain inverse kinematics.",
  type: "DESCRIPTIVE",
  points: 5,
  negative_points: 0,
  options: [],
  accepted_answers: [],
  grading_notes: "Look for joint-space reasoning.",
  explanation: null,
  answer_id: "ans-1",
  selected_option_ids: [],
  text_answer: "Solving joint angles from an end-effector pose.",
  is_correct: null,
  awarded_points: null,
  grading_method: null,
  needs_manual_grading: true,
  manually_gradable: true,
  graded_by_name: null,
  graded_at: null,
  grader_comment: null,
};

const review: AttemptReviewResponse = {
  invitation_id: "inv-1",
  application_id: "app-1",
  candidate_full_name: "Asha Rao",
  assessment_title: "Robotics test",
  attempt_number: 1,
  status: "SUBMITTED",
  submitted_at: new Date().toISOString(),
  result: {
    score: 0,
    max_score: 5,
    percentage: 0,
    passed: null,
    grading_status: "PENDING_MANUAL_REVIEW",
    evaluated_at: new Date().toISOString(),
  },
  answers: [descriptive],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/recruiter/assessments/attempts/inv-1"]}>
          <Routes>
            <Route path="/recruiter/assessments/attempts/:invitationId" element={<AttemptReviewPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AttemptReviewPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    auth.roles = ["RECRUITER"];
    vi.spyOn(api, "getAttemptReview").mockResolvedValue(review);
  });

  it("holds pass/fail until descriptive answers are graded", async () => {
    renderPage();

    expect(await screen.findByText("1 answer(s) awaiting grading")).toBeInTheDocument();
    expect(screen.queryByText("PASSED")).not.toBeInTheDocument();
    expect(screen.queryByText("FAILED")).not.toBeInTheDocument();
  });

  it("rejects marks above the question's maximum without calling the server", async () => {
    const grade = vi.spyOn(api, "gradeAttempt");

    renderPage();
    fireEvent.change(await screen.findByLabelText("Award marks"), { target: { value: "6" } });
    fireEvent.submit(screen.getByRole("button", { name: "Save marks" }).closest("form")!);

    expect(await screen.findByText("Question 1: marks must be a whole number from 0 to 5.")).toBeInTheDocument();
    expect(grade).not.toHaveBeenCalled();
  });

  it("submits valid marks with the grader's comment", async () => {
    const grade = vi.spyOn(api, "gradeAttempt").mockResolvedValue({
      ...review,
      result: { ...review.result!, score: 4, percentage: 80, passed: true, grading_status: "FINAL" },
      answers: [{ ...descriptive, awarded_points: 4, needs_manual_grading: false, grading_method: "MANUAL" }],
    });

    renderPage();
    fireEvent.change(await screen.findByLabelText("Award marks"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText(/Comment/), { target: { value: "Good reasoning" } });
    fireEvent.click(screen.getByRole("button", { name: "Save marks" }));

    await waitFor(() =>
      expect(grade).toHaveBeenCalledWith(
        "inv-1",
        [{ question_id: "q-desc", awarded_points: 4, comment: "Good reasoning" }],
        "test-token",
      ),
    );
    expect(await screen.findByText("PASSED")).toBeInTheDocument();
  });

  it("shows no grading controls to a role that cannot grade", async () => {
    auth.roles = ["INTERVIEWER"];

    renderPage();

    expect(await screen.findByText("Explain inverse kinematics.", { exact: false })).toBeInTheDocument();
    expect(screen.queryByLabelText("Award marks")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save marks" })).not.toBeInTheDocument();
  });
});
