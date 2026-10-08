import { apiClient } from "../../lib/apiClient";
import type {
  OrganizationCreateRequest,
  OrganizationResponse,
  TokenResponse,
  UserCreateRequest,
  UserResponse,
  UserUpdateRequest,
} from "../../types/auth";

/**
 * Staff (User) auth + org/user administration — thin service layer over
 * apiClient per docs/architecture.md § 6. One module because login/me/
 * refresh/logout, org bootstrap, and team-user management are all "staff"
 * concerns sharing the same audience partition (recruiter + admin), even
 * though they hit two different route prefixes.
 */

export function login(email: string, password: string) {
  return apiClient.post<TokenResponse>("/api/v1/recruiter/auth/login", { email, password });
}

export function refreshSession() {
  return apiClient.post<TokenResponse>("/api/v1/recruiter/auth/refresh");
}

export function logout() {
  return apiClient.post<void>("/api/v1/recruiter/auth/logout");
}

export function fetchCurrentUser(accessToken: string) {
  return apiClient.get<UserResponse>("/api/v1/recruiter/auth/me", accessToken);
}

export function listUsers(accessToken: string) {
  return apiClient.get<UserResponse[]>("/api/v1/recruiter/users", accessToken);
}

export function createUser(payload: UserCreateRequest, accessToken: string) {
  return apiClient.post<UserResponse>("/api/v1/recruiter/users", payload, accessToken);
}

export function updateUser(userId: string, payload: UserUpdateRequest, accessToken: string) {
  return apiClient.patch<UserResponse>(`/api/v1/recruiter/users/${userId}`, payload, accessToken);
}

/** Permanently deletes a deactivated team account (Org Admins only — the
 * backend enforces every rule). */
export function deleteUser(userId: string, accessToken: string) {
  return apiClient.delete(`/api/v1/recruiter/users/${userId}`, accessToken);
}

export function listOrganizations(accessToken: string) {
  return apiClient.get<OrganizationResponse[]>("/api/v1/admin/organizations", accessToken);
}

export function createOrganization(payload: OrganizationCreateRequest, accessToken: string) {
  return apiClient.post<OrganizationResponse>("/api/v1/admin/organizations", payload, accessToken);
}

/** SUPER_ADMIN: company-level settings, e.g. the careers contact email. */
export function updateOrganizationSettings(
  organizationId: string,
  payload: { careers_contact_email: string | null },
  accessToken: string,
) {
  return apiClient.patch<OrganizationResponse>(
    `/api/v1/admin/organizations/${organizationId}`,
    payload,
    accessToken,
  );
}
