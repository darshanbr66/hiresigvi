import type { ReactNode } from "react";
import { humanizeStatus, toneBadgeClass, type Tone } from "../../../shared/lib/statusTone";
import type { EnrollmentResponse, ProgramResponse } from "../../../types/program";

export function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card stack-sm" style={{ background: "var(--color-bg)" }}>
      <h3 style={{ margin: 0 }}>{title}</h3>
      {children}
    </section>
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="detail-row">
      <span className="detail-row-label">{label}</span>
      <span>{children}</span>
    </div>
  );
}

const STATE_TONES: Record<string, Tone> = {
  PENDING: "warn",
  PROPOSED: "warn",
  IN_REVIEW: "info",
  SENT: "info",
  SCHEDULED: "info",
  ENROLLED: "info",
  IN_PROGRESS: "brand",
  VERIFIED: "success",
  APPROVED: "success",
  ELIGIBLE: "success",
  COMPLETED: "success",
  SIGNED: "success",
  PAID: "success",
  FULLY_FUNDED: "success",
  ACCEPTED: "success",
  PASS: "success",
  PARTIALLY_PAID: "warn",
  UNPAID: "neutral",
  ON_HOLD: "warn",
  REJECTED: "danger",
  DECLINED: "danger",
  NOT_ELIGIBLE: "danger",
  NOT_PROCEEDING: "danger",
  OVERDUE: "danger",
  FAIL: "danger",
  TERMINATED: "danger",
  DISCONTINUED: "danger",
  WITHDRAWN: "neutral",
  VOID: "neutral",
  EXPIRED: "neutral",
  SUPERSEDED: "neutral",
  CANCELLED: "neutral",
  NO_SHOW: "danger",
};

const LABELS: Record<string, string> = {
  DISCONTINUED: "Terminated",
  SENT: "Awaiting acceptance",
  SIGNED: "Accepted (signed)",
  PENDING: "Pending",
  PROPOSED: "Awaiting approval",
};

export function StateBadge({ value, label }: { value: string; label?: string }) {
  return (
    <span className={toneBadgeClass(STATE_TONES[value] ?? "neutral")}>
      {label ?? LABELS[value] ?? humanizeStatus(value)}
    </span>
  );
}

/** What each section needs: the participant, the program, the viewer's
 * server-provided permissions, and one way to run a participant action. */
export interface SectionProps {
  e: EnrollmentResponse;
  program: ProgramResponse;
  busy: boolean;
  can: (permission: string) => boolean;
  run: (path: string, payload: Record<string, unknown>, message: string) => void;
}

