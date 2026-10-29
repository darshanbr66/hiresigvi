import { useState } from "react";
import type { TrainingStatus } from "../../../types/program";
import { Block, Row, StateBadge, type SectionProps } from "./panelParts";
import { SELECTED, today } from "./presentation";

const TRAINING_NEXT: Record<TrainingStatus, TrainingStatus[]> = {
  NOT_ENROLLED: ["ENROLLED"],
  ENROLLED: ["IN_PROGRESS", "WITHDRAWN", "DISCONTINUED"],
  IN_PROGRESS: ["COMPLETED", "WITHDRAWN", "DISCONTINUED"],
  COMPLETED: [],
  WITHDRAWN: [],
  DISCONTINUED: [],
};
const ACTION_LABEL: Partial<Record<TrainingStatus, string>> = {
  ENROLLED: "Enroll",
  IN_PROGRESS: "Mark started",
  COMPLETED: "Mark completed",
  WITHDRAWN: "Candidate withdrew",
  DISCONTINUED: "Terminate",
};

/** Full-time training (6 or 12 months) — a training-to-employment pathway,
 * not an internship — with milestones staff track to completion. */
export function TrainingSection({ e, program, busy, can, run }: SectionProps) {
  const [form, setForm] = useState({ duration: "", start: "", end: "", completedOn: "", note: "" });
  const [milestone, setMilestone] = useState({ title: "", due: "" });
  const manage = can("program.manage");
  const next = TRAINING_NEXT[e.training_status];
  const open = ["ENROLLED", "IN_PROGRESS"].includes(e.training_status);

  return (
    <Block title="Training (full-time)">
      <Row label="Status"><StateBadge value={e.training_status} /></Row>
      <Row label="Duration">{e.training_duration_months ? `${e.training_duration_months} months` : "—"}</Row>
      <Row label="Planned">{[e.training_start_date, e.training_end_date].filter(Boolean).join(" → ") || "—"}</Row>
      {e.training_enrolled_at && <Row label="Enrolled">{new Date(e.training_enrolled_at).toLocaleDateString()}</Row>}
      {e.training_completed_on && <Row label="Completed on">{e.training_completed_on}</Row>}
      {e.training_outcome_note && <Row label="Outcome note">{e.training_outcome_note}</Row>}

      {manage && SELECTED.includes(e.application_status) && next.length > 0 && (
        <>
          <div className="field-row">
            {e.training_status === "NOT_ENROLLED" && (
              <label className="field">
                <span>Duration</span>
                <select value={form.duration} onChange={(ev) => setForm({ ...form, duration: ev.target.value })} disabled={busy}>
                  <option value="">Choose…</option>
                  {program.training_duration_options.map((m) => <option key={m} value={m}>{m} months</option>)}
                </select>
              </label>
            )}
            {e.training_status !== "IN_PROGRESS" && (
              <label className="field">
                <span>Start date {e.training_status === "ENROLLED" ? "" : <span className="optional-mark">(Optional)</span>}</span>
                <input type="date" value={form.start} onChange={(ev) => setForm({ ...form, start: ev.target.value })} disabled={busy} />
              </label>
            )}
            {e.training_status === "NOT_ENROLLED" && (
              <label className="field">
                <span>Planned end <span className="optional-mark">(default: start + duration)</span></span>
                <input type="date" value={form.end} onChange={(ev) => setForm({ ...form, end: ev.target.value })} disabled={busy} />
              </label>
            )}
            {e.training_status === "IN_PROGRESS" && (
              <label className="field">
                <span>Completed on <span className="optional-mark">(to mark completed)</span></span>
                <input type="date" value={form.completedOn} max={today()} onChange={(ev) => setForm({ ...form, completedOn: ev.target.value })} disabled={busy} />
              </label>
            )}
          </div>
          <label className="field">
            <span>Note <span className="optional-mark">(required to withdraw or terminate)</span></span>
            <input value={form.note} onChange={(ev) => setForm({ ...form, note: ev.target.value })} disabled={busy} />
          </label>
          <div className="btn-group">
            {next.map((status) => (
              <button key={status} type="button" className="btn btn-ghost btn-sm"
                disabled={
                  busy ||
                  (status === "COMPLETED" && !form.completedOn) ||
                  (["WITHDRAWN", "DISCONTINUED"].includes(status) && !form.note.trim())
                }
                onClick={() => run("training", {
                  status,
                  duration_months: form.duration ? Number(form.duration) : null,
                  start_date: form.start || null,
                  end_date: form.end || null,
                  completed_on: status === "COMPLETED" ? form.completedOn : null,
                  note: form.note || null,
                }, `Training: ${(ACTION_LABEL[status] ?? status).toLowerCase()}.`)}>
                {ACTION_LABEL[status] ?? status}
              </button>
            ))}
          </div>
        </>
      )}

      <h4 style={{ margin: 0 }}>
        Milestones {e.milestones_overdue > 0 && <StateBadge value="OVERDUE" label={`${e.milestones_overdue} overdue`} />}
      </h4>
      {e.milestones.length === 0 && <p className="muted">No milestones recorded.</p>}
      {e.milestones.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
          {e.milestones.map((m) => (
            <li key={m.id}>
              <strong>{m.title}</strong>
              {m.due_on ? ` · due ${m.due_on}` : ""} · <StateBadge value={m.overdue ? "OVERDUE" : m.status} label={m.overdue ? "Overdue" : undefined} />
              {m.completed_on ? ` on ${m.completed_on}` : ""}
              {manage && open && m.status === "PENDING" && (
                <>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                    onClick={() => run(`milestones/${m.id}`, { status: "COMPLETED", completed_on: today() }, "Milestone completed.")}>
                    Completed today
                  </button>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                    onClick={() => run(`milestones/${m.id}`, { status: "MISSED" }, "Milestone marked missed.")}>
                    Missed
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {manage && open && (
        <div className="field-row">
          <label className="field">
            <span>New milestone</span>
            <input value={milestone.title} placeholder="e.g. Module 1 assessment" onChange={(ev) => setMilestone({ ...milestone, title: ev.target.value })} disabled={busy} />
          </label>
          <label className="field">
            <span>Due <span className="optional-mark">(Optional)</span></span>
            <input type="date" value={milestone.due} onChange={(ev) => setMilestone({ ...milestone, due: ev.target.value })} disabled={busy} />
          </label>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !milestone.title.trim()}
              onClick={() => {
                run("milestones", { title: milestone.title, due_on: milestone.due || null }, "Milestone added.");
                setMilestone({ title: "", due: "" });
              }}>
              Add
            </button>
          </div>
        </div>
      )}
    </Block>
  );
}
