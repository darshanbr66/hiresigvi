import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { EmptyState } from "../../../shared/components/EmptyState";
import { SkeletonList, SkeletonOrgChart } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import type {
  DepartmentResponse,
  EmployeeResponse,
} from "../../../types/teamHierarchy";
import { useAuth } from "../../auth/AuthContext";
import { OrgChart } from "./OrgChart";
import {
  createDepartment,
  createEmployee,
  deactivateEmployee,
  deleteDepartment,
  deleteEmployee,
  listDepartments,
  listEmployees,
  moveEmployee,
  reactivateEmployee,
  reorderEmployees,
  updateDepartment,
  updateEmployee,
} from "./api";
import { applyEmployeeOrder } from "./employeeOrder";

const DEPARTMENTS_KEY = ["recruiter", "departments"];
const EMPLOYEES_KEY = ["recruiter", "employees"];

const EMPTY_EMPLOYEE_FORM = {
  full_name: "",
  email: "",
  phone: "",
  designation: "",
  employee_code: "",
  location: "",
};

type EmployeeFormState = typeof EMPTY_EMPLOYEE_FORM;

/**
 * "<organization> → Departments → Employees" hierarchy view (SIGVITAS platform
 * overhaul § 9-12). Org Admin and HR (the RECRUITER role) get full CRUD;
 * Hiring Managers see the same tree read-only — the frontend hides manage
 * controls for UX only, the backend's `department.manage`/`employee.manage`
 * permissions are the actual authority (CLAUDE.md § 2).
 */
const MANAGE_ROLES = ["ORG_ADMIN", "RECRUITER"];

export function DepartmentsHierarchy() {
  const { accessToken, user: currentUser } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const canManage = currentUser?.roles.some((role) => MANAGE_ROLES.includes(role)) ?? false;

  const departmentsQuery = useQuery({
    queryKey: DEPARTMENTS_KEY,
    queryFn: () => listDepartments(token),
    enabled: accessToken !== null,
  });
  const employeesQuery = useQuery({
    queryKey: EMPLOYEES_KEY,
    queryFn: () => listEmployees(token),
    enabled: accessToken !== null,
  });

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showDeptForm, setShowDeptForm] = useState(false);
  const [editingDept, setEditingDept] = useState<DepartmentResponse | null>(null);
  const [deptName, setDeptName] = useState("");
  const [deptDescription, setDeptDescription] = useState("");
  const [deptFormError, setDeptFormError] = useState<string | null>(null);
  const [pendingDeleteDept, setPendingDeleteDept] = useState<DepartmentResponse | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [removingEmployee, setRemovingEmployee] = useState<EmployeeResponse | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [removeError, setRemoveError] = useState<string | null>(null);

  const [employeeFormDeptId, setEmployeeFormDeptId] = useState<string | null | undefined>(undefined);
  const [editingEmployee, setEditingEmployee] = useState<EmployeeResponse | null>(null);
  const [employeeForm, setEmployeeForm] = useState<EmployeeFormState>(EMPTY_EMPLOYEE_FORM);
  const [employeeFormError, setEmployeeFormError] = useState<string | null>(null);
  const [movingEmployee, setMovingEmployee] = useState<EmployeeResponse | null>(null);

  const employeesByDept = useMemo(() => {
    const map = new Map<string | null, EmployeeResponse[]>();
    for (const employee of employeesQuery.data ?? []) {
      const key = employee.department_id;
      const list = map.get(key) ?? [];
      list.push(employee);
      map.set(key, list);
    }
    return map;
  }, [employeesQuery.data]);

  function toggleExpanded(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function openCreateDept() {
    setEditingDept(null);
    setDeptName("");
    setDeptDescription("");
    setDeptFormError(null);
    setShowDeptForm(true);
  }

  function openEditDept(dept: DepartmentResponse) {
    setEditingDept(dept);
    setDeptName(dept.name);
    setDeptDescription(dept.description ?? "");
    setDeptFormError(null);
    setShowDeptForm(true);
  }

  const deptMutation = useMutation({
    mutationFn: () =>
      editingDept
        ? updateDepartment(editingDept.id, { name: deptName, description: deptDescription || null }, token)
        : createDepartment({ name: deptName, description: deptDescription || null }, token),
    onSuccess: () => {
      showToast(editingDept ? "Department updated." : "Department created.", "success");
      setShowDeptForm(false);
      void queryClient.invalidateQueries({ queryKey: DEPARTMENTS_KEY });
    },
    onError: (err) => {
      setDeptFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  const deleteDeptMutation = useMutation({
    mutationFn: () => deleteDepartment(pendingDeleteDept!.id, { reason: deleteReason.trim() }, token),
    onSuccess: () => {
      showToast(`${pendingDeleteDept?.name} was deleted.`, "success");
      setPendingDeleteDept(null);
      setDeleteReason("");
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: DEPARTMENTS_KEY });
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openCreateEmployee(departmentId: string | null) {
    setEditingEmployee(null);
    setEmployeeForm(EMPTY_EMPLOYEE_FORM);
    setEmployeeFormError(null);
    setEmployeeFormDeptId(departmentId);
  }

  function openEditEmployee(employee: EmployeeResponse) {
    setEditingEmployee(employee);
    setEmployeeForm({
      full_name: employee.full_name,
      email: employee.email,
      phone: employee.phone ?? "",
      designation: employee.designation ?? "",
      employee_code: employee.employee_code ?? "",
      location: employee.location ?? "",
    });
    setEmployeeFormError(null);
    setEmployeeFormDeptId(employee.department_id);
  }

  const employeeMutation = useMutation({
    mutationFn: () => {
      const shared = {
        full_name: employeeForm.full_name,
        email: employeeForm.email,
        phone: employeeForm.phone || null,
        designation: employeeForm.designation || null,
        employee_code: employeeForm.employee_code || null,
        location: employeeForm.location || null,
      };
      return editingEmployee
        ? updateEmployee(editingEmployee.id, shared, token)
        : createEmployee({ ...shared, department_id: employeeFormDeptId ?? null }, token);
    },
    onSuccess: () => {
      showToast(editingEmployee ? "Employee updated." : "Employee added.", "success");
      setEmployeeFormDeptId(undefined);
      setEditingEmployee(null);
      void queryClient.invalidateQueries({ queryKey: EMPLOYEES_KEY });
      void queryClient.invalidateQueries({ queryKey: DEPARTMENTS_KEY });
    },
    onError: (err) => {
      setEmployeeFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  const moveMutation = useMutation({
    mutationFn: (departmentId: string | null) => moveEmployee(movingEmployee!.id, { department_id: departmentId }, token),
    onSuccess: (updated) => {
      showToast(`${updated.full_name} moved to ${updated.department_name ?? "no department"}.`, "success");
      setMovingEmployee(null);
      void queryClient.invalidateQueries({ queryKey: EMPLOYEES_KEY });
      void queryClient.invalidateQueries({ queryKey: DEPARTMENTS_KEY });
    },
    onError: (err) => {
      showToast(err instanceof ApiError ? err.message : "Could not move that employee.", "error");
    },
  });

  // Reordering is optimistic: the chart reflects the drop immediately, the
  // order is persisted by the API, and if that fails the previous order is
  // put back and the user is told. Either way the list is refetched, so what
  // stays on screen is always what the server stored.
  const reorderMutation = useMutation({
    mutationFn: (orderedIds: string[]) => reorderEmployees(orderedIds, token),
    onMutate: async (orderedIds) => {
      await queryClient.cancelQueries({ queryKey: EMPLOYEES_KEY });
      const previous = queryClient.getQueryData<EmployeeResponse[]>(EMPLOYEES_KEY);
      if (previous) {
        queryClient.setQueryData(EMPLOYEES_KEY, applyEmployeeOrder(previous, orderedIds));
      }
      return { previous };
    },
    onError: (err, _orderedIds, context) => {
      if (context?.previous) queryClient.setQueryData(EMPLOYEES_KEY, context.previous);
      showToast(
        err instanceof ApiError ? err.message : "Could not save the new order. It was restored.",
        "error",
      );
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: EMPLOYEES_KEY });
    },
  });

  const statusMutation = useMutation({
    mutationFn: (employee: EmployeeResponse) =>
      employee.employment_status === "ACTIVE"
        ? deactivateEmployee(employee.id, {}, token)
        : reactivateEmployee(employee.id, token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: EMPLOYEES_KEY });
    },
    onError: (err) => {
      showToast(err instanceof ApiError ? err.message : "Could not update that employee.", "error");
    },
  });

  // Removing someone who left: a soft delete server-side, so they drop out of
  // every list and count here, while their record stays for history.
  const removeEmployeeMutation = useMutation({
    mutationFn: () =>
      deleteEmployee(removingEmployee!.id, { reason: removeReason.trim() || null }, token),
    onSuccess: (removed) => {
      showToast(`${removed.full_name} was removed.`, "success");
      setRemovingEmployee(null);
      setRemoveReason("");
      setRemoveError(null);
      void queryClient.invalidateQueries({ queryKey: EMPLOYEES_KEY });
      void queryClient.invalidateQueries({ queryKey: DEPARTMENTS_KEY });
    },
    onError: (err) => {
      setRemoveError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openRemoveEmployee(employee: EmployeeResponse) {
    setRemovingEmployee(employee);
    setRemoveReason("");
    setRemoveError(null);
  }

  function closeRemoveEmployee() {
    if (removeEmployeeMutation.isPending) return;
    setRemovingEmployee(null);
  }

  if (departmentsQuery.isPending || employeesQuery.isPending) {
    return (
      <div className="stack-lg" role="status" aria-label="Loading organization">
        <SkeletonOrgChart />
        <SkeletonList rows={3} />
      </div>
    );
  }

  if (departmentsQuery.isError) {
    return (
      <Alert>
        {departmentsQuery.error instanceof ApiError
          ? departmentsQuery.error.message
          : "Could not load the department hierarchy."}
      </Alert>
    );
  }

  const departments = departmentsQuery.data ?? [];
  const unassigned = employeesByDept.get(null) ?? [];

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <p className="muted" style={{ marginBottom: "0.25rem" }}>
            {currentUser?.organization_name ?? "Your organization"} &rarr; Departments
          </p>
          <p className="muted">Configurable departments and the employees within them.</p>
        </div>
        {canManage && (
          <button type="button" className="btn btn-primary" onClick={openCreateDept}>
            + New department
          </button>
        )}
      </div>

      <OrgChart
        organizationName={currentUser?.organization_name ?? "Your organization"}
        departments={departments}
        employees={employeesQuery.data ?? []}
        // Only offered to users who can manage employees; the backend
        // enforces `employee.manage` regardless.
        onReorder={canManage ? (_departmentId, orderedIds) => reorderMutation.mutate(orderedIds) : undefined}
        isSavingOrder={reorderMutation.isPending}
      />

      {departments.length === 0 ? (
        <EmptyState icon="team" title="No departments yet">
          Create your first department to start building the org chart.
        </EmptyState>
      ) : (
        <div className="stack-lg" style={{ gap: "0.75rem" }}>
          {departments.map((dept) => {
            const employees = employeesByDept.get(dept.id) ?? [];
            const isExpanded = expanded.has(dept.id);
            return (
              <section key={dept.id} className="card">
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    cursor: "pointer",
                  }}
                  onClick={() => toggleExpanded(dept.id)}
                >
                  <div>
                    <strong>{dept.name}</strong>{" "}
                    <span className="badge badge-inactive">{dept.employee_count} employee(s)</span>
                    {dept.description && <p className="muted">{dept.description}</p>}
                  </div>
                  <span aria-hidden="true">{isExpanded ? "▲" : "▼"}</span>
                </div>

                {isExpanded && (
                  <div className="stack-sm" style={{ marginTop: "1rem" }}>
                    {employees.length === 0 ? (
                      <p className="muted">No employees in this department yet.</p>
                    ) : (
                      <div className="table-scroll">
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th>Name</th>
                              <th>Designation</th>
                              <th>Email</th>
                              <th>Status</th>
                              {canManage && <th>Actions</th>}
                            </tr>
                          </thead>
                          <tbody>
                            {employees.map((employee) => (
                              <tr key={employee.id}>
                                <td>{employee.full_name}</td>
                                <td>{employee.designation ?? "—"}</td>
                                <td>{employee.email}</td>
                                <td>
                                  <span
                                    className={`badge ${employee.employment_status === "ACTIVE" ? "badge-active" : "badge-inactive"}`}
                                  >
                                    {employee.employment_status}
                                  </span>
                                </td>
                                {canManage && (
                                  <td>
                                    <div className="btn-group">
                                      <button
                                        type="button"
                                        className="btn btn-ghost btn-sm"
                                        onClick={() => openEditEmployee(employee)}
                                      >
                                        Edit
                                      </button>
                                      <button
                                        type="button"
                                        className="btn btn-ghost btn-sm"
                                        onClick={() => setMovingEmployee(employee)}
                                      >
                                        Move
                                      </button>
                                      <button
                                        type="button"
                                        className="btn btn-ghost btn-sm"
                                        disabled={statusMutation.isPending}
                                        onClick={() => statusMutation.mutate(employee)}
                                      >
                                        {employee.employment_status === "ACTIVE"
                                          ? "Deactivate"
                                          : "Reactivate"}
                                      </button>
                                      {currentUser?.id !== employee.user_id && (
                                        <button
                                          type="button"
                                          className="btn btn-danger btn-sm"
                                          onClick={() => openRemoveEmployee(employee)}
                                        >
                                          Remove
                                        </button>
                                      )}
                                    </div>
                                  </td>
                                )}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {canManage && (
                      <div className="btn-group">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => openCreateEmployee(dept.id)}
                        >
                          + Add employee
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => openEditDept(dept)}
                        >
                          Edit department
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger btn-sm"
                          onClick={() => {
                            setPendingDeleteDept(dept);
                            setDeleteReason("");
                            setDeleteError(null);
                          }}
                        >
                          Delete department
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      {unassigned.length > 0 && (
        <section className="card">
          <strong>Unassigned</strong>{" "}
          <span className="badge badge-inactive">{unassigned.length} employee(s)</span>
          <div className="table-scroll" style={{ marginTop: "0.75rem" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Designation</th>
                  <th>Email</th>
                  {canManage && <th>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {unassigned.map((employee) => (
                  <tr key={employee.id}>
                    <td>{employee.full_name}</td>
                    <td>{employee.designation ?? "—"}</td>
                    <td>{employee.email}</td>
                    {canManage && (
                      <td>
                        <div className="btn-group">
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => setMovingEmployee(employee)}
                          >
                            Assign to department
                          </button>
                          {currentUser?.id !== employee.user_id && (
                            <button
                              type="button"
                              className="btn btn-danger btn-sm"
                              onClick={() => openRemoveEmployee(employee)}
                            >
                              Remove
                            </button>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {canManage && (
        <button type="button" className="btn btn-ghost" onClick={() => openCreateEmployee(null)}>
          + Add employee (unassigned)
        </button>
      )}

      {showDeptForm && (
        <Modal
          title={editingDept ? `Edit ${editingDept.name}` : "Create a department"}
          onClose={() => setShowDeptForm(false)}
        >
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              deptMutation.mutate();
            }}
          >
            <label className="field">
              <span>Name</span>
              <input
                required
                value={deptName}
                onChange={(e) => setDeptName(e.target.value)}
                disabled={deptMutation.isPending}
              />
            </label>
            <label className="field">
              <span>Description</span>
              <textarea
                rows={2}
                value={deptDescription}
                onChange={(e) => setDeptDescription(e.target.value)}
                disabled={deptMutation.isPending}
              />
            </label>
            {deptFormError && <Alert>{deptFormError}</Alert>}
            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={deptMutation.isPending}>
                {deptMutation.isPending ? <Spinner label="Saving…" /> : "Save"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowDeptForm(false)}
                disabled={deptMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingDeleteDept && (
        <Modal title="Delete department" onClose={() => setPendingDeleteDept(null)}>
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              if (deleteReason.trim().length === 0) {
                setDeleteError("A reason for deletion is required.");
                return;
              }
              deleteDeptMutation.mutate();
            }}
          >
            <p className="muted">
              This removes "{pendingDeleteDept.name}" from your active department list. If any
              employees are still assigned to it, deletion is blocked until they're moved.
            </p>
            <label className="field">
              <span>Reason for deletion</span>
              <textarea
                required
                rows={2}
                value={deleteReason}
                onChange={(e) => {
                  setDeleteReason(e.target.value);
                  if (deleteError) setDeleteError(null);
                }}
                disabled={deleteDeptMutation.isPending}
              />
            </label>
            {deleteError && <Alert>{deleteError}</Alert>}
            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-danger" disabled={deleteDeptMutation.isPending}>
                {deleteDeptMutation.isPending ? <Spinner label="Deleting…" /> : "Delete department"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setPendingDeleteDept(null)}
                disabled={deleteDeptMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {removingEmployee && (
        <Modal title="Remove employee" onClose={closeRemoveEmployee}>
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              removeEmployeeMutation.mutate();
            }}
          >
            <p>
              <strong>Employee:</strong> {removingEmployee.full_name}
              <br />
              <strong>Email:</strong> {removingEmployee.email}
            </p>
            <p className="muted">
              Use this when {removingEmployee.full_name} has left the company. They will no longer
              appear in any department or in the organization chart. Their record is kept for
              history, and this action is recorded in Activities. A portal login, if they have
              one, is deactivated separately under Accounts.
            </p>
            <label className="field">
              <span>Reason (optional)</span>
              <textarea
                rows={2}
                value={removeReason}
                onChange={(e) => setRemoveReason(e.target.value)}
                disabled={removeEmployeeMutation.isPending}
                placeholder="e.g. Resigned"
              />
            </label>
            {removeError && <Alert>{removeError}</Alert>}
            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button
                type="submit"
                className="btn btn-danger"
                disabled={removeEmployeeMutation.isPending}
              >
                {removeEmployeeMutation.isPending ? <Spinner label="Removing…" /> : "Remove employee"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeRemoveEmployee}
                disabled={removeEmployeeMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {employeeFormDeptId !== undefined && (
        <Modal
          title={editingEmployee ? `Edit ${editingEmployee.full_name}` : "Add an employee"}
          onClose={() => setEmployeeFormDeptId(undefined)}
        >
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              employeeMutation.mutate();
            }}
          >
            <label className="field">
              <span>Full name</span>
              <input
                required
                value={employeeForm.full_name}
                onChange={(e) => setEmployeeForm({ ...employeeForm, full_name: e.target.value })}
                disabled={employeeMutation.isPending}
              />
            </label>
            <label className="field">
              <span>Email</span>
              <input
                required
                type="email"
                value={employeeForm.email}
                onChange={(e) => setEmployeeForm({ ...employeeForm, email: e.target.value })}
                disabled={employeeMutation.isPending}
              />
            </label>
            <div className="field-row">
              <label className="field">
                <span>Designation</span>
                <input
                  value={employeeForm.designation}
                  onChange={(e) => setEmployeeForm({ ...employeeForm, designation: e.target.value })}
                  disabled={employeeMutation.isPending}
                  placeholder="e.g. Software Engineer"
                />
              </label>
              <label className="field">
                <span>Phone</span>
                <input
                  value={employeeForm.phone}
                  onChange={(e) => setEmployeeForm({ ...employeeForm, phone: e.target.value })}
                  disabled={employeeMutation.isPending}
                />
              </label>
            </div>
            <div className="field-row">
              <label className="field">
                <span>Employee ID</span>
                <input
                  value={employeeForm.employee_code}
                  onChange={(e) => setEmployeeForm({ ...employeeForm, employee_code: e.target.value })}
                  disabled={employeeMutation.isPending}
                />
              </label>
              <label className="field">
                <span>Location</span>
                <input
                  value={employeeForm.location}
                  onChange={(e) => setEmployeeForm({ ...employeeForm, location: e.target.value })}
                  disabled={employeeMutation.isPending}
                />
              </label>
            </div>
            {employeeFormError && <Alert>{employeeFormError}</Alert>}
            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={employeeMutation.isPending}>
                {employeeMutation.isPending ? <Spinner label="Saving…" /> : "Save"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setEmployeeFormDeptId(undefined)}
                disabled={employeeMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {movingEmployee && (
        <Modal title={`Move ${movingEmployee.full_name}`} onClose={() => setMovingEmployee(null)}>
          <div className="stack-sm">
            <label className="field">
              <span>Department</span>
              <select
                defaultValue={movingEmployee.department_id ?? ""}
                onChange={(e) => moveMutation.mutate(e.target.value || null)}
                disabled={moveMutation.isPending}
              >
                <option value="">No department</option>
                {departments.map((dept) => (
                  <option key={dept.id} value={dept.id}>
                    {dept.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </Modal>
      )}
    </div>
  );
}
