/**
 * Mirrors backend/app/schemas/internal_ai.py (Phase A/B — internal AI
 * "Recruitment Intelligence"). Structured card shapes, not just prose, so
 * the UI renders candidate/job/match cards instead of parsing text.
 */

export interface InternalAIHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface InternalAIQueryContext {
  candidate_id?: string | null;
  job_id?: string | null;
  application_id?: string | null;
  candidate_ids?: string[];
}

export interface InternalAIQueryRequest {
  message: string;
  conversation_id?: string | null;
  history?: InternalAIHistoryMessage[];
  context?: InternalAIQueryContext | null;
}

export interface CandidateCard {
  id: string;
  full_name: string;
  current_title: string | null;
  current_company: string | null;
  location: string | null;
  years_experience: number | null;
  notice_period_days: number | null;
  application_status: string | null;
}

export interface JobCard {
  id: string;
  title: string;
  department: string | null;
  location: string | null;
  status: string;
}

export interface MatchEvidence {
  requirement_label: string;
  resume_chunk_id: string;
  excerpt: string;
  similarity: number;
}

export interface MatchCard {
  match_id: string | null;
  candidate_id: string;
  candidate_name: string;
  job_id: string;
  job_title: string;
  status: "PENDING" | "COMPLETED" | "FAILED";
  overall_match_score: number | null;
  confidence: string | null;
  matching_skills: string[];
  missing_skills: string[];
  role_alignment: string | null;
  explanation: string | null;
  potential_concerns: string[];
  evidence: MatchEvidence[];
  disclaimer: string;
}

export interface TopJobStat {
  title: string;
  application_count: number;
}

/** Org-wide counts returned for a pipeline-summary question. */
export interface PipelineStats {
  /** ApplicationStatus value -> count (only statuses with >= 1 application). */
  applications_by_status: Record<string, number>;
  open_jobs: number;
  top_jobs_by_applications: TopJobStat[];
}

export interface InternalAIQueryResponse {
  conversation_id: string;
  message: string;
  candidates: CandidateCard[];
  jobs: JobCard[];
  matches: MatchCard[];
  pipeline_stats: PipelineStats | null;
  disclaimer: string;
}

export interface MatchRequest {
  candidate_id: string;
  job_id: string;
  application_id?: string | null;
}

export interface JobMatchRequest {
  job_id: string;
  limit?: number;
}

export interface CategoryBreakdown {
  score: number;
  matched?: string[];
  missing?: string[];
  detail?: string;
}

export interface MatchResponse {
  id: string;
  candidate_id: string;
  job_id: string;
  application_id: string | null;
  status: "PENDING" | "COMPLETED" | "FAILED";
  provider: string;
  model: string;
  overall_match_score: number | null;
  confidence: string | null;
  matching_skills: string[];
  missing_skills: string[];
  matching_experience: CategoryBreakdown | null;
  matching_education: CategoryBreakdown | null;
  matching_location: CategoryBreakdown | null;
  notice_period_fit: CategoryBreakdown | null;
  role_alignment: string | null;
  potential_concerns: string[];
  evidence: MatchEvidence[];
  explanation: string | null;
  scoring_breakdown: Record<string, { score: number; weight: number }> | null;
  error_message: string | null;
  created_at: string;
  disclaimer: string;
}
