import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { AssessmentResponse } from "../../../types/assessment";
import { AssessmentDetailPage } from "./AssessmentDetailPage";
import * as assessmentsApi from "./api";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token" }),
}));

const baseAssessment: AssessmentResponse = {
  id: "assess-1",
  title: "Python Basics",
  instructions: "Answer everything.",
  duration_minutes: 30,
  pass_score: 60,
  status: "PUBLISHED",
  deleted_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  has_invitations: false,
  questions: [
    {
      id: "q1",
      prompt: "What is 2 + 2?",
      type: "MCQ_SINGLE",
      points: 1,
      negative_points: 0,
      options: [
        { id: "o1", label: "3", is_correct: false },
        { id: "o2", label: "4", is_correct: true },
      ],
      accepted_answers: [],
      case_sensitive: false,
      grading_notes: null,
      explanation: null,
      source_bank_version_id: null,
    },
  ],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/recruiter/assessments/assess-1"]}>
          <Routes>
            <Route path="/recruiter/assessments/:assessmentId" element={<AssessmentDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AssessmentDetailPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the assessment's questions and an Edit action", async () => {
    vi.spyOn(assessmentsApi, "getAssessment").mockResolvedValue(baseAssessment);

    renderPage();

    expect(await screen.findByRole("heading", { name: "Python Basics" })).toBeInTheDocument();
    expect(screen.getByText(/What is 2 \+ 2\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("locks question editing once the assessment has invitations, but keeps other fields editable", async () => {
    const inUse = { ...baseAssessment, has_invitations: true };
    vi.spyOn(assessmentsApi, "getAssessment").mockResolvedValue(inUse);

    renderPage();
    await screen.findByRole("heading", { name: "Python Basics" });
    expect(screen.getByText("In use — questions locked")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const titleInput = screen.getByDisplayValue("Python Basics");
    expect(titleInput).not.toBeDisabled();

    const promptInput = screen.getByDisplayValue("What is 2 + 2?");
    expect(promptInput).toBeDisabled();
    expect(screen.getByText(/can't be changed once this assessment has been sent/i)).toBeInTheDocument();
  });

  it("saves non-question edits without sending a questions field when locked", async () => {
    const inUse = { ...baseAssessment, has_invitations: true };
    vi.spyOn(assessmentsApi, "getAssessment").mockResolvedValue(inUse);
    const updateSpy = vi.spyOn(assessmentsApi, "updateAssessment").mockResolvedValue({
      ...inUse,
      duration_minutes: 45,
    });

    renderPage();
    await screen.findByRole("heading", { name: "Python Basics" });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const durationInput = screen.getByDisplayValue("30");
    fireEvent.change(durationInput, { target: { value: "45" } });

    const form = document.querySelector("form")!;
    fireEvent.submit(form);

    await screen.findByRole("button", { name: "Edit" });
    expect(updateSpy).toHaveBeenCalledWith(
      "assess-1",
      expect.not.objectContaining({ questions: expect.anything() }),
      "test-token",
    );
  });
});
