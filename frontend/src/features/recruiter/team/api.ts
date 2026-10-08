import { apiClient } from "../../../lib/apiClient";
import type {
  DepartmentCreateRequest,
  DepartmentDeleteRequest,
  DepartmentResponse,
  DepartmentUpdateRequest,
  EmployeeCreateRequest,
  EmployeeDeactivateRequest,
  EmployeeDeleteRequest,
  EmployeeMoveRequest,
  EmployeeReorderRequest,
  EmployeeResponse,
  EmployeeUpdateRequest,
} from "../../../types/teamHierarchy";

export function listDepartments(accessToken: string) {
  return apiClient.get<DepartmentResponse[]>("/api/v1/recruiter/departments", accessToken);
}

export function createDepartment(payload: DepartmentCreateRequest, accessToken: string) {
  return apiClient.post<DepartmentResponse>("/api/v1/recruiter/departments", payload, accessToken);
}

export function updateDepartment(
  departmentId: string,
  payload: DepartmentUpdateRequest,
  accessToken: string,
) {
  return apiClient.patch<DepartmentResponse>(
    `/api/v1/recruiter/departments/${departmentId}`,
    payload,
    accessToken,
  );
}

export function deleteDepartment(
  departmentId: string,
  payload: DepartmentDeleteRequest,
  accessToken: string,
) {
  return apiClient.post<DepartmentResponse>(
    `/api/v1/recruiter/departments/${departmentId}/delete`,
    payload,
    accessToken,
  );
}

export function listEmployees(accessToken: string, departmentId?: string) {
  const query = departmentId ? `?department_id=${departmentId}` : "";
  return apiClient.get<EmployeeResponse[]>(`/api/v1/recruiter/employees${query}`, accessToken);
}

export function createEmployee(payload: EmployeeCreateRequest, accessToken: string) {
  return apiClient.post<EmployeeResponse>("/api/v1/recruiter/employees", payload, accessToken);
}

export function updateEmployee(
  employeeId: string,
  payload: EmployeeUpdateRequest,
  accessToken: string,
) {
  return apiClient.patch<EmployeeResponse>(
    `/api/v1/recruiter/employees/${employeeId}`,
    payload,
    accessToken,
  );
}

export function moveEmployee(employeeId: string, payload: EmployeeMoveRequest, accessToken: string) {
  return apiClient.post<EmployeeResponse>(
    `/api/v1/recruiter/employees/${employeeId}/move`,
    payload,
    accessToken,
  );
}

export function reorderEmployees(employeeIds: string[], accessToken: string) {
  const payload: EmployeeReorderRequest = { employee_ids: employeeIds };
  return apiClient.patch<EmployeeResponse[]>(
    "/api/v1/recruiter/employees/reorder",
    payload,
    accessToken,
  );
}

export function deactivateEmployee(
  employeeId: string,
  payload: EmployeeDeactivateRequest,
  accessToken: string,
) {
  return apiClient.post<EmployeeResponse>(
    `/api/v1/recruiter/employees/${employeeId}/deactivate`,
    payload,
    accessToken,
  );
}

/** Removes an employee who left the company (soft delete — the record is
 * kept for history but no longer appears in any employee or department list). */
export function deleteEmployee(
  employeeId: string,
  payload: EmployeeDeleteRequest,
  accessToken: string,
) {
  return apiClient.post<EmployeeResponse>(
    `/api/v1/recruiter/employees/${employeeId}/delete`,
    payload,
    accessToken,
  );
}

export function reactivateEmployee(employeeId: string, accessToken: string) {
  return apiClient.post<EmployeeResponse>(
    `/api/v1/recruiter/employees/${employeeId}/reactivate`,
    undefined,
    accessToken,
  );
}
