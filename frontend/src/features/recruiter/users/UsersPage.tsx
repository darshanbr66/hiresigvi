import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { ASSIGNABLE_ROLES, type AssignableRole, type UserResponse } from "../../../types/auth";
import { Alert } from "../../../shared/components/Alert";
import { EmptyState } from "../../../shared/components/EmptyState";
import { ConfirmDialog } from "../../../shared/components/ConfirmDialog";
import { Modal } from "../../../shared/components/Modal";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import { useAuth } from "../../auth/AuthContext";
import { createUser, deleteUser, listUsers, updateUser } from "../../auth/api";
import { DepartmentsHierarchy } from "../team/DepartmentsHierarchy";

const USERS_QUERY_KEY = ["recruiter", "users"];

type TeamTab = "accounts" | "departments";

export function UsersPage() {
  const { accessToken, user: currentUser } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [activeTab, setActiveTab] = useState<TeamTab>("departments");

  const usersQuery = useQuery({
    queryKey: USERS_QUERY_KEY,
    queryFn: () => listUsers(token),
    enabled: accessToken !== null,
  });

  const [showForm, setShowForm] = useState(false);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<AssignableRole>("RECRUITER");
  const [formError, setFormError] = useState<string | null>(null);

  const [pendingDeactivate, setPendingDeactivate] = useState<UserResponse | null>(null);
  const [deactivateReason, setDeactivateReason] = useState("");
  const [deactivateError, setDeactivateError] = useState<string | null>(null);
  const [pendingReactivate, setPendingReactivate] = useState<UserResponse | null>(null);
  const [pendingDelete, setPendingDelete] = useState<UserResponse | null>(null);
  const [deleteAcknowledged, setDeleteAcknowledged] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const createUserMutation = useMutation({
    mutationFn: () => createUser({ email, password, full_name: fullName, role }, token),
    onSuccess: () => {
      showToast(`Team member ${fullName} was added.`, "success");
      setEmail("");
      setFullName("");
      setPassword("");
      setRole("RECRUITER");
      setFormError(null);
      setShowForm(false);
      void queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createUserMutation.mutate();
  }

  function closeForm() {
    if (createUserMutation.isPending) return;
    setShowForm(false);
  }

  const roleMutation = useMutation({
    mutationFn: ({ userId, newRole }: { userId: string; newRole: AssignableRole }) =>
      updateUser(userId, { role: newRole }, token),
    onSuccess: (updated) => {
      showToast(`${updated.full_name}'s role is now ${updated.roles.join(", ")}.`, "success");
      void queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
    },
    onError: (err) => {
      showToast(
        err instanceof ApiError ? err.message : "Could not change that team member's role.",
        "error",
      );
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: () =>
      updateUser(
        pendingDeactivate!.id,
        { is_active: false, reason: deactivateReason.trim() || undefined },
        token,
      ),
    onSuccess: (updated) => {
      showToast(`${updated.full_name} was deactivated.`, "success");
      setPendingDeactivate(null);
      setDeactivateReason("");
      setDeactivateError(null);
      void queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
    },
    onError: (err) => {
      setDeactivateError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  const reactivateMutation = useMutation({
    mutationFn: () => updateUser(pendingReactivate!.id, { is_active: true }, token),
    onSuccess: (updated) => {
      showToast(`${updated.full_name} was reactivated.`, "success");
      setPendingReactivate(null);
      void queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
    },
    onError: (err) => {
      showToast(
        err instanceof ApiError ? err.message : "Could not reactivate that team member.",
        "error",
      );
      setPendingReactivate(null);
    },
  });

  // Permanent: only for a deactivated account, Org Admins only — the
  // backend enforces both, plus "never yourself / the last Org Admin" and
  // refusing an account that authored recruitment records.
  const deleteMutation = useMutation({
    mutationFn: (target: UserResponse) => deleteUser(target.id, token),
    onSuccess: (_result, target) => {
      showToast(`${target.full_name}'s account was permanently deleted.`, "success");
      queryClient.setQueryData<UserResponse[]>(USERS_QUERY_KEY, (current) =>
        current?.filter((member) => member.id !== target.id),
      );
      setPendingDelete(null);
      void queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    },
  });

  function openDeleteModal(teamUser: UserResponse) {
    setPendingDelete(teamUser);
    setDeleteAcknowledged(false);
    setDeleteError(null);
  }

  function closeDeleteModal() {
    if (deleteMutation.isPending) return;
    setPendingDelete(null);
  }

  function openDeactivateModal(teamUser: UserResponse) {
    setPendingDeactivate(teamUser);
    setDeactivateReason("");
    setDeactivateError(null);
  }

  function closeDeactivateModal() {
    if (deactivateMutation.isPending) return;
    setPendingDeactivate(null);
    setDeactivateReason("");
    setDeactivateError(null);
  }

  function handleDeactivateSubmit(event: FormEvent) {
    event.preventDefault();
    deactivateMutation.mutate();
  }

  // ORG_ADMIN and HR (RECRUITER) hold `user.read` together with
  // `user.create`/`user.update` (migration d7e8f9a0b1c2) — so a successful
  // list load already implies manage access too.
  const canManageUsers = !(usersQuery.error instanceof ApiError && usersQuery.error.status === 403);
  // Only an Org Admin (`user.manage_admins`) may create, promote, demote,
  // deactivate or reactivate an Org Admin. The backend enforces it; HR
  // simply isn't offered those controls.
  const isOrgAdmin = currentUser?.roles.includes("ORG_ADMIN") ?? false;
  const roleOptions = isOrgAdmin
    ? ASSIGNABLE_ROLES
    : ASSIGNABLE_ROLES.filter((option) => option !== "ORG_ADMIN");

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Team</h1>
          <p className="muted">
            {activeTab === "accounts"
              ? "Recruiters and admins who work in your organization."
              : "The organization hierarchy — departments and the employees within them."}
          </p>
        </div>
        {activeTab === "accounts" && canManageUsers && (
          <button type="button" className="btn btn-primary" onClick={() => setShowForm(true)}>
            + Add team member
          </button>
        )}
      </div>

      <div className="btn-group" role="tablist" aria-label="Team views">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "departments"}
          className={`btn btn-sm ${activeTab === "departments" ? "btn-primary" : "btn-ghost"}`}
          onClick={() => setActiveTab("departments")}
        >
          Departments
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "accounts"}
          className={`btn btn-sm ${activeTab === "accounts" ? "btn-primary" : "btn-ghost"}`}
          onClick={() => setActiveTab("accounts")}
        >
          Accounts
        </button>

      </div>

      {activeTab === "departments" && <DepartmentsHierarchy />}

      {activeTab === "accounts" && usersQuery.isPending && <SkeletonTable columns={6} />}

      {activeTab === "accounts" && usersQuery.isError && !canManageUsers && (
        <Alert>You do not have permission to view or manage team members.</Alert>
      )}

      {activeTab === "accounts" && usersQuery.isError && canManageUsers && (
        <Alert>
          {usersQuery.error instanceof ApiError
            ? usersQuery.error.message
            : "Could not load team members."}
        </Alert>
      )}

      {activeTab === "accounts" && usersQuery.isSuccess && (
        <section>
          {usersQuery.data.length === 0 ? (
            <EmptyState icon="user" title="No team members yet">
              Add your first teammate below.
            </EmptyState>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Status</th>
                    {canManageUsers && <th>Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {usersQuery.data.map((teamUser, index) => {
                    const isSelf = teamUser.id === currentUser?.id;
                    const currentRole = (teamUser.roles[0] as AssignableRole | undefined) ?? "RECRUITER";
                    const canManageRow =
                      canManageUsers && !isSelf && (isOrgAdmin || !teamUser.roles.includes("ORG_ADMIN"));
                    return (
                      <tr key={teamUser.id}>
                        <td>{index + 1}</td>
                        <td>
                          {teamUser.full_name}
                          {isSelf && <span className="muted"> (you)</span>}
                        </td>
                        <td>{teamUser.email}</td>
                        <td>
                          {canManageRow ? (
                            <select
                              value={currentRole}
                              aria-label={`Role for ${teamUser.full_name}`}
                              disabled={roleMutation.isPending}
                              onChange={(e) =>
                                roleMutation.mutate({
                                  userId: teamUser.id,
                                  newRole: e.target.value as AssignableRole,
                                })
                              }
                            >
                              {roleOptions.map((option) => (
                                <option key={option} value={option}>
                                  {option}
                                </option>
                              ))}
                            </select>
                          ) : (
                            teamUser.roles.join(", ") || "—"
                          )}
                        </td>
                        <td>
                          <span
                            className={`badge ${teamUser.is_active ? "badge-active" : "badge-inactive"}`}
                          >
                            {teamUser.is_active ? "Active" : "Inactive"}
                          </span>
                        </td>
                        {canManageUsers && (
                          <td>
                            {!canManageRow ? (
                              <span className="muted" style={{ fontSize: "0.8rem" }}>
                                —
                              </span>
                            ) : teamUser.is_active ? (
                              <button
                                type="button"
                                className="btn btn-danger btn-sm"
                                onClick={() => openDeactivateModal(teamUser)}
                              >
                                Deactivate
                              </button>
                            ) : (
                              <div className="btn-group">
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-sm"
                                  onClick={() => setPendingReactivate(teamUser)}
                                >
                                  Reactivate
                                </button>
                                {isOrgAdmin && (
                                  <button
                                    type="button"
                                    className="btn btn-danger btn-sm"
                                    onClick={() => openDeleteModal(teamUser)}
                                    aria-label={`Delete ${teamUser.full_name}`}
                                  >
                                    Delete
                                  </button>
                                )}
                              </div>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {canManageUsers && showForm && (
        <Modal title="Add a team member" onClose={closeForm}>
          <form onSubmit={handleSubmit} noValidate>
            <label className="field">
              <span>Full name</span>
              <input
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                disabled={createUserMutation.isPending}
              />
            </label>

            <label className="field">
              <span>Email</span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={createUserMutation.isPending}
              />
            </label>

            <label className="field">
              <span>Temporary password</span>
              <input
                type="password"
                required
                minLength={10}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={createUserMutation.isPending}
              />
              <span className="field-hint">At least 10 characters.</span>
            </label>

            <label className="field">
              <span>Role</span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as AssignableRole)}
                disabled={createUserMutation.isPending}
              >
                {roleOptions.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>

            {formError && <Alert>{formError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={createUserMutation.isPending}>
                {createUserMutation.isPending ? <Spinner label="Creating…" /> : "Create user"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeForm}
                disabled={createUserMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingDeactivate && (
        <Modal title="Deactivate team member" onClose={closeDeactivateModal}>
          <form onSubmit={handleDeactivateSubmit}>
            <p>
              <strong>Team member:</strong> {pendingDeactivate.full_name}
              <br />
              <strong>Email:</strong> {pendingDeactivate.email}
            </p>
            <p className="muted">
              This immediately revokes {pendingDeactivate.full_name}'s ability to sign in. Their
              past work (jobs, assessments, notes, etc.) is preserved, and this action is recorded
              in Activities.
            </p>
            <label className="field">
              <span>Reason (optional)</span>
              <textarea
                rows={3}
                value={deactivateReason}
                onChange={(e) => setDeactivateReason(e.target.value)}
                disabled={deactivateMutation.isPending}
                placeholder="e.g. Left the team"
              />
            </label>

            {deactivateError && <Alert>{deactivateError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-danger" disabled={deactivateMutation.isPending}>
                {deactivateMutation.isPending ? <Spinner label="Deactivating…" /> : "Deactivate"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeDeactivateModal}
                disabled={deactivateMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingDelete && (
        <Modal title="Delete account permanently" onClose={closeDeleteModal}>
          <form
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              if (!deleteAcknowledged) return;
              setDeleteError(null);
              deleteMutation.mutate(pendingDelete);
            }}
          >
            <p>
              <strong>Team member:</strong> {pendingDelete.full_name}
              <br />
              <strong>Email:</strong> {pendingDelete.email}
            </p>
            <Alert>
              This permanently removes {pendingDelete.full_name}'s account and cannot be undone.
              They can never sign in with it again.
            </Alert>
            <p className="muted">
              Their activity history and messages they sent are kept, and a linked employee record
              stays in the hierarchy without a login. An account that created jobs, assessments,
              campus drives, notes or calendar events can't be deleted — it stays deactivated so
              that history keeps its author. This action is recorded in Activities.
            </p>
            <label className="field field-inline">
              <input
                type="checkbox"
                checked={deleteAcknowledged}
                onChange={(e) => setDeleteAcknowledged(e.target.checked)}
                disabled={deleteMutation.isPending}
              />
              <span>I understand this cannot be undone</span>
            </label>

            {deleteError && <Alert>{deleteError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button
                type="submit"
                className="btn btn-danger"
                disabled={!deleteAcknowledged || deleteMutation.isPending}
              >
                {deleteMutation.isPending ? <Spinner label="Deleting…" /> : "Delete permanently"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={closeDeleteModal}
                disabled={deleteMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingReactivate && (
        <ConfirmDialog
          title="Reactivate this team member?"
          message={`${pendingReactivate.full_name} will be able to sign in again immediately.`}
          confirmLabel="Reactivate"
          danger={false}
          isConfirming={reactivateMutation.isPending}
          onCancel={() => setPendingReactivate(null)}
          onConfirm={() => reactivateMutation.mutate()}
        />
      )}
    </div>
  );
}
