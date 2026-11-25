/** Mirrors backend/app/schemas/application_draft.py — the progressive apply
 * flow (careers-site jobs and campus drive links). */

import type { CandidateType } from "./recruitment";

export type EducationCheckStatus =
  | "NOT_STARTED"
  | "VERIFIED"
  | "MISMATCH"
  | "UNCLEAR"
  | "FAILED"
  | "MANUAL_REVIEW";

/** The Professional background step, as the API names it (`location` is the
 * current location). */
export interface ApplicantBackground {
  date_of_birth: string;
  place_of_birth: string;
  languages: string[];
  candidate_type: CandidateType;
  location: string;
  preferred_location: string;
  linkedin_url: string | null;
  github_url: string | null;
  years_experience: number | null;
  current_title: string | null;
  current_company: string | null;
  notice_period_days: number | null;
  immediate_joiner: boolean | null;
}

export interface EducationDetails {
  qualification_key: string;
  degree_name: string | null;
  /** Keys from the specialization catalogue (`QualificationOptions.
   * specialization_categories`). Null only on drafts saved before it existed. */
  specialization_category: string | null;
  specialization: string | null;
  /** Set by the server from `specialization` (its label); ignored when sent. */
  branch: string | null;
  institution: string;
  completion_year: number;
  tenth_marks_percentage: number | string | null;
}

export interface QualificationOption {
  key: string;
  label: string;
  level: string;
  branch_label: string;
  branch_required: boolean;
  requires_degree_name: boolean;
  document_hint: string;
}

export interface SpecializationView {
  key: string;
  label: string;
}

/** One category of the two-level specialization choice. */
export interface SpecializationCategoryView {
  key: string;
  label: string;
  specializations: SpecializationView[];
}

/** What the Education step offers for this job — derived from its JD. */
export interface QualificationOptions {
  restricted: boolean;
  summary: string;
  options: QualificationOption[];
  fields_of_study: string[];
  field_mandatory: boolean;
  /** The specialization catalogue — the same for every job and qualification. */
  specialization_categories: SpecializationCategoryView[];
  accepted_document_types: string[];
  max_document_size_mb: number;
}

export interface AcademicDocumentView {
  filename: string;
  content_type: string;
  size_bytes: number;
  uploaded_at: string;
}

/** The academic-document check as the candidate sees it: a status and a
 * plain-language message — never the AI's raw output. */
export interface EducationCheck {
  status: EducationCheckStatus;
  message: string | null;
  can_continue: boolean;
  can_retry: boolean;
  can_request_manual_review: boolean;
  attempts_remaining: number;
}

export interface DraftView {
  email: string;
  full_name: string;
  phone: string;
  background: ApplicantBackground | null;
  education: EducationDetails | null;
  document: AcademicDocumentView | null;
  education_check: EducationCheck;
  expires_at: string;
}

export interface DraftAuth {
  email: string;
  email_verification_token: string;
}
