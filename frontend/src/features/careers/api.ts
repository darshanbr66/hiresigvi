import { apiClient } from "../../lib/apiClient";
import type {
  ApplicantStatus,
  ApplicantStatusQuery,
  EmailVerificationConfirmed,
  EmailVerificationRequested,
  JobApplicationFormValues,
  PublicApplicationResult,
  PublicJobDetail,
  PublicJobSummary,
  PublicOrganizationSummary,
} from "../../types/careers";
import type {
  AnswerSubmission,
  MonitoringEventCreate,
  PublicInvitationView,
  PublicSubmissionResult,
} from "../../types/assessment";
import type {
  PublicCampusDriveApplicationResult,
  PublicCampusDriveResult,
} from "../../types/publicCampusDrive";

export function listOpenJobs(slug: string) {
  return apiClient.get<PublicJobSummary[]>(`/api/v1/public/organizations/${slug}/jobs`);
}

export function getOpenJob(slug: string, jobId: string) {
  return apiClient.get<PublicJobDetail>(`/api/v1/public/organizations/${slug}/jobs/${jobId}`);
}

export function getPublicOrganization(slug: string) {
  return apiClient.get<PublicOrganizationSummary>(`/api/v1/public/organizations/${slug}`);
}

/** `jobId`: the job being applied for, so a candidate who already applied
 * to it gets no code. */
export function requestEmailCode(slug: string, email: string, jobId?: string) {
  return apiClient.post<EmailVerificationRequested>(
    `/api/v1/public/organizations/${slug}/email-verification/request`,
    jobId ? { email, job_id: jobId } : { email },
  );
}

export function verifyEmailCode(slug: string, email: string, code: string, jobId?: string) {
  return apiClient.post<EmailVerificationConfirmed>(
    `/api/v1/public/organizations/${slug}/email-verification/verify`,
    jobId ? { email, code, job_id: jobId } : { email, code },
  );
}

/** The apply form's early check, before any code is sent: only a yes/no. */
export function checkApplicantStatus(slug: string, query: ApplicantStatusQuery) {
  return apiClient.post<ApplicantStatus>(
    `/api/v1/public/organizations/${slug}/applicant-status`,
    query,
  );
}

export function checkCampusApplicantStatus(token: string, query: ApplicantStatusQuery) {
  return apiClient.post<ApplicantStatus>(`/api/v1/public/campus-drive/${token}/applicant-status`, query);
}

/** The complete application form — shared by the careers site and campus
 * drive links, which take exactly the same fields. */
function buildApplicationFormData(
  values: JobApplicationFormValues,
  resume: File,
  verificationToken: string,
  marksCard: File | null,
): FormData {
  const formData = new FormData();
  // Only non-empty values are sent — the backend treats an absent field as
  // "not provided", while an empty string would fail number/enum parsing.
  const append = (name: string, value: string) => {
    const trimmed = value.trim();
    if (trimmed) formData.append(name, trimmed);
  };

  append("full_name", values.full_name);
  append("email", values.email);
  formData.append("email_verification_token", verificationToken);
  append("phone", values.phone);
  append("date_of_birth", values.date_of_birth);
  append("place_of_birth", values.place_of_birth);
  for (const language of values.languages) append("languages", language);
  formData.append("candidate_type", values.candidate_type);
  if (values.candidate_type === "EXPERIENCED") {
    append("years_experience", values.years_experience);
    append("current_title", values.current_title);
    append("current_company", values.current_company);
    if (values.immediate_joiner) {
      formData.append("immediate_joiner", "true");
    } else {
      append("notice_period_days", values.notice_period_days);
    }
  }
  append("current_location", values.current_location);
  append("preferred_location", values.preferred_location);
  append("qualification", values.qualification);
  append("linkedin_url", values.linkedin_url);
  append("github_url", values.github_url);
  append("tenth_marks_percentage", values.tenth_marks_percentage);
  formData.append("resume", resume);
  if (marksCard) formData.append("tenth_marks_card", marksCard);
  return formData;
}

export function applyToJob(
  slug: string,
  jobId: string,
  values: JobApplicationFormValues,
  resume: File,
  verificationToken: string,
  marksCard: File | null = null,
) {
  return apiClient.postForm<PublicApplicationResult>(
    `/api/v1/public/organizations/${slug}/jobs/${jobId}/apply`,
    buildApplicationFormData(values, resume, verificationToken, marksCard),
  );
}

export function getAssessmentInvitation(token: string) {
  return apiClient.get<PublicInvitationView>(`/api/v1/public/assessment/${token}`);
}

export function startAssessment(token: string) {
  return apiClient.post<PublicInvitationView>(`/api/v1/public/assessment/${token}/start`);
}

export function submitAssessment(token: string, answers: AnswerSubmission[]) {
  return apiClient.post<PublicSubmissionResult>(`/api/v1/public/assessment/${token}/submit`, {
    answers,
  });
}

export function sendMonitoringEvents(token: string, events: MonitoringEventCreate[]) {
  return apiClient.post<void>(`/api/v1/public/assessment/${token}/events`, { events });
}

export function getCampusDriveByToken(token: string) {
  return apiClient.get<PublicCampusDriveResult>(`/api/v1/public/campus-drive/${token}`);
}

export function requestCampusEmailCode(token: string, email: string) {
  return apiClient.post<EmailVerificationRequested>(
    `/api/v1/public/campus-drive/${token}/email-verification/request`,
    { email },
  );
}

export function verifyCampusEmailCode(token: string, email: string, code: string) {
  return apiClient.post<EmailVerificationConfirmed>(
    `/api/v1/public/campus-drive/${token}/email-verification/verify`,
    { email, code },
  );
}

export function applyToCampusDrive(
  token: string,
  values: JobApplicationFormValues,
  resume: File,
  verificationToken: string,
  marksCard: File | null = null,
) {
  return apiClient.postForm<PublicCampusDriveApplicationResult>(
    `/api/v1/public/campus-drive/${token}/apply`,
    buildApplicationFormData(values, resume, verificationToken, marksCard),
  );
}
