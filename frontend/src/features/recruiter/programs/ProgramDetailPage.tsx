import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { BackLink } from "../../../shared/components/BackLink";
import { ConfirmDialog } from "../../../shared/components/ConfirmDialog";
import { SkeletonLines } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import { humanizeStatus } from "../../../shared/lib/statusTone";
import {
  EDUCATION_LEVEL_LABELS,
  PROGRAM_STATUS_LABELS,
  PROGRAM_TRANSITIONS,
  type ProgramReport,
  type ProgramStatus,
} from "../../../types/program";
import { useAuth } from "../../auth/AuthContext";
import {
  changeProgramStatus,
  getProgram,
  getProgramReport,
  listEnrollments,
  updateProgram,
} from "./api";
import { EnrollmentPanel } from "./EnrollmentPanel";
import { ProgramForm, programToFormValue, toProgramPayload, type ProgramFormValue } from "./ProgramForm";
import { PROGRAM_STATUS_BADGE } from "./presentation";

const ACTION_LABELS: Record<ProgramStatus, string> = {
  DRAFT: "Back to draft",
  PUBLISHED: "Publish",
  PAUSED: "Pause",
  CLOSED: "Close",
  ARCHIVED: "Archive",
};

const REPORT_ITEMS: { key: keyof ProgramReport; label: string }[] = [
  { key: "applications", label: "Applications" },
  { key: "eligibility_pending", label: "Eligibility review" },
  { key: "eligible", label: "Eligible" },
  { key: "not_eligible", label: "Not eligible" },
  { key: "assessment_invited", label: "Test invited" },
  { key: "assessment_passed", label: "Test passed" },
  { key: "assessment_failed", label: "Test failed" },
  { key: "assessment_pending_review", label: "Test to grade" },
  { key: "interview", label: "In interview" },
  { key: "interview_completed", label: "Interviews completed" },
  { key: "selected", label: "Selected" },
  { key: "not_selected", label: "Not selected" },
  { key: "scholarship_eligible", label: "Scholarship eligible" },
  { key: "scholarship_pending", label: "Scholarship pending" },
  { key: "scholarship_approved", label: "Scholarship approved" },
  { key: "scholarship_declined", label: "Scholarship declined" },
  { key: "training_enrolled", label: "Training enrolled" },
  { key: "training_in_progress", label: "Training in progress" },
  { key: "training_completed", label: "Training completed" },
  { key: "offers_made", label: "Offers made" },
  { key: "offers_accepted", label: "Offers accepted" },
  { key: "joined", label: "Joined full-time" },
  { key: "agreements_signed", label: "Agreements signed" },
];

const money = (amount: string | null, currency: string | null) =>
  amount && currency ? `${currency} ${Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : null;

export function ProgramDetailPage() {
  const { programId = "" } = useParams<{ programId: string }>();
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const canManage = user?.roles.some((r) => ["ORG_ADMIN", "RECRUITER"].includes(r)) ?? false;
  const [editing, setEditing] = useState<ProgramFormValue | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<{ message: string; problems: string[] } | null>(null);
  const [confirming, setConfirming] = useState<ProgramStatus | null>(null);
  const [openEnrollment, setOpenEnrollment] = useState<string | null>(null);

  const key = ["recruiter", "programs", programId];
  const programQuery = useQuery({ queryKey: key, queryFn: () => getProgram(programId, token), enabled: accessToken !== null });
  const reportQuery = useQuery({
    queryKey: [...key, "report"],
    queryFn: () => getProgramReport(programId, token),
    enabled: accessToken !== null,
  });
  const enrollmentsQuery = useQuery({
    queryKey: [...key, "enrollments"],
    queryFn: () => listEnrollments(programId, token),
    enabled: accessToken !== null,
  });

  const saveMutation = useMutation({
    mutationFn: () => updateProgram(programId, toProgramPayload(editing!), token),
    onSuccess: (saved) => {
      queryClient.setQueryData(key, saved);
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "programs"] });
      setEditing(null);
      showToast("Program saved.", "success");
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  const statusMutation = useMutation({
    mutationFn: (status: ProgramStatus) => changeProgramStatus(programId, status, token),
    onSuccess: (saved) => {
      queryClient.setQueryData(key, saved);
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "programs"] });
      setConfirming(null);
      setStatusError(null);
      showToast(`Program is now ${PROGRAM_STATUS_LABELS[saved.status].toLowerCase()}.`, "success");
    },
    onError: (err) => {
      setConfirming(null);
      const problems = err instanceof ApiError && Array.isArray(err.data?.problems) ? (err.data!.problems as string[]) : [];
      setStatusError({ message: err instanceof ApiError ? err.message : "Unable to reach the server.", problems });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!editing?.title.trim() || !editing.description.trim()) {
      setFormError("A title and description are required.");
      return;
    }
    saveMutation.mutate();
  }

  if (programQuery.isPending) return <SkeletonLines count={8} />;
  if (programQuery.isError || !programQuery.data) {
    return (
      <Alert>{programQuery.error instanceof ApiError ? programQuery.error.message : "Program not found."}</Alert>
    );
  }
  const program = programQuery.data;
  const fee = money(program.training_fee_amount, program.training_fee_currency);
  const scholarshipLow = money(program.scholarship_amount_min, program.scholarship_currency);
  const scholarshipHigh = money(program.scholarship_amount_max, program.scholarship_currency);

  return (
    <div className="stack-lg">
      <p>
        <BackLink to="/recruiter/programs">Back to programs</BackLink>
      </p>
      <div className="page-header">
        <div>
          <h1>{program.title}</h1>
          <span className={`badge ${PROGRAM_STATUS_BADGE[program.status]}`}>{PROGRAM_STATUS_LABELS[program.status]}</span>
          {program.status === "PUBLISHED" && (
            <p className="field-hint" style={{ marginTop: "0.35rem" }}>
              Candidate page: <Link to={`/programs/${program.id}`}>/programs/{program.id}</Link>
            </p>
          )}
        </div>
        {canManage && !editing && (
          <div className="btn-group">
            {program.status !== "ARCHIVED" && (
              <button type="button" className="btn btn-ghost" onClick={() => { setFormError(null); setEditing(programToFormValue(program)); }}>
                Edit configuration
              </button>
            )}
            {PROGRAM_TRANSITIONS[program.status].map((next) => (
              <button
                key={next}
                type="button"
                className={`btn ${next === "PUBLISHED" ? "btn-primary" : "btn-ghost"}`}
                disabled={statusMutation.isPending}
                onClick={() => (next === "PUBLISHED" ? statusMutation.mutate(next) : setConfirming(next))}
              >
                {ACTION_LABELS[next]}
              </button>
            ))}
          </div>
        )}
      </div>

      {statusError && (
        <Alert>
          {statusError.problems.length > 0 ? (
            <>
              This program can't be published yet:
              <ul style={{ margin: "0.35rem 0 0", paddingLeft: "1.1rem" }}>
                {statusError.problems.map((p) => <li key={p}>{p}</li>)}
              </ul>
            </>
          ) : (
            statusError.message
          )}
        </Alert>
      )}

      {program.status === "DRAFT" && program.publication_problems.length > 0 && !editing && (
        <section className="card stack-sm" aria-label="Before publishing">
          <h2>Before this program can be published</h2>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {program.publication_problems.map((p) => <li key={p}>{p}</li>)}
          </ul>
          <p className="field-hint">
            Values the business hasn't finalized can stay empty — publish approved disclosure
            wording instead.
          </p>
        </section>
      )}

      {editing ? (
        <form onSubmit={submit} className="stack-lg">
          <ProgramForm value={editing} onChange={setEditing} disabled={saveMutation.isPending} accessToken={token} />
          {formError && <Alert>{formError}</Alert>}
          <div className="btn-group">
            <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
              {saveMutation.isPending ? <Spinner label="Saving…" /> : "Save configuration"}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setEditing(null)} disabled={saveMutation.isPending}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <section className="card stack-sm">
          <p>{program.description}</p>
          <div className="detail-row">
            <span className="detail-row-label">Eligibility</span>
            <span>
              {program.eligible_education_levels.map((l) => EDUCATION_LEVEL_LABELS[l]).join(", ") || "Not set"}
              {program.eligible_study_years.length ? ` · year ${program.eligible_study_years.join("/")}` : ""}
              {program.eligible_specializations.length ? ` · ${program.eligible_specializations.join(", ")}` : ""}
              {program.eligible_institutions.length ? ` · ${program.eligible_institutions.map((i) => i.name).join(", ")}` : ""}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-row-label">Training</span>
            <span>
              {program.training_duration_options.map((m) => `${m} months`).join(" or ") || "Duration not set"} ·{" "}
              {fee ? `fee ${fee}` : program.fee_disclosure ? `fee: "${program.fee_disclosure}"` : "fee not finalized"}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-row-label">Scholarship</span>
            <span>
              {scholarshipLow
                ? `${scholarshipLow}${scholarshipHigh && scholarshipHigh !== scholarshipLow ? ` – ${scholarshipHigh}` : ""}`
                : program.scholarship_disclosure
                  ? `"${program.scholarship_disclosure}"`
                  : "Amount not finalized"}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-row-label">Commitment</span>
            <span>
              {program.bond_duration_months ? `${program.bond_duration_months} months` : "Not set"}
              {program.bond_start_rule ? ` · starts: ${program.bond_start_rule}` : " · start rule not finalized"}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-row-label">Selection test</span>
            <span>{program.assessment_title ?? "None"}</span>
          </div>
          <div className="detail-row">
            <span className="detail-row-label">Applications</span>
            <span>
              {program.application_opens_at ? `open ${new Date(program.application_opens_at).toLocaleString()}` : "open on publish"}
              {program.application_closes_at ? ` · close ${new Date(program.application_closes_at).toLocaleString()}` : ""}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-row-label">Terms</span>
            <span>{program.terms_version ? `Version ${program.terms_version}` : "Not set"}</span>
          </div>
        </section>
      )}

      <section className="card">
        <h2>Program report</h2>
        {reportQuery.isSuccess ? (
          <div className="funnel-grid">
            {REPORT_ITEMS.map((item) => (
              <div key={item.key} className="funnel-stat">
                <span className="funnel-stat-value">{reportQuery.data[item.key]}</span>
                <span className="funnel-stat-label">{item.label}</span>
              </div>
            ))}
          </div>
        ) : (
          <p role="status">Loading report…</p>
        )}
      </section>

      <section className="card stack-sm">
        <h2>Participants</h2>
        {enrollmentsQuery.isPending && <p role="status">Loading…</p>}
        {enrollmentsQuery.isError && <Alert>Could not load participants.</Alert>}
        {enrollmentsQuery.isSuccess && enrollmentsQuery.data.length === 0 && (
          <p className="muted">No applications yet.</p>
        )}
        {enrollmentsQuery.isSuccess && enrollmentsQuery.data.length > 0 && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Education</th>
                  <th>Stage</th>
                  <th>Eligibility</th>
                  <th>Test</th>
                  <th>Scholarship</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {enrollmentsQuery.data.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <Link to={`/recruiter/applications/${e.application_id}`}>{e.candidate_full_name}</Link>
                      <span className="cell-sub">{e.candidate_email}</span>
                    </td>
                    <td>
                      {EDUCATION_LEVEL_LABELS[e.education_level]}
                      {e.study_year ? ` yr ${e.study_year}` : ""}
                      <span className="cell-sub">{e.specialization} · {e.institution_name}</span>
                    </td>
                    <td>{e.stage}</td>
                    <td>{humanizeStatus(e.eligibility_status)}</td>
                    <td>
                      {e.latest_result
                        ? e.latest_result.passed === null
                          ? "To grade"
                          : `${e.latest_result.passed ? "Passed" : "Failed"} (${e.latest_result.percentage}%)`
                        : "—"}
                    </td>
                    <td>{humanizeStatus(e.scholarship_status)}</td>
                    <td>
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpenEnrollment(e.id)}>
                        {canManage ? "Manage" : "View"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {confirming && (
        <ConfirmDialog
          title={`${ACTION_LABELS[confirming]} this program?`}
          message={
            confirming === "ARCHIVED"
              ? "Archived programs can't be edited or reopened. Participants' records are kept."
              : confirming === "CLOSED"
                ? "Candidates can no longer apply. Participants' records are kept, and you can reopen the program."
                : "Candidates can't apply while the program is paused."
          }
          confirmLabel={ACTION_LABELS[confirming]}
          isConfirming={statusMutation.isPending}
          onCancel={() => setConfirming(null)}
          onConfirm={() => statusMutation.mutate(confirming)}
        />
      )}

      {openEnrollment && (
        <EnrollmentPanel program={program} enrollmentId={openEnrollment} onClose={() => setOpenEnrollment(null)} />
      )}
    </div>
  );
}
