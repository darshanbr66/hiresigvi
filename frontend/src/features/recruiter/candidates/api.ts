import { apiClient } from "../../../lib/apiClient";
import type {
  ApplicationResponse,
  CandidateCreateRequest,
  CandidateHistoryResponse,
  CandidateResponse,
  HrApplicationWithResumeResponse,
  ReapplyPermissionGrantedResponse,
  ReapplySearchResult,
  ReapplyStatusResponse,
} from "../../../types/recruitment";

export function listCandidates(accessToken: string) {
  return apiClient.get<CandidateResponse[]>("/api/v1/recruiter/candidates", accessToken);
}

export function createCandidate(payload: CandidateCreateRequest, accessToken: string) {
  return apiClient.post<CandidateResponse>("/api/v1/recruiter/candidates", payload, accessToken);
}

export function getCandidate(candidateId: string, accessToken: string) {
  return apiClient.get<CandidateResponse>(`/api/v1/recruiter/candidates/${candidateId}`, accessToken);
}

export function deleteCandidate(candidateId: string, reason: string, accessToken: string) {
  return apiClient.post<CandidateResponse>(
    `/api/v1/recruiter/candidates/${candidateId}/delete`,
    { reason },
    accessToken,
  );
}

/** Original application, HR job matches, every AI screening run and the
 * candidate-journey audit trail. */
export function getCandidateHistory(candidateId: string, accessToken: string) {
  return apiClient.get<CandidateHistoryResponse>(
    `/api/v1/recruiter/candidates/${candidateId}/history`,
    accessToken,
  );
}

/** HR matches the candidate to another job (a new HR_MATCH application on the
 * same profile; the original application is untouched). */
export function matchCandidateToJob(
  candidateId: string,
  payload: { job_id: string; reason?: string | null },
  accessToken: string,
) {
  return apiClient.post<ApplicationResponse>(
    `/api/v1/recruiter/candidates/${candidateId}/job-matches`,
    payload,
    accessToken,
  );
}

/** When this candidate may self-apply again, plus any open HR grant
 * (backend: app/services/reapply_service.py). */
export function getReapplyStatus(candidateId: string, accessToken: string) {
  return apiClient.get<ReapplyStatusResponse>(
    `/api/v1/recruiter/candidates/${candidateId}/reapply-status`,
    accessToken,
  );
}

/** HR's "Reapply Permission" lookup by name, email or mobile number. */
export function searchCandidatesForReapply(query: string, accessToken: string) {
  return apiClient.get<ReapplySearchResult[]>(
    `/api/v1/recruiter/candidates/reapply-search?q=${encodeURIComponent(query)}`,
    accessToken,
  );
}

/** HR "Grant Reapply Permission": clears the candidate's previous
 * self-service applications so they can apply again with the same email and
 * mobile number. 404 unknown candidate; 409 when nothing is restricted. */
export function grantReapplyPermission(candidateId: string, accessToken: string) {
  return apiClient.post<ReapplyPermissionGrantedResponse>(
    `/api/v1/recruiter/candidates/${candidateId}/grant-reapply`,
    undefined,
    accessToken,
  );
}

/** HR adds a resume + applying role for a candidate: creates the
 * application and, unless `runScreening` is false, runs the same AI
 * resume-vs-JD screening a self-service submission gets. Multipart,
 * because the resume travels with it. */
export function addApplicationWithResume(
  candidateId: string,
  payload: { jobId: string; resume: File; runScreening?: boolean },
  accessToken: string,
) {
  const form = new FormData();
  form.append("job_id", payload.jobId);
  form.append("run_screening", String(payload.runScreening ?? true));
  form.append("resume", payload.resume);
  return apiClient.postForm<HrApplicationWithResumeResponse>(
    `/api/v1/recruiter/candidates/${candidateId}/applications`,
    form,
    accessToken,
  );
}
