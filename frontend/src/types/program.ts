/** Mirrors backend/app/schemas/program.py. Money values arrive as decimal
 * strings (never floats) and are null whenever the business hasn't
 * finalized them. */

import type { ApplicationStatus } from "./recruitment";
import type { InstitutionSummary } from "./institution";
import type { ResultGradingStatus } from "./assessment";

export type EducationLevel = "DIPLOMA" | "BACHELORS" | "MASTERS" | "MTECH" | "PHD" | "OTHER";
export type ProgramStatus = "DRAFT" | "PUBLISHED" | "PAUSED" | "CLOSED" | "ARCHIVED";
export type EligibilityStatus = "PENDING" | "ELIGIBLE" | "NOT_ELIGIBLE";
export type ScholarshipEligibility = "NOT_DETERMINED" | "ELIGIBLE" | "NOT_ELIGIBLE";
export type ScholarshipStatus = "NOT_STARTED" | "PENDING" | "APPROVED" | "DECLINED";
export type TrainingStatus = "NOT_ENROLLED" | "ENROLLED" | "IN_PROGRESS" | "COMPLETED" | "DISCONTINUED";
export type OfferStatus = "NOT_OFFERED" | "OFFERED" | "ACCEPTED" | "DECLINED" | "WITHDRAWN";
export type AgreementStatus = "NOT_REQUIRED" | "PENDING" | "SENT" | "SIGNED" | "DECLINED" | "VOID";
export type DisbursementStatus = "SCHEDULED" | "PAID" | "CANCELLED";
export type PaymentMethod = "BANK_TRANSFER" | "UPI" | "CHEQUE" | "CASH" | "OTHER";

export const EDUCATION_LEVEL_LABELS: Record<EducationLevel, string> = {
  DIPLOMA: "Diploma",
  BACHELORS: "Bachelor's degree",
  MASTERS: "Master's degree",
  MTECH: "M.Tech",
  PHD: "PhD",
  OTHER: "Other",
};

export const PROGRAM_STATUS_LABELS: Record<ProgramStatus, string> = {
  DRAFT: "Draft",
  PUBLISHED: "Published",
  PAUSED: "Paused",
  CLOSED: "Closed",
  ARCHIVED: "Archived",
};

/** Mirrors PROGRAM_STATUS_TRANSITIONS in app/workflows/program_workflow.py. */
export const PROGRAM_TRANSITIONS: Record<ProgramStatus, ProgramStatus[]> = {
  DRAFT: ["PUBLISHED", "ARCHIVED"],
  PUBLISHED: ["PAUSED", "CLOSED"],
  PAUSED: ["PUBLISHED", "CLOSED"],
  CLOSED: ["PUBLISHED", "ARCHIVED"],
  ARCHIVED: [],
};

export const APPROVED_TRAINING_DURATIONS = [6, 12] as const;

export interface ProgramFields {
  eligible_education_levels?: EducationLevel[] | null;
  eligible_study_years?: number[] | null;
  eligible_specializations?: string[] | null;
  eligible_institution_ids?: string[] | null;
  expected_completion_from?: string | null;
  expected_completion_to?: string | null;
  eligibility_notes?: string | null;
  training_duration_options?: number[] | null;
  training_fee_amount?: string | null;
  training_fee_currency?: string | null;
  fee_payment_terms?: string | null;
  fee_disclosure?: string | null;
  scholarship_amount_min?: string | null;
  scholarship_amount_max?: string | null;
  scholarship_currency?: string | null;
  scholarship_eligibility_rules?: string | null;
  scholarship_disbursement_timing?: string | null;
  scholarship_disclosure?: string | null;
  employment_pathway?: string | null;
  bond_duration_months?: number | null;
  bond_terms_summary?: string | null;
  bond_start_rule?: string | null;
  application_opens_at?: string | null;
  application_closes_at?: string | null;
  assessment_id?: string | null;
  interview_stages?: string[] | null;
  required_documents?: string[] | null;
  candidate_disclosures?: string | null;
  terms_version?: string | null;
  tracks_fee_payments?: boolean | null;
  tracks_training?: boolean | null;
  tracks_employment?: boolean | null;
  requires_agreement?: boolean | null;
}

export interface ProgramCreateRequest extends ProgramFields {
  title: string;
  description: string;
}

export interface ProgramUpdateRequest extends ProgramFields {
  title?: string;
  description?: string;
}

export interface ProgramResponse {
  id: string;
  title: string;
  description: string;
  status: ProgramStatus;
  job_id: string;
  eligible_education_levels: EducationLevel[];
  eligible_study_years: number[];
  eligible_specializations: string[];
  eligible_institutions: InstitutionSummary[];
  expected_completion_from: string | null;
  expected_completion_to: string | null;
  eligibility_notes: string | null;
  training_duration_options: number[];
  training_fee_amount: string | null;
  training_fee_currency: string | null;
  fee_payment_terms: string | null;
  fee_disclosure: string | null;
  scholarship_amount_min: string | null;
  scholarship_amount_max: string | null;
  scholarship_currency: string | null;
  scholarship_eligibility_rules: string | null;
  scholarship_disbursement_timing: string | null;
  scholarship_disclosure: string | null;
  employment_pathway: string | null;
  bond_duration_months: number | null;
  bond_terms_summary: string | null;
  bond_start_rule: string | null;
  application_opens_at: string | null;
  application_closes_at: string | null;
  assessment_id: string | null;
  assessment_title: string | null;
  interview_stages: string[];
  required_documents: string[];
  candidate_disclosures: string | null;
  terms_version: string | null;
  tracks_fee_payments: boolean;
  tracks_training: boolean;
  tracks_employment: boolean;
  requires_agreement: boolean;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  publication_problems: string[];
  enrollment_count: number;
}

export interface ProgramSummary {
  id: string;
  title: string;
  status: ProgramStatus;
  training_duration_options: number[];
  application_opens_at: string | null;
  application_closes_at: string | null;
  enrollment_count: number;
  created_at: string;
}

export interface ProgramReport {
  applications: number;
  eligibility_pending: number;
  eligible: number;
  not_eligible: number;
  assessment_invited: number;
  assessment_passed: number;
  assessment_failed: number;
  assessment_pending_review: number;
  interview: number;
  interview_completed: number;
  selected: number;
  not_selected: number;
  scholarship_eligible: number;
  scholarship_pending: number;
  scholarship_approved: number;
  scholarship_declined: number;
  training_enrolled: number;
  training_in_progress: number;
  training_completed: number;
  offers_made: number;
  offers_accepted: number;
  joined: number;
  agreements_signed: number;
}

export interface FeePaymentResponse {
  id: string;
  amount: string;
  currency: string;
  paid_on: string;
  method: PaymentMethod;
  reference: string | null;
  note: string | null;
  status: "RECORDED" | "VOIDED";
  void_reason: string | null;
  voided_at: string | null;
  created_at: string;
}

export interface DisbursementResponse {
  id: string;
  amount: string;
  currency: string;
  status: DisbursementStatus;
  scheduled_on: string | null;
  paid_on: string | null;
  reference: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface EnrollmentResponse {
  id: string;
  program_id: string;
  application_id: string;
  candidate_id: string;
  candidate_full_name: string;
  candidate_email: string;
  application_status: ApplicationStatus;
  stage: string;
  applied_at: string;
  education_level: EducationLevel;
  study_year: number | null;
  specialization: string;
  institution_id: string | null;
  institution_name: string;
  expected_completion_date: string | null;
  terms_version_accepted: string;
  terms_accepted_at: string;
  eligibility_status: EligibilityStatus;
  eligibility_reviewed_at: string | null;
  eligibility_note: string | null;
  latest_result: {
    percentage: number;
    passed: boolean | null;
    grading_status: ResultGradingStatus;
    attempt_number: number;
  } | null;
  interview_completed_at: string | null;
  scholarship_eligibility: ScholarshipEligibility;
  scholarship_eligibility_decided_at: string | null;
  scholarship_status: ScholarshipStatus;
  approved_scholarship_amount: string | null;
  approved_scholarship_currency: string | null;
  scholarship_decided_at: string | null;
  scholarship_decision_note: string | null;
  training_status: TrainingStatus;
  training_duration_months: number | null;
  training_start_date: string | null;
  training_end_date: string | null;
  offer_status: OfferStatus;
  offer_made_at: string | null;
  joined_on: string | null;
  agreement_status: AgreementStatus;
  agreement_reference: string | null;
  agreement_signed_on: string | null;
  agreement_document_filename: string | null;
  agreement_document_uploaded_at: string | null;
  bond_duration_months: number | null;
  bond_start_date: string | null;
  bond_end_date: string | null;
  fee_paid_total: string;
  disbursed_total: string;
  fee_payments: FeePaymentResponse[];
  disbursements: DisbursementResponse[];
}

// --- Candidate-facing ---

export interface PublicProgramSummary {
  id: string;
  title: string;
  training_duration_options: number[];
  eligible_education_levels: EducationLevel[];
  application_closes_at: string | null;
  accepting_applications: boolean;
}

export interface PublicProgramView {
  kind: "program";
  id: string;
  title: string;
  description: string;
  organization_name: string;
  careers_contact_email: string | null;
  eligible_education_levels: EducationLevel[];
  eligible_study_years: number[];
  eligible_specializations: string[];
  eligible_institutions: InstitutionSummary[];
  expected_completion_from: string | null;
  expected_completion_to: string | null;
  eligibility_notes: string | null;
  training_duration_options: number[];
  training_fee_amount: string | null;
  training_fee_currency: string | null;
  fee_payment_terms: string | null;
  fee_disclosure: string | null;
  scholarship_amount_min: string | null;
  scholarship_amount_max: string | null;
  scholarship_currency: string | null;
  scholarship_eligibility_rules: string | null;
  scholarship_disbursement_timing: string | null;
  scholarship_disclosure: string | null;
  employment_pathway: string | null;
  bond_duration_months: number | null;
  bond_terms_summary: string | null;
  has_assessment: boolean;
  interview_stages: string[];
  required_documents: string[];
  candidate_disclosures: string;
  terms_version: string;
  disclosure_text: string;
  application_opens_at: string | null;
  application_closes_at: string | null;
  accepting_applications: boolean;
  not_accepting_reason: string | null;
}

export interface PublicProgramUnavailable {
  kind: "unavailable";
  message: string;
}

export type PublicProgramResult = PublicProgramView | PublicProgramUnavailable;

/** The program-specific part of the application form. */
export interface ProgramDeclaration {
  education_level: EducationLevel | "";
  study_year: string;
  specialization: string;
  institution_id: string;
  institution_name: string;
  expected_completion_date: string;
  terms_version: string;
  terms_accepted: boolean;
}

export interface PublicProgramApplicationResult {
  id: string;
  program_title: string;
  candidate_email: string;
  confirmation_email_sent: boolean;
  careers_contact_email: string | null;
  submitted_at: string;
}
