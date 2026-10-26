import type { PublicJobDetail, PublicJobSummary } from "../../types/careers";
import {
  COMPENSATION_PERIOD_LABELS,
  EMPLOYMENT_TYPE_LABELS,
  WORK_MODE_LABELS,
} from "../../types/recruitment";

function money(value: string): string {
  const amount = Number(value);
  return Number.isFinite(amount) ? amount.toLocaleString() : value;
}

/** "INR 15,000 – 20,000 per month", or null when no pay is published. */
export function formatCompensation(job: PublicJobSummary): string | null {
  const { compensation_min: min, compensation_max: max } = job;
  if (!min && !max) return null;
  const range = min && max ? `${money(min)} – ${money(max)}` : min ? `From ${money(min)}` : `Up to ${money(max!)}`;
  const period = job.compensation_period ? ` ${COMPENSATION_PERIOD_LABELS[job.compensation_period]}` : "";
  return `${job.compensation_currency ?? ""} ${range}${period}`.trim();
}

/** The short facts a job card shows as chips, in reading order. */
export function jobChips(job: PublicJobSummary): string[] {
  const chips: string[] = [];
  if (job.employment_type) chips.push(EMPLOYMENT_TYPE_LABELS[job.employment_type]);
  if (job.work_mode) chips.push(WORK_MODE_LABELS[job.work_mode]);
  if (job.duration_months) chips.push(`${job.duration_months} month(s)`);
  if (job.hours_per_week) chips.push(`${job.hours_per_week} hrs/week`);
  if (job.employment_type === "INTERNSHIP" && job.is_paid === false) chips.push("Unpaid");
  const pay = formatCompensation(job);
  if (pay) chips.push(pay);
  return chips;
}

/** Labelled details for the job page; only what the recruiter filled in. */
export function jobDetailFacts(job: PublicJobDetail): { label: string; value: string }[] {
  const facts: { label: string; value: string }[] = [];
  const { min_experience_years: minExp, max_experience_years: maxExp } = job;
  if (minExp != null || maxExp != null) {
    facts.push({
      label: "Experience",
      value:
        minExp != null && maxExp != null
          ? `${minExp}–${maxExp} years`
          : minExp != null
            ? `${minExp}+ years`
            : `Up to ${maxExp} years`,
    });
  }
  if (job.work_schedule) facts.push({ label: "Schedule", value: job.work_schedule });
  if (job.conversion_to_full_time != null) {
    facts.push({ label: "Full-time conversion", value: job.conversion_to_full_time ? "Possible" : "No" });
  }
  if (job.expected_joining_date) {
    facts.push({ label: "Expected joining", value: new Date(job.expected_joining_date).toLocaleDateString() });
  }
  if (job.eligibility_criteria) facts.push({ label: "Eligibility", value: job.eligibility_criteria });
  if (job.employment_terms) facts.push({ label: "Terms", value: job.employment_terms });
  return facts;
}
