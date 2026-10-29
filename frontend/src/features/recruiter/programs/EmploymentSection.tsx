import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { OfferStatus } from "../../../types/program";
import { listDepartments } from "../team/api";
import { Block, Row, StateBadge, type SectionProps } from "./panelParts";
import { SELECTED } from "./presentation";

const OFFER_NEXT: Record<OfferStatus, OfferStatus[]> = {
  NOT_OFFERED: ["OFFERED"],
  OFFERED: ["ACCEPTED", "DECLINED", "WITHDRAWN"],
  ACCEPTED: ["WITHDRAWN"],
  DECLINED: [],
  WITHDRAWN: [],
};

/** Training completed → employment review → approved decision (role,
 * department, start date) → offer → joining, which links (or creates) the
 * team-hierarchy employee record. Nothing here is automatic and completing
 * training never guarantees an offer. */
export function EmploymentSection({ e, program, busy, can, run, token }: SectionProps & { token: string }) {
  const [review, setReview] = useState({
    role: e.employment_role_title ?? "",
    department: e.employment_department_id ?? "",
    start: e.employment_proposed_start_date ?? "",
    note: "",
  });
  const [joinedOn, setJoinedOn] = useState("");
  const manage = can("program.manage");
  const approve = can("program.employment.approve");
  const departments = useQuery({
    queryKey: ["recruiter", "departments"],
    queryFn: () => listDepartments(token),
    enabled: approve,
    retry: false,
  });
  const ready = !program.tracks_training || e.training_status === "COMPLETED";
  const status = e.employment_review_status;
  const department = departments.data?.find((d) => d.id === e.employment_department_id);

  return (
    <Block title="Full-time employment">
      <Row label="Employment review"><StateBadge value={status} /></Row>
      {e.employment_role_title && <Row label="Intended role">{e.employment_role_title}</Row>}
      {department && <Row label="Department">{department.name}</Row>}
      {e.employment_proposed_start_date && <Row label="Proposed start">{e.employment_proposed_start_date}</Row>}
      {e.employment_decision_note && <Row label="Decision note">{e.employment_decision_note}</Row>}
      <Row label="Offer"><StateBadge value={e.offer_status} /></Row>
      <Row label="Joined">{e.joined_on ?? "—"}{e.employee_id ? " · employee record linked" : ""}</Row>

      {SELECTED.includes(e.application_status) && !ready && status === "NOT_STARTED" && (
        <p className="field-hint">The employment review starts once training is recorded as completed.</p>
      )}
      {manage && ready && SELECTED.includes(e.application_status) && ["NOT_STARTED", "NOT_PROCEEDING"].includes(status) && (
        <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} disabled={busy}
          onClick={() => run("employment-review", { status: "IN_REVIEW" }, "Employment review started.")}>
          {status === "NOT_PROCEEDING" ? "Reopen employment review" : "Start employment review"}
        </button>
      )}
      {approve && ["IN_REVIEW", "APPROVED"].includes(status) && e.offer_status === "NOT_OFFERED" && (
        <div className="stack-sm">
          {status === "IN_REVIEW" && (
            <div className="field-row">
              <label className="field">
                <span>Intended role</span>
                <input value={review.role} onChange={(ev) => setReview({ ...review, role: ev.target.value })} disabled={busy} />
              </label>
              {departments.data && (
                <label className="field">
                  <span>Department <span className="optional-mark">(Optional)</span></span>
                  <select value={review.department} onChange={(ev) => setReview({ ...review, department: ev.target.value })} disabled={busy}>
                    <option value="">—</option>
                    {departments.data.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                </label>
              )}
              <label className="field">
                <span>Proposed start date</span>
                <input type="date" value={review.start} onChange={(ev) => setReview({ ...review, start: ev.target.value })} disabled={busy} />
              </label>
            </div>
          )}
          <label className="field">
            <span>Decision note <span className="optional-mark">(required when not proceeding)</span></span>
            <input value={review.note} onChange={(ev) => setReview({ ...review, note: ev.target.value })} disabled={busy} />
          </label>
          <div className="btn-group">
            {status === "IN_REVIEW" && (
              <button type="button" className="btn btn-primary btn-sm" disabled={busy || !review.role.trim() || !review.start}
                onClick={() => run("employment-review", {
                  status: "APPROVED",
                  role_title: review.role,
                  department_id: review.department || null,
                  proposed_start_date: review.start,
                  note: review.note || null,
                }, "Employment approved.")}>
                Approve employment
              </button>
            )}
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy || !review.note.trim()}
              onClick={() => run("employment-review", { status: "NOT_PROCEEDING", note: review.note }, "Recorded as not proceeding.")}>
              Not proceeding
            </button>
          </div>
        </div>
      )}
      {manage && (
        <div className="btn-group">
          {OFFER_NEXT[e.offer_status]
            .filter((next) => next !== "OFFERED" || (status === "APPROVED" && e.application_status === "SELECTED"))
            .map((next) => (
              <button key={next} type="button" className="btn btn-ghost btn-sm" disabled={busy}
                onClick={() => run("offer", { status: next }, `Offer ${next.toLowerCase()}.`)}>
                {next === "OFFERED" ? "Record offer made" : `Offer ${next.toLowerCase()}`}
              </button>
            ))}
        </div>
      )}
      {manage && e.offer_status === "ACCEPTED" && !e.joined_on && (
        <div className="field-row">
          <label className="field">
            <span>Joining date</span>
            <input type="date" value={joinedOn} onChange={(ev) => setJoinedOn(ev.target.value)} disabled={busy} />
          </label>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="button" className="btn btn-primary btn-sm" disabled={busy || !joinedOn}
              onClick={() => run("joining", { joined_on: joinedOn }, "Joining recorded.")}>
              Record joining
            </button>
          </div>
        </div>
      )}
    </Block>
  );
}
