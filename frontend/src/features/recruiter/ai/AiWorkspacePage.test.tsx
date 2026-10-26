import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { JobResponse } from "../../../types/recruitment";
import type { CandidateResponse } from "../../../types/recruitment";
import type { InternalAIQueryResponse, MatchResponse } from "../../../types/internalAi";
import { AiWorkspacePage } from "./AiWorkspacePage";
import * as aiApi from "./api";
import * as candidatesApi from "../candidates/api";
import * as jobsApi from "../jobs/api";
import * as applicationsApi from "../applications/api";

let mockUser: { full_name: string; roles: string[] } | null = { full_name: "Test User", roles: ["RECRUITER"] };

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: mockUser }),
}));

const sampleJob: JobResponse = {
  id: "job-1",
  organization_id: "org-1",
  title: "Full Stack Developer",
  department: "Engineering",
  location: "Bengaluru",
  employment_type: "FULL_TIME",
  description: "React, Node.js, MongoDB required.",
  description_visible: true,
  status: "OPEN",
  openings_count: 1,
  created_by: "user-1",
  deleted_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const sampleCandidate: CandidateResponse = {
  id: "cand-1",
  organization_id: "org-1",
  email: "priya@example.com",
  full_name: "Priya Sharma",
  phone: null,
  location: "Bengaluru",
  current_title: "Full Stack Developer",
  years_experience: 5,
  candidate_type: null,
  current_company: null,
  preferred_location: null,
  notice_period_days: 30,
  immediate_joiner: false,
  qualification: null,
  linkedin_url: null,
  github_url: null,
  source: "PORTAL",
  is_active: true,
  deleted_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
} as CandidateResponse;

const sampleMatchResponse: MatchResponse = {
  id: "match-1",
  candidate_id: "cand-1",
  job_id: "job-1",
  application_id: "app-1",
  status: "COMPLETED",
  provider: "deterministic",
  model: "rule-based-v1",
  overall_match_score: 95,
  confidence: "HIGH",
  matching_skills: ["React", "Node.js", "MongoDB", "REST APIs"],
  missing_skills: [],
  matching_experience: { score: 1, detail: "5 years vs 4+ required." },
  matching_education: { score: 1, detail: "B.Tech matches." },
  matching_location: { score: 1, detail: "Bengaluru matches." },
  notice_period_fit: { score: 1, detail: "Within range." },
  role_alignment: "Strong alignment",
  potential_concerns: [],
  evidence: [{ requirement_label: "React", resume_chunk_id: "chunk-1", excerpt: "5 years of React.", similarity: 0.9 }],
  explanation: "Candidate meets the core technical requirements and experience requirement.",
  scoring_breakdown: { SKILL: { score: 1, weight: 0.45 } },
  error_message: null,
  created_at: new Date().toISOString(),
  disclaimer: "AI-generated assessment. Final hiring decision remains with the recruitment team.",
};

function renderWorkspace(initialPath = "/recruiter/ai") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <AiWorkspacePage />
        </ToastProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("AiWorkspacePage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockUser = { full_name: "Test User", roles: ["RECRUITER"] };
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([sampleJob]);
    vi.spyOn(candidatesApi, "listCandidates").mockResolvedValue([sampleCandidate]);
    vi.spyOn(candidatesApi, "getCandidate").mockResolvedValue(sampleCandidate);
    vi.spyOn(applicationsApi, "listApplicationsForJob").mockResolvedValue([
      {
        id: "app-1",
        organization_id: "org-1",
        candidate_id: "cand-1",
        candidate_full_name: "Priya Sharma",
        candidate_email: "priya@example.com",
        candidate_phone: null,
        job_id: "job-1",
        job_title: "Full Stack Developer",
        campus_drive_id: null,
        status: "APPLIED",
        source: "PORTAL",
        applied_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        resume_id: null,
        resume_filename: null,
        deleted_at: null,
      },
    ]);
    vi.spyOn(applicationsApi, "listApplicationsForCandidate").mockResolvedValue([]);
  });

  it("renders the workspace home with quick actions and the AI disclosure", async () => {
    renderWorkspace();
    expect(screen.getByRole("heading", { name: "AI Intelligence" })).toBeInTheDocument();
    expect(screen.getByText(/final hiring decisions are made by authorized personnel/i)).toBeInTheDocument();
    expect(screen.getByText("Find candidates for a job")).toBeInTheDocument();
    expect(screen.getByText("Analyze a candidate")).toBeInTheDocument();
    expect(screen.getByText("Compare candidates")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask AI about recruitment data" })).toBeInTheDocument();
    expect(screen.getByText("No AI analysis yet")).toBeInTheDocument();
  });

  it("shows a permission notice instead of the workspace for a role without internal_ai.use", async () => {
    mockUser = { full_name: "Ivy Interviewer", roles: ["INTERVIEWER"] };
    renderWorkspace();
    expect(await screen.findByText(/you do not have permission to use the internal ai/i)).toBeInTheDocument();
    expect(screen.queryByText("Find candidates for a job")).not.toBeInTheDocument();
  });

  it("submits a natural-language query and renders the assistant's structured response", async () => {
    const response: InternalAIQueryResponse = {
      conversation_id: "conv-1",
      message: "Priya Sharma scores 95% against Full Stack Developer.",
      candidates: [],
      jobs: [],
      matches: [
        {
          match_id: "match-1",
          candidate_id: "cand-1",
          candidate_name: "Priya Sharma",
          job_id: "job-1",
          job_title: "Full Stack Developer",
          status: "COMPLETED",
          overall_match_score: 95,
          confidence: "HIGH",
          matching_skills: ["React"],
          missing_skills: [],
          role_alignment: "Strong alignment",
          explanation: "Strong match.",
          potential_concerns: [],
          evidence: [],
          disclaimer: "AI-generated assessment. Final hiring decision remains with the recruitment team.",
        },
      ],
      pipeline_stats: null,
      disclaimer: "AI-generated assessment. Final hiring decision remains with the recruitment team.",
    };
    const querySpy = vi.spyOn(aiApi, "queryInternalAi").mockResolvedValue(response);

    renderWorkspace();
    const textarea = screen.getByPlaceholderText(/ask about a candidate/i);
    fireEvent.change(textarea, { target: { value: "Is this candidate suitable for this role?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    // Optimistic user turn appears immediately, then the "thinking" state.
    expect(screen.getByText("Is this candidate suitable for this role?")).toBeInTheDocument();

    await waitFor(() => expect(querySpy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(response.message)).toBeInTheDocument();
    expect(await screen.findByText("95%")).toBeInTheDocument();
  });

  it("renders a pipeline summary's stats as structured UI instead of raw JSON", async () => {
    const response: InternalAIQueryResponse = {
      conversation_id: "conv-2",
      message: "Your organization currently has 2 application(s) across 1 open job(s).",
      candidates: [],
      jobs: [],
      matches: [],
      pipeline_stats: {
        applications_by_status: { APPLIED: 1, SCREENING: 1 },
        open_jobs: 1,
        top_jobs_by_applications: [{ title: "Data Analyst", application_count: 2 }],
      },
      disclaimer: "AI-generated assessment. Final hiring decision remains with the recruitment team.",
    };
    vi.spyOn(aiApi, "queryInternalAi").mockResolvedValue(response);

    renderWorkspace();
    fireEvent.change(screen.getByPlaceholderText(/ask about a candidate/i), {
      target: { value: "Give me a summary of the current recruitment pipeline." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText(response.message)).toBeInTheDocument();
    const panel = await screen.findByLabelText("Pipeline summary");
    expect(panel).toHaveTextContent("Applied");
    expect(panel).toHaveTextContent("Screening");
    expect(panel).toHaveTextContent("Data Analyst");
    expect(panel).toHaveTextContent("2 applications");
    expect(panel.textContent).not.toContain('{"APPLIED"');
    expect(panel.textContent).not.toContain("application_count");
  });

  it("shows a friendly error and keeps the conversation usable when the query fails", async () => {
    vi.spyOn(aiApi, "queryInternalAi").mockRejectedValue(new ApiError("boom", 503, "service_unavailable"));

    renderWorkspace();
    fireEvent.change(screen.getByPlaceholderText(/ask about a candidate/i), {
      target: { value: "How many candidates are shortlisted?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    const matches = await screen.findAllByText(/temporarily unavailable/i);
    expect(matches.length).toBeGreaterThan(0);
    expect(screen.queryByText("boom")).not.toBeInTheDocument();
  });

  it("runs job-wide matching for ?jobId= and ranks the results", async () => {
    vi.spyOn(aiApi, "computeJobMatches").mockResolvedValue([sampleMatchResponse]);

    renderWorkspace("/recruiter/ai?jobId=job-1");

    expect(await screen.findByText(/candidates for/i)).toBeInTheDocument();
    expect(await screen.findByText("Priya Sharma")).toBeInTheDocument();
    expect(await screen.findByText("95%")).toBeInTheDocument();
  });

  it("shows an empty state when a job has no applicants", async () => {
    vi.spyOn(aiApi, "computeJobMatches").mockResolvedValue([]);
    renderWorkspace("/recruiter/ai?jobId=job-1");
    expect(await screen.findByText("No applicants yet")).toBeInTheDocument();
  });

  it("runs candidate analysis for ?candidateId= against the candidate's applied role", async () => {
    vi.spyOn(applicationsApi, "listApplicationsForCandidate").mockResolvedValue([
      {
        id: "app-1",
        organization_id: "org-1",
        candidate_id: "cand-1",
        candidate_full_name: "Priya Sharma",
        candidate_email: "priya@example.com",
        candidate_phone: null,
        job_id: "job-1",
        job_title: "Full Stack Developer",
        campus_drive_id: null,
        status: "APPLIED",
        source: "PORTAL",
        applied_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        resume_id: null,
        resume_filename: null,
        deleted_at: null,
      },
    ]);
    vi.spyOn(aiApi, "computeMatch").mockResolvedValue(sampleMatchResponse);

    renderWorkspace("/recruiter/ai?candidateId=cand-1");

    expect(await screen.findByText("Analysis for Priya Sharma")).toBeInTheDocument();
    expect(await screen.findByText("95%")).toBeInTheDocument();
    // The role selector defaults to the candidate's applied job.
    expect(screen.getByRole("combobox", { name: /match against role/i })).toHaveValue("job-1");
  });

  it("lets the recruiter select two candidates from a job's ranking and compares them", async () => {
    const secondMatch: MatchResponse = {
      ...sampleMatchResponse,
      candidate_id: "cand-2",
      overall_match_score: 50,
      role_alignment: "Limited alignment",
      matching_skills: ["React"],
      missing_skills: ["MongoDB"],
    };
    vi.spyOn(aiApi, "computeJobMatches").mockResolvedValue([sampleMatchResponse, secondMatch]);
    vi.spyOn(applicationsApi, "listApplicationsForJob").mockResolvedValue([
      {
        id: "app-1",
        organization_id: "org-1",
        candidate_id: "cand-1",
        candidate_full_name: "Priya Sharma",
        candidate_email: "priya@example.com",
        candidate_phone: null,
        job_id: "job-1",
        job_title: "Full Stack Developer",
        campus_drive_id: null,
        status: "APPLIED",
        source: "PORTAL",
        applied_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        resume_id: null,
        resume_filename: null,
        deleted_at: null,
      },
      {
        id: "app-2",
        organization_id: "org-1",
        candidate_id: "cand-2",
        candidate_full_name: "Rahul Verma",
        candidate_email: "rahul@example.com",
        candidate_phone: null,
        job_id: "job-1",
        job_title: "Full Stack Developer",
        campus_drive_id: null,
        status: "APPLIED",
        source: "PORTAL",
        applied_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        resume_id: null,
        resume_filename: null,
        deleted_at: null,
      },
    ]);

    renderWorkspace("/recruiter/ai?jobId=job-1");

    await screen.findByText("Priya Sharma");
    await screen.findByText("Rahul Verma");

    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);

    expect(await screen.findByRole("heading", { name: "Comparison" })).toBeInTheDocument();
    const comparisonTable = screen.getByRole("heading", { name: "Comparison" }).closest("section")!;
    expect(within(comparisonTable).getByText("Priya Sharma")).toBeInTheDocument();
    expect(within(comparisonTable).getByText("Rahul Verma")).toBeInTheDocument();
  });

  it("opens the job picker from the 'Find candidates for a job' quick action and navigates on selection", async () => {
    renderWorkspace();
    fireEvent.click(screen.getByText("Find candidates for a job"));

    const dialog = await screen.findByRole("dialog", { name: "Find candidates for a job" });
    fireEvent.click(await within(dialog).findByText("Full Stack Developer"));

    expect(await screen.findByText(/candidates for “full stack developer”/i)).toBeInTheDocument();
  });
});
