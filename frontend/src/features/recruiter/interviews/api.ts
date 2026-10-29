import { apiClient } from "../../../lib/apiClient";
import type { InterviewListResponse, InterviewOutcome, InterviewResponse } from "../../../types/program";

/** Interview rounds of any application (docs/recruitment-workflow.md § 2a). */
const url = (applicationId: string) => `/api/v1/recruiter/applications/${applicationId}/interviews`;

export function listInterviews(applicationId: string, accessToken: string) {
  return apiClient.get<InterviewListResponse>(url(applicationId), accessToken);
}

export function scheduleInterview(
  applicationId: string,
  payload: {
    stage: string;
    scheduled_at: string;
    duration_minutes?: number | null;
    location?: string | null;
    interviewer_user_id?: string | null;
  },
  accessToken: string,
) {
  return apiClient.post<InterviewResponse>(url(applicationId), payload, accessToken);
}

export function recordInterviewOutcome(
  applicationId: string,
  interviewId: string,
  payload: { outcome: InterviewOutcome; rating?: number | null; feedback?: string | null },
  accessToken: string,
) {
  return apiClient.post<InterviewResponse>(`${url(applicationId)}/${interviewId}/outcome`, payload, accessToken);
}

export function closeInterview(
  applicationId: string,
  interviewId: string,
  payload: { status: "CANCELLED" | "NO_SHOW"; reason?: string | null },
  accessToken: string,
) {
  return apiClient.post<InterviewResponse>(`${url(applicationId)}/${interviewId}/close`, payload, accessToken);
}
