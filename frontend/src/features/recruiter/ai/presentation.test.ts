import { describe, expect, it } from "vitest";
import type { MatchCard, MatchResponse } from "../../../types/internalAi";
import {
  alignmentToneClass,
  formatConfidence,
  formatScore,
  matchStatusMessage,
  normalizeMatchCard,
  normalizeMatchResponse,
  normalizePipelineStats,
} from "./presentation";

describe("alignmentToneClass", () => {
  it("maps each backend alignment label to its badge tone", () => {
    expect(alignmentToneClass("Strong alignment")).toBe("badge-active");
    expect(alignmentToneClass("Moderate alignment")).toBe("badge-warn");
    expect(alignmentToneClass("Limited alignment")).toBe("badge-danger");
    expect(alignmentToneClass(null)).toBe("badge-inactive");
    expect(alignmentToneClass("something unexpected")).toBe("badge-inactive");
  });
});

describe("formatScore", () => {
  it("formats a numeric score as a percentage, or an em dash when unknown", () => {
    expect(formatScore(95)).toBe("95%");
    expect(formatScore(0)).toBe("0%");
    expect(formatScore(null)).toBe("—");
  });
});

describe("formatConfidence", () => {
  it("never calls the score itself 'AI confidence' — confidence is a separate data-quality line", () => {
    expect(formatConfidence("HIGH")).toBe("High data confidence");
    expect(formatConfidence("MEDIUM")).toBe("Medium data confidence");
    expect(formatConfidence(null)).toBe("Unknown data confidence");
  });
});

describe("matchStatusMessage", () => {
  it("returns null for a completed match (nothing to warn about)", () => {
    expect(matchStatusMessage({ status: "COMPLETED", error_message: null })).toBeNull();
  });

  it("never surfaces the raw backend error_message to the user", () => {
    const message = matchStatusMessage({ status: "FAILED", error_message: "AIProviderAuthError: key rejected (401)" });
    expect(message).not.toContain("AIProviderAuthError");
    expect(message).not.toContain("401");
  });

  it("has a distinct message for a still-pending match", () => {
    expect(matchStatusMessage({ status: "PENDING", error_message: null })).toMatch(/being computed/i);
  });
});

const sampleMatchCard: MatchCard = {
  match_id: "match-1",
  candidate_id: "cand-1",
  candidate_name: "Priya Sharma",
  job_id: "job-1",
  job_title: "Full Stack Developer",
  status: "COMPLETED",
  overall_match_score: 95,
  confidence: "HIGH",
  matching_skills: ["React", "Node.js"],
  missing_skills: [],
  role_alignment: "Strong alignment",
  explanation: "Strong match.",
  potential_concerns: [],
  evidence: [],
  disclaimer: "AI-generated assessment. Final hiring decision remains with the recruitment team.",
};

describe("normalizeMatchCard", () => {
  it("maps every field through without loss", () => {
    const normalized = normalizeMatchCard(sampleMatchCard);
    expect(normalized.candidateName).toBe("Priya Sharma");
    expect(normalized.overallScore).toBe(95);
    expect(normalized.matchingSkills).toEqual(["React", "Node.js"]);
    expect(normalized.categories).toBeUndefined();
  });
});

const sampleMatchResponse: MatchResponse = {
  id: "match-2",
  candidate_id: "cand-2",
  job_id: "job-2",
  application_id: null,
  status: "COMPLETED",
  provider: "deterministic",
  model: "rule-based-v1",
  overall_match_score: 50,
  confidence: "MEDIUM",
  matching_skills: ["React"],
  missing_skills: ["MongoDB"],
  matching_experience: { score: 0.5, detail: "2 years vs 4+ required." },
  matching_education: { score: 0, detail: "No match." },
  matching_location: null,
  notice_period_fit: { score: 1, detail: "Within range." },
  role_alignment: "Limited alignment",
  potential_concerns: ["Experience gap"],
  evidence: [{ requirement_label: "React", resume_chunk_id: "chunk-1", excerpt: "Used React for 2 years.", similarity: 0.8 }],
  explanation: "Partial match.",
  scoring_breakdown: { SKILL: { score: 0.5, weight: 0.45 } },
  error_message: null,
  created_at: new Date().toISOString(),
  disclaimer: "AI-generated assessment. Final hiring decision remains with the recruitment team.",
};

describe("normalizeMatchResponse", () => {
  it("builds a category breakdown only from the fields that are present", () => {
    const normalized = normalizeMatchResponse("Rahul Verma", "Backend Engineer", sampleMatchResponse);
    expect(normalized.candidateName).toBe("Rahul Verma");
    expect(normalized.jobTitle).toBe("Backend Engineer");
    expect(normalized.categories).toEqual([
      { label: "Experience", score: 0.5, detail: "2 years vs 4+ required." },
      { label: "Education", score: 0, detail: "No match." },
      { label: "Notice period", score: 1, detail: "Within range." },
    ]);
    // matching_location was null on the sample — never a fabricated category for it.
    expect(normalized.categories?.some((c) => c.label === "Location")).toBe(false);
  });

  it("never lets the deterministic score be overridden by anything AI-generated", () => {
    const normalized = normalizeMatchResponse("Rahul Verma", "Backend Engineer", sampleMatchResponse);
    expect(normalized.overallScore).toBe(sampleMatchResponse.overall_match_score);
  });
});

describe("normalizePipelineStats", () => {
  const valid = {
    applications_by_status: { APPLIED: 1, SCREENING: 1, SHORTLISTED: 3 },
    open_jobs: 4,
    top_jobs_by_applications: [
      { title: "Data Analyst", application_count: 2 },
      { title: "Backend Engineer", application_count: 1 },
    ],
  };

  it("normalizes the API's structured payload, sorting statuses by count", () => {
    expect(normalizePipelineStats(valid)).toEqual({
      statusCounts: [
        { status: "SHORTLISTED", count: 3 },
        { status: "APPLIED", count: 1 },
        { status: "SCREENING", count: 1 },
      ],
      totalApplications: 5,
      openJobs: 4,
      topJobs: [
        { title: "Data Analyst", count: 2 },
        { title: "Backend Engineer", count: 1 },
      ],
    });
  });

  it("marks missing sections as null instead of inventing values", () => {
    expect(normalizePipelineStats({ open_jobs: 2 })).toEqual({
      statusCounts: null,
      totalApplications: null,
      openJobs: 2,
      topJobs: null,
    });
  });

  it("keeps empty-but-valid sections so the UI can say 'none yet'", () => {
    expect(normalizePipelineStats({ applications_by_status: {}, open_jobs: 0, top_jobs_by_applications: [] })).toEqual({
      statusCounts: [],
      totalApplications: 0,
      openJobs: 0,
      topJobs: [],
    });
  });

  it("drops malformed entries but keeps the valid ones", () => {
    const result = normalizePipelineStats({
      applications_by_status: { APPLIED: 2, SCREENING: "3", REJECTED: -1, HIRED: Number.NaN, "": 1 },
      open_jobs: "4",
      top_jobs_by_applications: [
        { title: "Data Analyst", application_count: 2 },
        { title: "", application_count: 1 },
        { title: "No count" },
        "Backend Engineer",
        null,
      ],
    });
    expect(result).toEqual({
      statusCounts: [{ status: "APPLIED", count: 2 }],
      totalApplications: 2,
      openJobs: null,
      topJobs: [{ title: "Data Analyst", count: 2 }],
    });
  });

  it.each([
    ["null", null],
    ["a serialized JSON string", JSON.stringify(valid)],
    ["an array", [valid]],
    ["an object with no recognized fields", { foo: "bar" }],
    ["an object whose fields are all malformed", { applications_by_status: "APPLIED:1", open_jobs: null, top_jobs_by_applications: {} }],
  ])("returns null for %s", (_label, raw) => {
    expect(normalizePipelineStats(raw)).toBeNull();
  });
});
