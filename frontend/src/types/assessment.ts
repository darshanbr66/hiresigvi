/** Mirrors backend/app/schemas/assessment.py and public_assessment.py. */

export type QuestionType =
  | "MCQ_SINGLE"
  | "MCQ_MULTI"
  | "TRUE_FALSE"
  | "SHORT_ANSWER"
  | "DESCRIPTIVE"
  | "CODING";
export type InvitationStatus = "SENT" | "STARTED" | "SUBMITTED" | "EXPIRED" | "CANCELLED";
export type AssessmentStatus = "DRAFT" | "PUBLISHED";
export type ResultGradingStatus = "FINAL" | "PENDING_MANUAL_REVIEW";
export type GradingMethod = "AUTO" | "MANUAL";

export const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  MCQ_SINGLE: "Single choice",
  MCQ_MULTI: "Multiple choice",
  TRUE_FALSE: "True / false",
  SHORT_ANSWER: "Short answer",
  DESCRIPTIVE: "Descriptive",
  CODING: "Coding (manually reviewed)",
};

/** Answered by choosing options. */
export const CHOICE_TYPES: QuestionType[] = ["MCQ_SINGLE", "MCQ_MULTI", "TRUE_FALSE"];

export function isChoiceType(type: QuestionType): boolean {
  return CHOICE_TYPES.includes(type);
}

export interface QuestionOptionCreate {
  label: string;
  is_correct: boolean;
}

export interface QuestionCreate {
  prompt: string;
  type: QuestionType;
  points: number;
  negative_points?: number;
  options: QuestionOptionCreate[];
  accepted_answers?: string[];
  case_sensitive?: boolean;
  grading_notes?: string | null;
  explanation?: string | null;
  /** Set for a question taken from the question bank: the server copies
   * that exact version and ignores the content sent with it (only
   * `points` may differ). */
  bank_version_id?: string | null;
}

export interface AssessmentCreateRequest {
  title: string;
  instructions: string;
  duration_minutes: number;
  pass_score: number;
  questions: QuestionCreate[];
  status?: AssessmentStatus;
}

/** Response for the "Import Questions" preview step (POST
 * /recruiter/assessments/parse-questions) — nothing has been persisted
 * yet. `questions` is shaped exactly like AssessmentCreateRequest.questions
 * so it can be edited and submitted unchanged. */
export interface ParsedQuestionsResponse {
  questions: QuestionCreate[];
  warnings: string[];
}

export interface QuestionOptionResponse {
  id: string;
  label: string;
  is_correct: boolean;
}

export interface QuestionResponse {
  id: string;
  prompt: string;
  type: QuestionType;
  points: number;
  negative_points: number;
  options: QuestionOptionResponse[];
  accepted_answers: string[];
  case_sensitive: boolean;
  grading_notes: string | null;
  explanation: string | null;
  source_bank_version_id: string | null;
}

export interface AssessmentResponse {
  id: string;
  title: string;
  instructions: string;
  duration_minutes: number;
  pass_score: number;
  status: AssessmentStatus;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  questions: QuestionResponse[];
  /** True once any candidate could have been invited — the point past
   * which question-structure edits are locked (see AssessmentUpdateRequest). */
  has_invitations: boolean;
}

export interface AssessmentUpdateRequest {
  title?: string;
  instructions?: string;
  duration_minutes?: number;
  pass_score?: number;
  /** Replaces the entire question set — only accepted while
   * `has_invitations` is false. */
  questions?: QuestionCreate[];
}

export interface AssessmentSummary {
  id: string;
  title: string;
  duration_minutes: number;
  pass_score: number;
  question_count: number;
  status: AssessmentStatus;
  created_at: string;
}

export interface AssessmentDeleteRequest {
  reason: string;
}

export interface AssessmentResultResponse {
  score: number;
  max_score: number;
  percentage: number;
  /** null while answers still await manual grading. */
  passed: boolean | null;
  grading_status: ResultGradingStatus;
  evaluated_at: string;
}

export interface AssessmentPreviewResponse {
  title: string;
  instructions: string;
  duration_minutes: number;
  questions: PublicQuestion[];
}

export interface AttemptAnswerView {
  question_id: string;
  order: number;
  prompt: string;
  type: QuestionType;
  points: number;
  negative_points: number;
  options: QuestionOptionResponse[];
  accepted_answers: string[];
  grading_notes: string | null;
  explanation: string | null;
  answer_id: string | null;
  selected_option_ids: string[];
  text_answer: string | null;
  is_correct: boolean | null;
  awarded_points: number | null;
  grading_method: GradingMethod | null;
  needs_manual_grading: boolean;
  manually_gradable: boolean;
  graded_by_name: string | null;
  graded_at: string | null;
  grader_comment: string | null;
}

export interface AttemptReviewResponse {
  invitation_id: string;
  application_id: string;
  candidate_full_name: string;
  assessment_title: string;
  attempt_number: number;
  status: InvitationStatus;
  submitted_at: string | null;
  result: AssessmentResultResponse | null;
  answers: AttemptAnswerView[];
}

export interface GradeEntry {
  question_id: string;
  awarded_points: number;
  comment?: string | null;
}

export interface PendingReviewEntry {
  invitation_id: string;
  application_id: string;
  assessment_id: string;
  assessment_title: string;
  candidate_full_name: string;
  attempt_number: number;
  submitted_at: string | null;
}

export interface AssessmentInvitationResponse {
  id: string;
  assessment_id: string;
  assessment_title: string;
  application_id: string;
  candidate_full_name: string;
  status: InvitationStatus;
  expires_at: string;
  started_at: string | null;
  submitted_at: string | null;
  /** null = prepared but never emailed to the candidate. */
  emailed_at: string | null;
  attempt_number: number;
  retest_reason: string | null;
  result: AssessmentResultResponse | null;
  invitation_link: string | null;
}

export type RetestAssessmentChoice = "SAME" | "EXISTING" | "NEW";

export interface RetestRequest {
  application_id: string;
  reason: string;
  assessment_choice: RetestAssessmentChoice;
  assessment_id?: string | null;
  new_assessment?: AssessmentCreateRequest | null;
}

// --- Public (candidate-facing, token-based) ---

export interface PublicQuestionOption {
  id: string;
  label: string;
}

export interface PublicQuestion {
  id: string;
  prompt: string;
  type: QuestionType;
  points: number;
  options: PublicQuestionOption[];
}

export interface PublicInvitationView {
  status: InvitationStatus;
  assessment_title: string;
  instructions: string;
  duration_minutes: number;
  job_title: string;
  organization_name: string;
  expires_at: string;
  started_at: string | null;
  questions: PublicQuestion[];
}

export interface AnswerSubmission {
  question_id: string;
  selected_option_ids: string[];
  /** Short-answer, descriptive and coding questions. */
  text_answer?: string | null;
}

/** No score/percentage/pass-fail on purpose — the candidate sees a
 * polished confirmation, never a number (recruiters see the score via
 * AssessmentResultResponse, a separate schema). */
export interface PublicSubmissionResult {
  submitted_at: string;
}

export type MonitoringEventType =
  | "MONITORING_CONSENT_GIVEN"
  | "TAB_SWITCH"
  | "WINDOW_BLUR"
  | "WINDOW_FOCUS"
  | "FULLSCREEN_EXIT"
  | "CAMERA_PERMISSION_CHANGED"
  | "MICROPHONE_PERMISSION_CHANGED"
  | "CAMERA_DEVICE_CHANGED"
  | "MICROPHONE_DEVICE_CHANGED"
  | "CAMERA_UNAVAILABLE"
  | "MICROPHONE_UNAVAILABLE"
  | "CONNECTION_INTERRUPTED"
  | "CONNECTION_RESTORED";

export interface MonitoringEventCreate {
  event_type: MonitoringEventType;
  occurred_at: string;
  duration_ms?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface MonitoringEventResponse {
  id: string;
  event_type: MonitoringEventType;
  occurred_at: string;
  duration_ms: number | null;
  metadata: Record<string, unknown> | null;
}
