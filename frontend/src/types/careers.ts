/** Mirrors backend/app/schemas/public.py. */

import type { CandidateType } from "./recruitment";

export interface PublicOrganizationSummary {
  name: string;
  slug: string;
  /** The recruitment team's published contact address (null = not published). */
  careers_contact_email: string | null;
}

export interface PublicJobSummary {
  id: string;
  title: string;
  department: string | null;
  location: string | null;
  employment_type: string | null;
  openings_count: number;
  created_at: string;
}

export interface PublicJobDetail extends PublicJobSummary {
  /** null when the recruiter has hidden the JD from the public page. */
  description: string | null;
  organization: PublicOrganizationSummary;
}

/** The submission confirmation. Deliberately carries no screening outcome,
 * AI score/reasoning or internal status — the response is the same whatever
 * the AI decided; any decision reaches the candidate by email. */
export interface PublicApplicationResult {
  id: string;
  job_title: string;
  candidate_email: string;
  /** true only if the confirmation email was actually sent. */
  confirmation_email_sent: boolean;
  careers_contact_email: string | null;
  submitted_at: string;
}

export interface EmailVerificationRequested {
  expires_in_seconds: number;
  resend_available_in_seconds: number;
}

/** A returning candidate's stored profile — mirrors the backend's
 * ApplicantProfilePrefill. Only ever received after the email's one-time code
 * has been verified. Any field may be null (an HR-created profile can be
 * incomplete). */
export interface ApplicantProfilePrefill {
  full_name: string | null;
  phone: string | null;
  date_of_birth: string | null;
  place_of_birth: string | null;
  languages: string[];
  candidate_type: CandidateType | null;
  years_experience: number | null;
  notice_period_days: number | null;
  immediate_joiner: boolean | null;
  current_title: string | null;
  current_company: string | null;
  location: string | null;
  preferred_location: string | null;
  qualification: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  tenth_marks_percentage: number | null;
}

export interface EmailVerificationConfirmed {
  verification_token: string;
  expires_at: string;
  existing_profile?: ApplicantProfilePrefill | null;
}

/** The early "already applied?" check — deliberately only a yes/no. */
export interface ApplicantStatus {
  already_applied: boolean;
}

export interface ApplicantStatusQuery {
  email?: string;
  phone?: string;
  job_id?: string;
}

/** All text inputs are kept as strings (the form's own state); `applyToJob`
 * decides what is sent — experience/availability fields only for
 * EXPERIENCED candidates. Every field is mandatory except `linkedin_url`,
 * `github_url` and `tenth_marks_percentage` (the backend enforces the same
 * rules). The optional 10th marks card file is passed to `applyToJob`
 * separately, like the resume. */
export interface JobApplicationFormValues {
  full_name: string;
  email: string;
  phone: string;
  date_of_birth: string;
  place_of_birth: string;
  languages: string[];
  candidate_type: CandidateType;
  years_experience: string;
  notice_period_days: string;
  immediate_joiner: boolean;
  current_title: string;
  current_company: string;
  current_location: string;
  preferred_location: string;
  qualification: string;
  linkedin_url: string;
  github_url: string;
  tenth_marks_percentage: string;
}
