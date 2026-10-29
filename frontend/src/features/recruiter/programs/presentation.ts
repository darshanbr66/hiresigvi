import type { ProgramStatus } from "../../../types/program";

export const PROGRAM_STATUS_BADGE: Record<ProgramStatus, string> = {
  DRAFT: "badge-inactive",
  PUBLISHED: "badge-active",
  PAUSED: "badge-warn",
  CLOSED: "badge-inactive",
  ARCHIVED: "badge-inactive",
};

/** Application statuses that mean "selected through the program's process". */
export const SELECTED = ["SELECTED", "HIRED"];

/** Today as an ISO date (yyyy-mm-dd), for date inputs. */
export const today = () => new Date().toISOString().slice(0, 10);
