import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { PublicInvitationView, PublicSubmissionResult } from "../../types/assessment";
import { ThemeProvider } from "../theme/ThemeContext";
import { AssessmentTakingPage } from "./AssessmentTakingPage";
import * as careersApi from "./api";

const sentInvitation: PublicInvitationView = {
  status: "SENT",
  assessment_title: "Python Basics",
  instructions: "Answer everything.",
  duration_minutes: 30,
  job_title: "Backend Engineer",
  organization_name: "Zodiac Patent",
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  started_at: null,
  questions: [
    {
      id: "q1",
      prompt: "2 + 2?",
      type: "MCQ_SINGLE",
      points: 1,
      options: [
        { id: "o1", label: "3" },
        { id: "o2", label: "4" },
      ],
    },
  ],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/assessment/tok123"]}>
          <Routes>
            <Route path="/assessment/:token" element={<AssessmentTakingPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("AssessmentTakingPage monitoring consent", () => {
  it("requires the consent checkbox before Start Assessment is enabled", async () => {
    vi.spyOn(careersApi, "getAssessmentInvitation").mockResolvedValue(sentInvitation);

    renderPage();

    expect(await screen.findByRole("heading", { name: "Before You Begin" })).toBeInTheDocument();
    const startButton = screen.getByRole("button", { name: "Start Assessment" });
    expect(startButton).toBeDisabled();

    fireEvent.click(
      screen.getByLabelText("I understand and agree to browser-based assessment monitoring."),
    );
    expect(startButton).not.toBeDisabled();
  });

  it("starts the assessment and sends a consent event once accepted", async () => {
    const startedInvitation: PublicInvitationView = {
      ...sentInvitation,
      status: "STARTED",
      started_at: new Date().toISOString(),
    };
    vi.spyOn(careersApi, "getAssessmentInvitation").mockImplementation(() =>
      Promise.resolve(startSpy.mock.calls.length > 0 ? startedInvitation : sentInvitation),
    );
    const startSpy = vi.spyOn(careersApi, "startAssessment").mockResolvedValue(startedInvitation);
    const eventsSpy = vi.spyOn(careersApi, "sendMonitoringEvents").mockResolvedValue(undefined);

    renderPage();
    await screen.findByRole("heading", { name: "Before You Begin" });
    fireEvent.click(
      screen.getByLabelText("I understand and agree to browser-based assessment monitoring."),
    );
    fireEvent.click(screen.getByRole("button", { name: "Start Assessment" }));

    await screen.findByText(/2 \+ 2\?/);
    expect(startSpy).toHaveBeenCalledWith("tok123");
    expect(eventsSpy).toHaveBeenCalled();
    const [, events] = eventsSpy.mock.calls[0];
    expect(events.some((e) => e.event_type === "MONITORING_CONSENT_GIVEN")).toBe(true);
  });

  it("shows a polished success screen with no score after submission", async () => {
    const started: PublicInvitationView = {
      ...sentInvitation,
      status: "STARTED",
      started_at: new Date().toISOString(),
    };
    vi.spyOn(careersApi, "getAssessmentInvitation").mockResolvedValue(started);
    const submissionResult: PublicSubmissionResult = { submitted_at: new Date().toISOString() };
    vi.spyOn(careersApi, "submitAssessment").mockResolvedValue(submissionResult);

    renderPage();
    await screen.findByText(/2 \+ 2\?/);
    fireEvent.click(screen.getByLabelText("4"));
    fireEvent.click(screen.getByRole("button", { name: "Submit assessment" }));

    expect(await screen.findByRole("heading", { name: "Assessment Submitted" })).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });
});

describe("AssessmentTakingPage focused experience", () => {
  const twoQuestions: PublicInvitationView = {
    ...sentInvitation,
    status: "STARTED",
    started_at: new Date().toISOString(),
    questions: [
      ...sentInvitation.questions,
      {
        id: "q2",
        prompt: "3 + 3?",
        type: "MCQ_SINGLE",
        points: 1,
        options: [
          { id: "p1", label: "6" },
          { id: "p2", label: "7" },
        ],
      },
    ],
  };

  it("shows how many questions are answered, updating as the candidate answers", async () => {
    vi.spyOn(careersApi, "getAssessmentInvitation").mockResolvedValue(twoQuestions);

    renderPage();
    const progress = await screen.findByRole("progressbar", { name: "Questions answered" });
    expect(progress).toHaveAttribute("aria-valuemax", "2");
    expect(progress).toHaveAttribute("aria-valuenow", "0");
    expect(progress).toHaveAttribute("aria-valuetext", "0 of 2 answered");

    fireEvent.click(screen.getByLabelText("4"));
    expect(progress).toHaveAttribute("aria-valuenow", "1");
    fireEvent.click(screen.getByLabelText("6"));
    expect(progress).toHaveAttribute("aria-valuetext", "2 of 2 answered");

    // changing an answer is not answering again
    fireEvent.click(screen.getByLabelText("3"));
    expect(progress).toHaveAttribute("aria-valuenow", "2");
  });

  it("states the allotted time and question count, but never invents a countdown that isn't enforced", async () => {
    vi.spyOn(careersApi, "getAssessmentInvitation").mockResolvedValue(twoQuestions);

    renderPage();
    await screen.findByRole("progressbar");

    expect(screen.getByText("30 minutes")).toBeInTheDocument();
    expect(screen.getByText("2 questions")).toBeInTheDocument();
    expect(screen.queryByRole("timer")).not.toBeInTheDocument();
    expect(screen.queryByText(/remaining|time left/i)).not.toBeInTheDocument();
  });

  it("shows no progress or timer before the assessment has started", async () => {
    vi.spyOn(careersApi, "getAssessmentInvitation").mockResolvedValue(sentInvitation);

    renderPage();
    await screen.findByRole("heading", { name: "Before You Begin" });

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("presents each answer as a selectable tile that stays a real radio input", async () => {
    vi.spyOn(careersApi, "getAssessmentInvitation").mockResolvedValue(twoQuestions);

    renderPage();
    await screen.findByRole("progressbar");

    const four = screen.getByLabelText("4");
    expect(four).toHaveAttribute("type", "radio");
    expect(four.closest("label")).toHaveClass("choice");
  });
});
