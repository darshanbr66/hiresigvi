/**
 * Presentation-only helpers for the internal AI surface — pure functions, no
 * network/state, so score/label formatting is unit-testable and consistent
 * everywhere a match result is shown.
 *
 * Terminology (deliberate, per product direction): the number
 * (`overall_match_score`) is the backend's deterministic matching score,
 * never described as "AI confidence" — `confidence` is a *separate* field
 * (how much real signal backed the score, e.g. missing resume data), shown
 * as its own line. Only the narrative `explanation` is AI-generated.
 */
import type { MatchCard, MatchEvidence, MatchResponse } from "../../../types/internalAi";

/** Badge tone class for the alignment label the backend already computed
 * (`role_alignment`) — never re-derived from the score independently, so
 * the badge and the label it sits next to can never disagree. */
export function alignmentToneClass(roleAlignment: string | null): string {
  switch (roleAlignment) {
    case "Strong alignment":
      return "badge-active";
    case "Moderate alignment":
      return "badge-warn";
    case "Limited alignment":
      return "badge-danger";
    default:
      return "badge-inactive";
  }
}

export function formatScore(score: number | null): string {
  return score === null ? "—" : `${score}%`;
}

export function formatConfidence(confidence: string | null): string {
  if (!confidence) return "Unknown data confidence";
  const label = confidence.charAt(0) + confidence.slice(1).toLowerCase();
  return `${label} data confidence`;
}

/** A short, safe one-line summary for a match that failed or is still
 * pending — never shows a raw backend/provider error string to the user. */
export function matchStatusMessage(match: Pick<MatchResponse, "status" | "error_message">): string | null {
  if (match.status === "COMPLETED") return null;
  if (match.status === "PENDING") return "This match is still being computed.";
  return "This match could not be completed. Try again in a moment.";
}

export interface MatchCategory {
  label: string;
  score: number;
  detail?: string;
}

/** One shape every match card renders from, regardless of which endpoint it
 * came from — the natural-language query response's lighter `MatchCard`, or
 * the full `MatchResponse` from an explicit `/ai/match` call (which also
 * carries the per-category breakdown). */
export interface NormalizedMatch {
  matchId: string | null;
  candidateId: string;
  candidateName: string;
  jobId: string;
  jobTitle: string;
  status: string;
  overallScore: number | null;
  confidence: string | null;
  matchingSkills: string[];
  missingSkills: string[];
  roleAlignment: string | null;
  explanation: string | null;
  potentialConcerns: string[];
  evidence: MatchEvidence[];
  disclaimer: string;
  /** Only present when the source was a full `MatchResponse`. */
  categories?: MatchCategory[];
}

export function normalizeMatchCard(card: MatchCard): NormalizedMatch {
  return {
    matchId: card.match_id,
    candidateId: card.candidate_id,
    candidateName: card.candidate_name,
    jobId: card.job_id,
    jobTitle: card.job_title,
    status: card.status,
    overallScore: card.overall_match_score,
    confidence: card.confidence,
    matchingSkills: card.matching_skills,
    missingSkills: card.missing_skills,
    roleAlignment: card.role_alignment,
    explanation: card.explanation,
    potentialConcerns: card.potential_concerns,
    evidence: card.evidence,
    disclaimer: card.disclaimer,
  };
}

const CATEGORY_LABELS: Record<string, string> = {
  matching_experience: "Experience",
  matching_education: "Education",
  matching_location: "Location",
  notice_period_fit: "Notice period",
};

export function normalizeMatchResponse(candidateName: string, jobTitle: string, match: MatchResponse): NormalizedMatch {
  const categories: MatchCategory[] = [];
  for (const [field, label] of Object.entries(CATEGORY_LABELS)) {
    const breakdown = match[field as keyof MatchResponse] as { score: number; detail?: string } | null;
    if (breakdown) categories.push({ label, score: breakdown.score, detail: breakdown.detail });
  }

  return {
    matchId: match.id,
    candidateId: match.candidate_id,
    candidateName,
    jobId: match.job_id,
    jobTitle,
    status: match.status,
    overallScore: match.overall_match_score,
    confidence: match.confidence,
    matchingSkills: match.matching_skills,
    missingSkills: match.missing_skills,
    roleAlignment: match.role_alignment,
    explanation: match.explanation,
    potentialConcerns: match.potential_concerns,
    evidence: match.evidence,
    disclaimer: match.disclaimer,
    categories,
  };
}

export interface StatusCount {
  status: string;
  count: number;
}

export interface JobCount {
  title: string;
  count: number;
}

/** Pipeline stats after validation. A `null` section means the field was
 * missing or malformed, so the panel omits it rather than guessing. */
export interface NormalizedPipelineStats {
  statusCounts: StatusCount[] | null;
  totalApplications: number | null;
  openJobs: number | null;
  topJobs: JobCount[] | null;
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Validates the `pipeline_stats` payload at the render boundary. The API
 * sends real JSON objects (see backend PipelineStats), but this still guards
 * against a shape drift so a bad entry is dropped instead of being dumped as
 * raw JSON. Returns `null` when nothing usable remains. */
export function normalizePipelineStats(raw: unknown): NormalizedPipelineStats | null {
  if (!isPlainObject(raw)) return null;

  let statusCounts: StatusCount[] | null = null;
  if (isPlainObject(raw.applications_by_status)) {
    statusCounts = Object.entries(raw.applications_by_status)
      .filter((entry): entry is [string, number] => entry[0] !== "" && isCount(entry[1]))
      .map(([status, count]) => ({ status, count }))
      .sort((a, b) => b.count - a.count || a.status.localeCompare(b.status));
  }

  let topJobs: JobCount[] | null = null;
  if (Array.isArray(raw.top_jobs_by_applications)) {
    topJobs = raw.top_jobs_by_applications.flatMap((job: unknown) =>
      isPlainObject(job) && typeof job.title === "string" && job.title.trim() !== "" && isCount(job.application_count)
        ? [{ title: job.title, count: job.application_count }]
        : [],
    );
  }

  const openJobs = isCount(raw.open_jobs) ? raw.open_jobs : null;
  if (statusCounts === null && topJobs === null && openJobs === null) return null;

  return {
    statusCounts,
    totalApplications: statusCounts ? statusCounts.reduce((sum, s) => sum + s.count, 0) : null,
    openJobs,
    topJobs,
  };
}
