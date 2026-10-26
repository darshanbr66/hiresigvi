/**
 * Mirrors backend/app/schemas/{job,candidate,application}.py (Phase 3).
 */

import type { ScreeningRunResponse } from "./screening";

export type JobStatus = "DRAFT" | "OPEN" | "ON_HOLD" | "CLOSED" | "WITHDRAWN";

export const JOB_STATUSES: JobStatus[] = ["DRAFT", "OPEN", "ON_HOLD", "CLOSED", "WITHDRAWN"];

/** Mirrors app/models/job.py::EmploymentType. */
export type EmploymentType = "INTERNSHIP" | "PART_TIME" | "FULL_TIME";

export const EMPLOYMENT_TYPES: EmploymentType[] = ["INTERNSHIP", "PART_TIME", "FULL_TIME"];

export const EMPLOYMENT_TYPE_LABELS: Record<EmploymentType, string> = {
  INTERNSHIP: "Internship",
  PART_TIME: "Part-time",
  FULL_TIME: "Full-time",
};

export type WorkMode = "ON_SITE" | "HYBRID" | "REMOTE";

export const WORK_MODE_LABELS: Record<WorkMode, string> = {
  ON_SITE: "On-site",
  HYBRID: "Hybrid",
  REMOTE: "Remote",
};

export type CompensationPeriod = "HOURLY" | "MONTHLY" | "ANNUAL";

export const COMPENSATION_PERIOD_LABELS: Record<CompensationPeriod, string> = {
  HOURLY: "per hour",
  MONTHLY: "per month",
  ANNUAL: "per year",
};

/** The optional, type-specific job details (backend/app/schemas/job.py::
 * _EmploymentDetails). Money is a decimal string, as the API sends it. */
export interface EmploymentDetails {
  work_mode: WorkMode | null;
  duration_months: number | null;
  is_paid: boolean | null;
  compensation_min: string | null;
  compensation_max: string | null;
  compensation_currency: string | null;
  compensation_period: CompensationPeriod | null;
  hours_per_week: number | null;
  work_schedule: string | null;
  min_experience_years: number | null;
  max_experience_years: number | null;
  eligibility_criteria: string | null;
  conversion_to_full_time: boolean | null;
  expected_joining_date: string | null;
  employment_terms: string | null;
}

export type EmploymentDetailField = keyof EmploymentDetails;

const COMMON_TYPE_FIELDS: EmploymentDetailField[] = [
  "work_mode",
  "compensation_min",
  "compensation_max",
  "compensation_currency",
  "compensation_period",
  "eligibility_criteria",
];

/** Mirrors app/models/job.py::EMPLOYMENT_TYPE_FIELDS — which details apply
 * to each type. The server rejects a value outside the type's set. */
export const EMPLOYMENT_TYPE_FIELDS: Record<EmploymentType, EmploymentDetailField[]> = {
  INTERNSHIP: [...COMMON_TYPE_FIELDS, "duration_months", "is_paid", "conversion_to_full_time"],
  PART_TIME: [
    ...COMMON_TYPE_FIELDS,
    "duration_months",
    "hours_per_week",
    "work_schedule",
    "min_experience_years",
    "max_experience_years",
    "employment_terms",
  ],
  FULL_TIME: [
    ...COMMON_TYPE_FIELDS,
    "min_experience_years",
    "max_experience_years",
    "expected_joining_date",
    "employment_terms",
  ],
};

export interface JobResponse extends Partial<EmploymentDetails> {
  id: string;
  organization_id: string;
  title: string;
  department: string | null;
  location: string | null;
  /** null only for a recruitment program's carrier job. */
  employment_type: EmploymentType | null;
  description: string;
  description_visible: boolean;
  status: JobStatus;
  openings_count: number;
  created_by: string;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobDeleteRequest {
  reason: string;
}

export interface JobCreateRequest extends Partial<EmploymentDetails> {
  title: string;
  department?: string | null;
  location?: string | null;
  employment_type?: EmploymentType;
  description: string;
  description_visible?: boolean;
  openings_count?: number;
}

export interface JobUpdateRequest extends Partial<EmploymentDetails> {
  title?: string;
  department?: string | null;
  location?: string | null;
  /** Can be changed, never cleared. */
  employment_type?: EmploymentType;
  description?: string;
  description_visible?: boolean;
  openings_count?: number;
  status?: JobStatus;
}

export type CandidateSource = "PORTAL" | "RECRUITER_ADDED" | "CAMPUS_IMPORT" | "REFERRAL" | "OTHER";

export type CandidateType = "FRESHER" | "EXPERIENCED";

export interface CandidateResponse {
  id: string;
  organization_id: string;
  email: string;
  full_name: string;
  phone: string | null;
  location: string | null;
  current_title: string | null;
  current_company: string | null;
  preferred_location: string | null;
  years_experience: number | null;
  candidate_type: CandidateType | null;
  notice_period_days: number | null;
  immediate_joiner: boolean | null;
  qualification: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  date_of_birth: string | null;
  place_of_birth: string | null;
  languages: string[];
  /** Set when the candidate verified their email with a one-time code. */
  email_verified_at: string | null;
  source: CandidateSource;
  is_active: boolean;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CandidateCreateRequest {
  email: string;
  full_name: string;
  phone?: string | null;
  location?: string | null;
  current_title?: string | null;
  years_experience?: number | null;
}

export type ApplicationStatus =
  | "APPLIED"
  | "AI_SCREENED_OUT"
  | "UNDER_REVIEW"
  | "SCREENING"
  | "ASSESSMENT_INVITED"
  | "ASSESSMENT_STARTED"
  | "ASSESSMENT_COMPLETED"
  | "SHORTLISTED"
  | "INTERVIEW"
  | "SELECTED"
  | "REJECTED"
  | "HIRED";

export const APPLICATION_STATUSES: ApplicationStatus[] = [
  "APPLIED",
  "AI_SCREENED_OUT",
  "UNDER_REVIEW",
  "SCREENING",
  "ASSESSMENT_INVITED",
  "ASSESSMENT_STARTED",
  "ASSESSMENT_COMPLETED",
  "SHORTLISTED",
  "INTERVIEW",
  "SELECTED",
  "REJECTED",
  "HIRED",
];

/** Mirrors app/workflows/application_workflow.py's TRANSITIONS table, minus
 * what a person can't pick from a status menu: AI_SCREENED_OUT is set only
 * by the automatic screening, and leaving it for review is the dedicated
 * "Override AI decision" action (reason required), not a plain status move. */
export const APPLICATION_TRANSITIONS: Record<ApplicationStatus, ApplicationStatus[]> = {
  APPLIED: ["UNDER_REVIEW", "REJECTED"],
  AI_SCREENED_OUT: ["REJECTED"],
  UNDER_REVIEW: ["SCREENING", "REJECTED"],
  SCREENING: ["ASSESSMENT_INVITED", "SHORTLISTED", "REJECTED"],
  ASSESSMENT_INVITED: ["ASSESSMENT_STARTED", "REJECTED"],
  ASSESSMENT_STARTED: ["ASSESSMENT_COMPLETED", "REJECTED"],
  ASSESSMENT_COMPLETED: ["SHORTLISTED", "REJECTED"],
  SHORTLISTED: ["INTERVIEW", "REJECTED"],
  INTERVIEW: ["SELECTED", "REJECTED"],
  SELECTED: ["HIRED"],
  REJECTED: [],
  HIRED: [],
};

export interface ApplicationResponse {
  id: string;
  organization_id: string;
  candidate_id: string;
  candidate_full_name: string;
  candidate_email: string;
  candidate_phone: string | null;
  job_id: string;
  job_title: string;
  campus_drive_id: string | null;
  status: ApplicationStatus;
  source: string;
  applied_at: string;
  created_at: string;
  updated_at: string;
  resume_id: string | null;
  resume_filename: string | null;
  deleted_at: string | null;
}

export interface ApplicationCreateRequest {
  candidate_id: string;
  job_id: string;
}

export type ApplicationSource =
  | "PORTAL"
  | "RECRUITER_ADDED"
  | "CAMPUS_IMPORT"
  | "REFERRAL"
  | "OTHER"
  | "HR_MATCH";

/** Mirrors backend/app/schemas/candidate_history.py. */
export interface CandidateApplicationHistory {
  application_id: string;
  job_id: string;
  job_title: string;
  source: ApplicationSource;
  status: ApplicationStatus;
  applied_at: string;
  /** The candidate's own self-service application (vs. an HR match). */
  is_original: boolean;
  deleted_at: string | null;
  screenings: ScreeningRunResponse[];
}

export interface CandidateTimelineEntry {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor_name: string | null;
  description: string | null;
  reason: string | null;
  created_at: string;
}

export interface CandidateHistoryResponse {
  candidate_id: string;
  applications: CandidateApplicationHistory[];
  timeline: CandidateTimelineEntry[];
}

export interface ApplicationDeleteRequest {
  reason: string;
}


/** Mirrors backend/app/schemas/candidate.py § ReapplyGrantResponse — a
 * single-use grant from the earlier "Allow Reapply" action. No longer
 * issued, but one still open is honoured and shown. */
export interface ReapplyGrantResponse {
  id: string;
  candidate_id: string;
  granted_by_user_id: string | null;
  granted_by_name: string | null;
  reason: string;
  created_at: string;
  used_at: string | null;
  used_by_application_id: string | null;
}

/** When the candidate may self-apply again. `eligible_from`/
 * `last_self_applied_at` are null for someone with no self-service
 * history, who is never restricted. */
export interface ReapplyStatusResponse {
  candidate_id: string;
  cooldown_months: number;
  last_self_applied_at: string | null;
  eligible_from: string | null;
  can_self_apply_now: boolean;
  open_grant: ReapplyGrantResponse | null;
}

/** One match in HR's "Reapply Permission" lookup (backend:
 * ReapplySearchResult). `can_self_apply_now` false = still inside the
 * reapply window, so the grant is on offer. */
export interface ReapplySearchResult {
  id: string;
  full_name: string;
  email: string;
  phone: string | null;
  last_self_applied_at: string | null;
  eligible_from: string | null;
  can_self_apply_now: boolean;
}

export interface ReapplyPermissionGrantedResponse {
  candidate_id: string;
  full_name: string;
  email: string;
  cleared_application_count: number;
}

/** Result of HR adding a resume + applying role for a candidate.
 * `attached_to_existing` = the resume filled in a resume-less application
 * this candidate already had for that role, rather than creating one. */
export interface HrApplicationWithResumeResponse {
  application: ApplicationResponse;
  screening: ScreeningRunResponse | null;
  attached_to_existing: boolean;
}
