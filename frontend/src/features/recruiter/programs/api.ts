import { apiClient } from "../../../lib/apiClient";
import type { AssessmentInvitationResponse } from "../../../types/assessment";
import type {
  EnrollmentResponse,
  ProgramCreateRequest,
  ProgramReport,
  ProgramResponse,
  ProgramStatus,
  ProgramSummary,
  ProgramUpdateRequest,
} from "../../../types/program";

const BASE = "/api/v1/recruiter/programs";

export function listPrograms(accessToken: string) {
  return apiClient.get<ProgramSummary[]>(BASE, accessToken);
}

export function getProgram(programId: string, accessToken: string) {
  return apiClient.get<ProgramResponse>(`${BASE}/${programId}`, accessToken);
}

export function createProgram(payload: ProgramCreateRequest, accessToken: string) {
  return apiClient.post<ProgramResponse>(BASE, payload, accessToken);
}

export function updateProgram(programId: string, payload: ProgramUpdateRequest, accessToken: string) {
  return apiClient.patch<ProgramResponse>(`${BASE}/${programId}`, payload, accessToken);
}

export function changeProgramStatus(programId: string, status: ProgramStatus, accessToken: string) {
  return apiClient.post<ProgramResponse>(`${BASE}/${programId}/status`, { status }, accessToken);
}

export function getProgramReport(programId: string, accessToken: string) {
  return apiClient.get<ProgramReport>(`${BASE}/${programId}/report`, accessToken);
}

export function listEnrollments(programId: string, accessToken: string) {
  return apiClient.get<EnrollmentResponse[]>(`${BASE}/${programId}/enrollments`, accessToken);
}

export function getEnrollment(programId: string, enrollmentId: string, accessToken: string) {
  return apiClient.get<EnrollmentResponse>(
    `${BASE}/${programId}/enrollments/${enrollmentId}`,
    accessToken,
  );
}

/** Every participant action posts JSON to `.../enrollments/{id}/{action}`
 * and returns the refreshed participant. */
export function enrollmentAction(
  programId: string,
  enrollmentId: string,
  action: string,
  payload: Record<string, unknown>,
  accessToken: string,
) {
  return apiClient.post<EnrollmentResponse>(
    `${BASE}/${programId}/enrollments/${enrollmentId}/${action}`,
    payload,
    accessToken,
  );
}

export function inviteToProgramAssessment(programId: string, enrollmentId: string, accessToken: string) {
  return apiClient.post<AssessmentInvitationResponse>(
    `${BASE}/${programId}/enrollments/${enrollmentId}/invite-assessment`,
    {},
    accessToken,
  );
}

export function uploadAgreementDocument(
  programId: string,
  enrollmentId: string,
  file: File,
  accessToken: string,
) {
  const formData = new FormData();
  formData.append("file", file);
  return apiClient.postForm<EnrollmentResponse>(
    `${BASE}/${programId}/enrollments/${enrollmentId}/agreement/document`,
    formData,
    accessToken,
  );
}

export function downloadAgreementDocument(programId: string, enrollmentId: string, accessToken: string) {
  return apiClient.getBlob(
    `${BASE}/${programId}/enrollments/${enrollmentId}/agreement/document`,
    accessToken,
  );
}
