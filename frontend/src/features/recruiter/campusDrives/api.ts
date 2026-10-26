import { apiClient } from "../../../lib/apiClient";
import type {
  AffiliationStatus,
  AffiliationVerificationResponse,
  CampusDriveCreateRequest,
  CampusDriveDeleteRequest,
  CampusDriveFunnelCounts,
  CampusDriveReportRow,
  CampusDriveResponse,
  CampusDriveUpdateRequest,
  RosterEntryResponse,
} from "../../../types/campusDrive";

export function listCampusDrives(accessToken: string) {
  return apiClient.get<CampusDriveResponse[]>("/api/v1/recruiter/campus-drives", accessToken);
}

export function getCampusDrive(driveId: string, accessToken: string) {
  return apiClient.get<CampusDriveResponse>(`/api/v1/recruiter/campus-drives/${driveId}`, accessToken);
}

export function createCampusDrive(payload: CampusDriveCreateRequest, accessToken: string) {
  return apiClient.post<CampusDriveResponse>("/api/v1/recruiter/campus-drives", payload, accessToken);
}

export function updateCampusDrive(
  driveId: string,
  payload: CampusDriveUpdateRequest,
  accessToken: string,
) {
  return apiClient.patch<CampusDriveResponse>(
    `/api/v1/recruiter/campus-drives/${driveId}`,
    payload,
    accessToken,
  );
}

export function getCampusDriveFunnel(driveId: string, accessToken: string) {
  return apiClient.get<CampusDriveFunnelCounts>(
    `/api/v1/recruiter/campus-drives/${driveId}/funnel`,
    accessToken,
  );
}

export function regenerateCampusDriveLink(driveId: string, accessToken: string) {
  return apiClient.post<CampusDriveResponse>(
    `/api/v1/recruiter/campus-drives/${driveId}/regenerate-link`,
    undefined,
    accessToken,
  );
}

export function deleteCampusDrive(
  driveId: string,
  payload: CampusDriveDeleteRequest,
  accessToken: string,
) {
  return apiClient.post<CampusDriveResponse>(
    `/api/v1/recruiter/campus-drives/${driveId}/delete`,
    payload,
    accessToken,
  );
}

export function getCampusReport(accessToken: string, institutionId?: string) {
  const query = institutionId ? `?institution_id=${encodeURIComponent(institutionId)}` : "";
  return apiClient.get<CampusDriveReportRow[]>(
    `/api/v1/recruiter/campus-drives/report${query}`,
    accessToken,
  );
}

export function listAffiliationVerifications(driveId: string, accessToken: string) {
  return apiClient.get<AffiliationVerificationResponse[]>(
    `/api/v1/recruiter/campus-drives/${driveId}/verifications`,
    accessToken,
  );
}

export function reviewAffiliation(
  driveId: string,
  verificationId: string,
  payload: { status: AffiliationStatus; note?: string | null },
  accessToken: string,
) {
  return apiClient.post<AffiliationVerificationResponse>(
    `/api/v1/recruiter/campus-drives/${driveId}/verifications/${verificationId}/review`,
    payload,
    accessToken,
  );
}

export function listRoster(driveId: string, accessToken: string) {
  return apiClient.get<RosterEntryResponse[]>(
    `/api/v1/recruiter/campus-drives/${driveId}/roster`,
    accessToken,
  );
}

export function addRosterEntries(
  driveId: string,
  entries: { email: string; student_identifier?: string | null; full_name?: string | null }[],
  accessToken: string,
) {
  return apiClient.post<{ added: number; total: number }>(
    `/api/v1/recruiter/campus-drives/${driveId}/roster`,
    { entries },
    accessToken,
  );
}

export function removeRosterEntry(driveId: string, entryId: string, accessToken: string) {
  return apiClient.delete(`/api/v1/recruiter/campus-drives/${driveId}/roster/${entryId}`, accessToken);
}
