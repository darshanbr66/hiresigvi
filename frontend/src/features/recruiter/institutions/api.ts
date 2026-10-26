import { apiClient } from "../../../lib/apiClient";
import type {
  InstitutionCreateRequest,
  InstitutionResponse,
  InstitutionUpdateRequest,
} from "../../../types/institution";

export function listInstitutions(
  accessToken: string,
  options: { q?: string; activeOnly?: boolean } = {},
) {
  const params = new URLSearchParams();
  if (options.q) params.set("q", options.q);
  if (options.activeOnly) params.set("active_only", "true");
  const query = params.toString();
  return apiClient.get<InstitutionResponse[]>(
    `/api/v1/recruiter/institutions${query ? `?${query}` : ""}`,
    accessToken,
  );
}

export function createInstitution(payload: InstitutionCreateRequest, accessToken: string) {
  return apiClient.post<InstitutionResponse>("/api/v1/recruiter/institutions", payload, accessToken);
}

export function updateInstitution(
  institutionId: string,
  payload: InstitutionUpdateRequest,
  accessToken: string,
) {
  return apiClient.patch<InstitutionResponse>(
    `/api/v1/recruiter/institutions/${institutionId}`,
    payload,
    accessToken,
  );
}
