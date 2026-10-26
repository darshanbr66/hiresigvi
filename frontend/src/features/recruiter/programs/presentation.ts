import type { ProgramStatus } from "../../../types/program";

export const PROGRAM_STATUS_BADGE: Record<ProgramStatus, string> = {
  DRAFT: "badge-inactive",
  PUBLISHED: "badge-active",
  PAUSED: "badge-warn",
  CLOSED: "badge-inactive",
  ARCHIVED: "badge-inactive",
};
