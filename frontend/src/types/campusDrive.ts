/** Mirrors backend/app/schemas/campus_drive.py. */

import type { InstitutionCreateRequest } from "./institution";

export type AffiliationVerificationMethod = "NONE" | "EMAIL_DOMAIN" | "STUDENT_LIST" | "MANUAL";
export type AffiliationStatus = "PENDING" | "VERIFIED" | "REJECTED";

export const VERIFICATION_METHOD_LABELS: Record<AffiliationVerificationMethod, string> = {
  NONE: "Not required",
  EMAIL_DOMAIN: "College email domain",
  STUDENT_LIST: "College-provided student list",
  MANUAL: "Manual review (placement officer / HR)",
};

export type CampusDriveStatus = "DRAFT" | "ACTIVE" | "PAUSED" | "CLOSED";

export const CAMPUS_DRIVE_STATUSES: CampusDriveStatus[] = ["DRAFT", "ACTIVE", "PAUSED", "CLOSED"];

/** Either `job_id` (use an existing job) or both `new_job_title` and
 * `new_job_description` (create one inline) must be supplied — never both,
 * never neither. Enforced again server-side. */
export interface CampusDriveCreateRequest {
  name: string;
  /** Exactly one of `institution_id`, `new_institution` or (legacy)
   * `college_name`. */
  institution_id?: string | null;
  new_institution?: InstitutionCreateRequest | null;
  college_name?: string | null;
  affiliation_verification_method?: AffiliationVerificationMethod;
  job_id?: string | null;
  new_job_title?: string | null;
  new_job_description?: string | null;
  description?: string | null;
  batch_year?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  registration_deadline?: string | null;
  default_assessment_id?: string | null;
}

export interface CampusDriveUpdateRequest {
  name?: string;
  institution_id?: string;
  affiliation_verification_method?: AffiliationVerificationMethod;
  description?: string | null;
  batch_year?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  registration_deadline?: string | null;
  default_assessment_id?: string | null;
  status?: CampusDriveStatus;
}

export interface CampusDriveResponse {
  id: string;
  name: string;
  job_id: string;
  job_title: string;
  institution_id: string;
  institution_name: string;
  college_name: string;
  affiliation_verification_method: AffiliationVerificationMethod;
  description: string | null;
  batch_year: number | null;
  start_date: string | null;
  end_date: string | null;
  registration_deadline: string | null;
  default_assessment_id: string | null;
  default_assessment_title: string | null;
  status: CampusDriveStatus;
  application_count: number;
  deleted_at: string | null;
  created_at: string;
  /** Only populated once, on create/regenerate — never re-sent by list/get. */
  application_link: string | null;
}

export interface CampusDriveDeleteRequest {
  reason: string;
}

export interface CampusDriveFunnelCounts {
  registered: number;
  screening: number;
  assessment_invited: number;
  assessment_completed: number;
  assessment_passed: number;
  assessment_failed: number;
  shortlisted: number;
  interview: number;
  selected: number;
  rejected: number;
  hired: number;
  verification_pending: number;
  verified: number;
  verification_rejected: number;
}

export interface CampusDriveReportRow {
  drive_id: string;
  drive_name: string;
  drive_status: CampusDriveStatus;
  institution_id: string;
  institution_name: string;
  job_title: string;
  affiliation_verification_method: AffiliationVerificationMethod;
  applications: number;
  verification_pending: number;
  verified: number;
  verification_rejected: number;
  assessment_invited: number;
  assessment_completed: number;
  assessment_passed: number;
  assessment_failed: number;
  assessment_pending_review: number;
  interview: number;
  selected: number;
  hired: number;
  rejected: number;
}

export interface AffiliationVerificationResponse {
  id: string;
  application_id: string;
  candidate_full_name: string;
  candidate_email: string;
  status: AffiliationStatus;
  method: AffiliationVerificationMethod | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
}

export interface RosterEntryResponse {
  id: string;
  email: string;
  student_identifier: string | null;
  full_name: string | null;
  created_at: string;
}
