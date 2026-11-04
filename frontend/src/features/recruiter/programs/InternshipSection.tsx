import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  INTERNSHIP_MODE_LABELS,
  INTERNSHIP_STATUS_LABELS,
  INTERNSHIP_TRANSITIONS,
  type InternshipMode,
  type InternshipStatus,
} from "../../../types/program";
import { listEmployees } from "../team/api";
import { Block, Row, StateBadge, type SectionProps } from "./panelParts";
import { SELECTED, today } from "./presentation";

const ACTION_LABEL: Partial<Record<InternshipStatus, string>> = {
  ACTIVE: "Mark started",
  COMPLETED: "Record completion",
  WITHDRAWN: "Candidate withdrew",
  TERMINATED: "Terminate",
};

function money(amount: string | null, currency: string | null): string {
  return amount && currency ? `${currency} ${Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—";
}

/** The participant's internship — its own record, separate from training
 * and employment. Duration and stipend are fixed from the program when it
 * is planned; an active internship can be extended with a reason, and
 * completing it records an evaluation that never decides employment by
 * itself. */
export function InternshipSection({ e, program, busy, can, run, token }: SectionProps & { token: string }) {
  const internship = e.internship;
  const manage = can("program.manage");
  const finance = can("program.finance.manage");
  const [plan, setPlan] = useState({
    duration: internship ? String(internship.duration_months) : program.internship_duration_months ? String(program.internship_duration_months) : "",
    start: internship?.start_date ?? "",
    mode: (internship?.mode ?? "") as InternshipMode | "",
    role: internship?.role_title ?? "",
    supervisor: internship?.supervisor_employee_id ?? "",
    stipend: "",
    stipendCurrency: program.internship_stipend_currency ?? "",
  });
  const [step, setStep] = useState({ onDate: "", outcome: "", rating: "", note: "" });
  const [extension, setExtension] = useState({ end: "", reason: "" });
  const editable = !internship || internship.status === "PLANNED";
  const employees = useQuery({
    queryKey: ["recruiter", "employees"],
    queryFn: () => listEmployees(token),
    enabled: manage && editable,
    retry: false,
  });
  const next = internship ? INTERNSHIP_TRANSITIONS[internship.status] : [];

  function savePlan() {
    run(
      "internship",
      {
        duration_months: plan.duration ? Number(plan.duration) : null,
        start_date: plan.start || null,
        mode: plan.mode || null,
        role_title: plan.role || null,
        supervisor_employee_id: plan.supervisor || null,
        ...(finance && plan.stipend
          ? { stipend_amount: plan.stipend, stipend_currency: plan.stipendCurrency || null }
          : {}),
      },
      internship ? "Internship plan updated." : "Internship planned.",
    );
  }

  return (
    <Block title="Internship">
      {!internship && <p className="muted">No internship planned yet.</p>}
      {internship && (
        <>
          <Row label="Status">
            <StateBadge value={internship.status} label={INTERNSHIP_STATUS_LABELS[internship.status]} />
          </Row>
          {internship.role_title && <Row label="Role">{internship.role_title}</Row>}
          <Row label="Duration">{internship.duration_months} months</Row>
          <Row label="Dates">
            {[internship.start_date, internship.end_date].filter(Boolean).join(" → ") || "—"}
            {internship.extension_count > 0 &&
              ` (extended ${internship.extension_count}×, originally ending ${internship.original_end_date})`}
          </Row>
          {internship.mode && <Row label="Mode">{INTERNSHIP_MODE_LABELS[internship.mode]}</Row>}
          {internship.supervisor_name && <Row label="Supervisor">{internship.supervisor_name}</Row>}
          {e.finance_visible && (
            <Row label="Stipend (monthly)">{money(internship.stipend_amount, internship.stipend_currency)}</Row>
          )}
          {internship.status === "COMPLETED" && (
            <Row label="Evaluation">
              <StateBadge
                value={internship.outcome === "QUALIFIED" ? "APPROVED" : "NOT_PROCEEDING"}
                label={internship.outcome === "QUALIFIED" ? "Qualified" : "Not qualified"}
              />
              {internship.evaluation_rating ? ` · rating ${internship.evaluation_rating}/5` : ""}
              {internship.completed_on ? ` · completed ${internship.completed_on}` : ""}
            </Row>
          )}
          {internship.evaluation_note && <Row label="Evaluation note">{internship.evaluation_note}</Row>}
          {internship.end_reason && <Row label="Ended">{`${internship.ended_on ?? ""} — ${internship.end_reason}`}</Row>}
        </>
      )}

      {manage && SELECTED.includes(e.application_status) && editable && (
        <div className="stack-sm">
          <div className="field-row">
            <label className="field">
              <span>Duration (months)</span>
              <input type="number" min={1} max={60} value={plan.duration} onChange={(ev) => setPlan({ ...plan, duration: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Planned start <span className="optional-mark">(Optional)</span></span>
              <input type="date" value={plan.start} onChange={(ev) => setPlan({ ...plan, start: ev.target.value })} disabled={busy} />
            </label>
            <label className="field">
              <span>Mode <span className="optional-mark">(Optional)</span></span>
              <select value={plan.mode} onChange={(ev) => setPlan({ ...plan, mode: ev.target.value as InternshipMode | "" })} disabled={busy}>
                <option value="">—</option>
                {(Object.keys(INTERNSHIP_MODE_LABELS) as InternshipMode[]).map((m) => (
                  <option key={m} value={m}>{INTERNSHIP_MODE_LABELS[m]}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="field-row">
            <label className="field">
              <span>Role <span className="optional-mark">(Optional)</span></span>
              <input value={plan.role} onChange={(ev) => setPlan({ ...plan, role: ev.target.value })} disabled={busy} placeholder="e.g. Research Intern" />
            </label>
            <label className="field">
              <span>Supervisor <span className="optional-mark">(Optional)</span></span>
              <select value={plan.supervisor} onChange={(ev) => setPlan({ ...plan, supervisor: ev.target.value })} disabled={busy}>
                <option value="">—</option>
                {employees.data?.map((emp) => (
                  <option key={emp.id} value={emp.id}>{emp.full_name}</option>
                ))}
              </select>
            </label>
          </div>
          {finance && (
            <div className="field-row">
              <label className="field">
                <span>Different stipend <span className="optional-mark">(Optional — default from the program)</span></span>
                <input type="number" min={0} value={plan.stipend} onChange={(ev) => setPlan({ ...plan, stipend: ev.target.value })} disabled={busy} />
              </label>
              <label className="field">
                <span>Currency</span>
                <input value={plan.stipendCurrency} onChange={(ev) => setPlan({ ...plan, stipendCurrency: ev.target.value })} disabled={busy} placeholder="INR" />
              </label>
            </div>
          )}
          <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}
            disabled={busy || !plan.duration} onClick={savePlan}>
            {internship ? "Save plan" : "Plan internship"}
          </button>
        </div>
      )}

      {manage && internship && next.length > 0 && (
        <div className="stack-sm">
          <div className="field-row">
            <label className="field">
              <span>{internship.status === "PLANNED" ? "Start date" : "Date"}</span>
              <input type="date" value={step.onDate} max={internship.status === "ACTIVE" ? today() : undefined}
                onChange={(ev) => setStep({ ...step, onDate: ev.target.value })} disabled={busy} />
            </label>
            {internship.status === "ACTIVE" && (
              <>
                <label className="field">
                  <span>Evaluation <span className="optional-mark">(to record completion)</span></span>
                  <select value={step.outcome} onChange={(ev) => setStep({ ...step, outcome: ev.target.value })} disabled={busy}>
                    <option value="">Choose…</option>
                    <option value="QUALIFIED">Qualified</option>
                    <option value="NOT_QUALIFIED">Not qualified</option>
                  </select>
                </label>
                <label className="field">
                  <span>Rating <span className="optional-mark">(1–5, optional)</span></span>
                  <input type="number" min={1} max={5} value={step.rating} onChange={(ev) => setStep({ ...step, rating: ev.target.value })} disabled={busy} />
                </label>
              </>
            )}
          </div>
          <label className="field">
            <span>Note <span className="optional-mark">(required to withdraw or terminate)</span></span>
            <input value={step.note} onChange={(ev) => setStep({ ...step, note: ev.target.value })} disabled={busy} />
          </label>
          <div className="btn-group">
            {next.map((status) => (
              <button key={status} type="button" className="btn btn-ghost btn-sm"
                disabled={
                  busy ||
                  (status === "ACTIVE" && !step.onDate && !internship.start_date) ||
                  (status === "COMPLETED" && (!step.onDate || !step.outcome)) ||
                  (["WITHDRAWN", "TERMINATED"].includes(status) && !step.note.trim())
                }
                onClick={() => run("internship/status", {
                  status,
                  on_date: step.onDate || null,
                  outcome: status === "COMPLETED" ? step.outcome : null,
                  rating: status === "COMPLETED" && step.rating ? Number(step.rating) : null,
                  note: step.note || null,
                }, `Internship: ${(ACTION_LABEL[status] ?? status).toLowerCase()}.`)}>
                {ACTION_LABEL[status] ?? status}
              </button>
            ))}
          </div>
        </div>
      )}

      {manage && internship?.status === "ACTIVE" && (
        <div className="field-row">
          <label className="field">
            <span>Extend to</span>
            <input type="date" value={extension.end} min={internship.end_date ?? undefined}
              onChange={(ev) => setExtension({ ...extension, end: ev.target.value })} disabled={busy} />
          </label>
          <label className="field">
            <span>Reason</span>
            <input value={extension.reason} onChange={(ev) => setExtension({ ...extension, reason: ev.target.value })} disabled={busy} />
          </label>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !extension.end || !extension.reason.trim()}
              onClick={() => {
                run("internship/extension", { new_end_date: extension.end, reason: extension.reason }, "Internship extended.");
                setExtension({ end: "", reason: "" });
              }}>
              Extend
            </button>
          </div>
        </div>
      )}

      {internship && internship.events.length > 0 && (
        <details>
          <summary>History ({internship.events.length})</summary>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {internship.events.map((ev) => (
              <li key={ev.id}>
                {new Date(ev.created_at).toLocaleString()} — {ev.event.replace(/_/g, " ").toLowerCase()}
                {ev.note ? `: ${ev.note}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Block>
  );
}
