import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Modal } from "../../../shared/components/Modal";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import {
  INSTITUTION_TYPE_LABELS,
  type InstitutionResponse,
  type InstitutionType,
} from "../../../types/institution";
import { useAuth } from "../../auth/AuthContext";
import { createInstitution, listInstitutions, updateInstitution } from "./api";

const QUERY_KEY = ["recruiter", "institutions"];

interface FormState {
  name: string;
  institutionType: InstitutionType | "";
  city: string;
  state: string;
  country: string;
  website: string;
  emailDomains: string;
  contactName: string;
  contactEmail: string;
  contactPhone: string;
  notes: string;
}

const EMPTY: FormState = {
  name: "",
  institutionType: "",
  city: "",
  state: "",
  country: "",
  website: "",
  emailDomains: "",
  contactName: "",
  contactEmail: "",
  contactPhone: "",
  notes: "",
};

function toForm(institution: InstitutionResponse): FormState {
  return {
    name: institution.name,
    institutionType: institution.institution_type ?? "",
    city: institution.city ?? "",
    state: institution.state ?? "",
    country: institution.country ?? "",
    website: institution.website ?? "",
    emailDomains: institution.email_domains.join(", "),
    contactName: institution.placement_contact_name ?? "",
    contactEmail: institution.placement_contact_email ?? "",
    contactPhone: institution.placement_contact_phone ?? "",
    notes: institution.notes ?? "",
  };
}

function toPayload(form: FormState) {
  const text = (value: string) => value.trim() || null;
  return {
    name: form.name.trim(),
    institution_type: form.institutionType || null,
    city: text(form.city),
    state: text(form.state),
    country: text(form.country),
    website: text(form.website),
    email_domains: form.emailDomains.split(/[\s,]+/).map((d) => d.trim()).filter(Boolean),
    placement_contact_name: text(form.contactName),
    placement_contact_email: text(form.contactEmail),
    placement_contact_phone: text(form.contactPhone),
    notes: text(form.notes),
  };
}

/** The organization's registry of colleges and universities that campus
 * drives and recruitment programs refer to. */
export function InstitutionsPage() {
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  // Changing the registry needs `campus_drive.manage` (ORG_ADMIN, RECRUITER);
  // the server enforces it, this only hides controls that would be refused.
  const canManage = user?.roles.some((r) => ["ORG_ADMIN", "RECRUITER"].includes(r)) ?? false;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<InstitutionResponse | "new" | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [formError, setFormError] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: [...QUERY_KEY, search],
    queryFn: () => listInstitutions(token, { q: search.trim() || undefined }),
    enabled: accessToken !== null,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      editing === "new"
        ? createInstitution(toPayload(form), token)
        : updateInstitution((editing as InstitutionResponse).id, toPayload(form), token),
    onSuccess: (saved) => {
      showToast(`${saved.name} saved.`, "success");
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  const activeMutation = useMutation({
    mutationFn: (institution: InstitutionResponse) =>
      updateInstitution(institution.id, { is_active: !institution.is_active }, token),
    onSuccess: (saved) => {
      showToast(`${saved.name} is now ${saved.is_active ? "active" : "inactive"}.`, "success");
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
    onError: (err) => showToast(err instanceof ApiError ? err.message : "Could not update.", "error"),
  });

  function open(target: InstitutionResponse | "new") {
    setEditing(target);
    setForm(target === "new" ? EMPTY : toForm(target));
    setFormError(null);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!form.name.trim()) {
      setFormError("Institution name is required.");
      return;
    }
    saveMutation.mutate();
  }

  const forbidden = listQuery.error instanceof ApiError && listQuery.error.status === 403;
  const field = (key: keyof FormState, label: string, props: Record<string, unknown> = {}) => (
    <label className="field">
      <span>
        {label} {key !== "name" && <span className="optional-mark">(Optional)</span>}
      </span>
      <input
        value={form[key]}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
        disabled={saveMutation.isPending}
        {...props}
      />
    </label>
  );

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Institutions</h1>
          <p className="muted">
            Colleges and universities you hire from. Campus drives and programs pick from this list,
            so each institution is recorded once.
          </p>
        </div>
        {canManage && !forbidden && (
          <button type="button" className="btn btn-primary" onClick={() => open("new")}>
            + New institution
          </button>
        )}
      </div>

      <label className="field" style={{ maxWidth: "24rem" }}>
        <span>Search institutions</span>
        <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name or city" />
      </label>

      {listQuery.isPending && <SkeletonTable columns={5} />}
      {forbidden && <Alert>You do not have permission to view institutions.</Alert>}
      {listQuery.isError && !forbidden && <Alert>Could not load institutions.</Alert>}
      {listQuery.isSuccess &&
        (listQuery.data.length === 0 ? (
          <EmptyState icon="institution" title={search ? "No matching institutions" : "No institutions yet"}>
            Register the colleges you run campus drives with.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Institution</th>
                  <th>Location</th>
                  <th>Email domain(s)</th>
                  <th>Placement contact</th>
                  <th>Drives</th>
                  <th>Status</th>
                  {canManage && <th>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {listQuery.data.map((institution) => (
                  <tr key={institution.id}>
                    <td>
                      {institution.name}
                      {institution.institution_type && (
                        <span className="cell-sub">{INSTITUTION_TYPE_LABELS[institution.institution_type]}</span>
                      )}
                    </td>
                    <td>{[institution.city, institution.state, institution.country].filter(Boolean).join(", ") || "—"}</td>
                    <td>{institution.email_domains.join(", ") || "—"}</td>
                    <td>
                      {institution.placement_contact_name ?? "—"}
                      {institution.placement_contact_email && (
                        <span className="cell-sub">{institution.placement_contact_email}</span>
                      )}
                    </td>
                    <td>
                      {institution.campus_drive_count > 0 ? (
                        <Link to="/recruiter/campus-drives">{institution.campus_drive_count}</Link>
                      ) : (
                        0
                      )}
                    </td>
                    <td>
                      <span className={`badge ${institution.is_active ? "badge-active" : "badge-inactive"}`}>
                        {institution.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    {canManage && (
                      <td>
                        <div className="btn-group">
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => open(institution)}>
                            Edit
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => activeMutation.mutate(institution)}
                            disabled={activeMutation.isPending}
                          >
                            {institution.is_active ? "Deactivate" : "Reactivate"}
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {editing && (
        <Modal
          title={editing === "new" ? "Register an institution" : `Edit ${editing.name}`}
          onClose={() => !saveMutation.isPending && setEditing(null)}
          wide
        >
          <form onSubmit={submit} className="stack-sm">
            {field("name", "Institution name", { required: true })}
            <div className="field-row">
              <label className="field">
                <span>
                  Type <span className="optional-mark">(Optional)</span>
                </span>
                <select
                  value={form.institutionType}
                  onChange={(e) => setForm({ ...form, institutionType: e.target.value as InstitutionType | "" })}
                  disabled={saveMutation.isPending}
                >
                  <option value="">Not specified</option>
                  {(Object.keys(INSTITUTION_TYPE_LABELS) as InstitutionType[]).map((type) => (
                    <option key={type} value={type}>
                      {INSTITUTION_TYPE_LABELS[type]}
                    </option>
                  ))}
                </select>
              </label>
              {field("website", "Website", { type: "url", placeholder: "https://college.edu" })}
            </div>
            <div className="field-row">
              {field("city", "City")}
              {field("state", "State")}
              {field("country", "Country")}
            </div>
            <label className="field">
              <span>
                Official student email domain(s) <span className="optional-mark">(Optional)</span>
              </span>
              <input
                value={form.emailDomains}
                onChange={(e) => setForm({ ...form, emailDomains: e.target.value })}
                placeholder="college.edu, students.college.edu"
                disabled={saveMutation.isPending}
              />
              <span className="field-hint">
                Only needed for email-domain verification on campus drives. Many institutions have
                none — leave it empty.
              </span>
            </label>
            <div className="field-row">
              {field("contactName", "Placement contact")}
              {field("contactEmail", "Contact email", { type: "email" })}
              {field("contactPhone", "Contact phone", { type: "tel" })}
            </div>
            <label className="field">
              <span>
                Notes <span className="optional-mark">(Optional)</span>
              </span>
              <textarea
                rows={2}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                disabled={saveMutation.isPending}
              />
            </label>
            {formError && <Alert>{formError}</Alert>}
            <div className="btn-group">
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? <Spinner label="Saving…" /> : "Save institution"}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setEditing(null)} disabled={saveMutation.isPending}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
