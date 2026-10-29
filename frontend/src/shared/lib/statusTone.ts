/**
 * One mapping from an application status to its recruitment-pipeline stage,
 * and from a stage to its colour — used everywhere a stage appears (badges,
 * pipeline bars, timelines, legends), so a status never changes colour
 * between pages. The status always comes from the API value, never from a
 * display label; an unknown or newly added status falls back to neutral.
 *
 * Stages: applied (neutral) → screening (amber) → assessment (blue) →
 * interview (violet) → selected / hired (green); rejected (red); hold
 * (muted amber — the advisory AI_SCREENED_OUT, which HR can override).
 * Colour is never the only signal: every use keeps the stage name visible.
 */
export type Tone = "neutral" | "info" | "brand" | "success" | "danger" | "accent" | "warn" | "interview";

export type PipelineStage =
  | "applied"
  | "screening"
  | "assessment"
  | "interview"
  | "selected"
  | "hired"
  | "rejected"
  | "hold";

const STAGE_OF: Record<string, PipelineStage> = {
  APPLIED: "applied",
  AI_SCREENED_OUT: "hold",
  UNDER_REVIEW: "screening",
  SCREENING: "screening",
  ASSESSMENT_INVITED: "assessment",
  ASSESSMENT_STARTED: "assessment",
  ASSESSMENT_COMPLETED: "assessment",
  SHORTLISTED: "interview",
  INTERVIEW: "interview",
  SELECTED: "selected",
  REJECTED: "rejected",
  HIRED: "hired",
};

export const PIPELINE_STAGE_LABELS: Record<PipelineStage, string> = {
  applied: "Applied",
  screening: "Screening",
  assessment: "Assessment",
  interview: "Interview",
  selected: "Selected",
  hired: "Hired",
  rejected: "Rejected",
  hold: "On hold",
};

/** The pipeline stage of an API status; null when the status is unknown. */
export function pipelineStage(status: string): PipelineStage | null {
  return STAGE_OF[status] ?? null;
}

/** A CSS colour (theme-aware token) for a status's stage — neutral fallback. */
export function pipelineColor(status: string): string {
  const stage = pipelineStage(status);
  return stage ? `var(--pipeline-${stage})` : "var(--pipeline-applied)";
}

const STAGE_TONE: Record<PipelineStage, Tone> = {
  applied: "neutral",
  screening: "warn",
  assessment: "info",
  interview: "interview",
  selected: "success",
  hired: "success",
  rejected: "danger",
  hold: "warn",
};

export function statusTone(status: string): Tone {
  const stage = pipelineStage(status);
  return stage ? STAGE_TONE[stage] : "neutral";
}

const BADGE_CLASS: Record<Tone, string> = {
  neutral: "badge-inactive",
  info: "badge-info",
  brand: "badge-info",
  success: "badge-active",
  danger: "badge-danger",
  accent: "badge-accent",
  warn: "badge-warn",
  interview: "badge-interview",
};

export function toneBadgeClass(tone: Tone): string {
  return `badge ${BADGE_CLASS[tone]}`;
}

const TONE_COLOR: Record<Tone, string> = {
  neutral: "var(--color-text-muted)",
  info: "var(--pipeline-assessment)",
  brand: "var(--color-primary)",
  success: "var(--color-active-text)",
  danger: "var(--color-danger)",
  accent: "var(--color-accent)",
  warn: "var(--color-warn-text)",
  interview: "var(--pipeline-interview)",
};

/** A CSS colour for fills (bars, dots) — theme-aware through the tokens. */
export function toneColor(tone: Tone): string {
  return TONE_COLOR[tone];
}

const STATUS_LABELS: Record<string, string> = {
  AI_SCREENED_OUT: "AI screened out",
  HR_MATCH: "HR match",
};

/** "UNDER_REVIEW" -> "Under review". */
export function humanizeStatus(status: string): string {
  if (STATUS_LABELS[status]) return STATUS_LABELS[status];
  const text = status.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
