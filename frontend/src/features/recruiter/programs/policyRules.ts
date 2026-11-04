import {
  MAX_DURATION_MONTHS,
  MIN_DURATION_MONTHS,
  type EducationLevel,
  type StudyYearRules,
} from "../../../types/program";

/** Per-level study-year rules as form text: level -> "2" / "1, 2". A level
 * left empty has no rule of its own. */
export type StudyYearRuleText = Partial<Record<EducationLevel, string>>;

/** "6, 12" -> [6, 12]; anything that isn't a whole number is dropped. */
export function parseNumberList(text: string): number[] {
  return [
    ...new Set(
      text
        .split(/[\s,]+/)
        .filter(Boolean)
        .map(Number)
        .filter((n) => Number.isInteger(n)),
    ),
  ].sort((a, b) => a - b);
}

export function durationProblem(durations: number[]): string | null {
  return durations.some((d) => d < MIN_DURATION_MONTHS || d > MAX_DURATION_MONTHS)
    ? `Each duration must be between ${MIN_DURATION_MONTHS} and ${MAX_DURATION_MONTHS} months.`
    : null;
}

export function rulesToText(rules: StudyYearRules): StudyYearRuleText {
  return Object.fromEntries(
    Object.entries(rules).map(([level, years]) => [level, (years ?? []).join(", ")]),
  );
}

/** Only levels that are still eligible and have years entered are sent. */
export function textToRules(text: StudyYearRuleText, levels: EducationLevel[]): StudyYearRules {
  const rules: StudyYearRules = {};
  for (const level of levels) {
    const years = parseNumberList(text[level] ?? "");
    if (years.length) rules[level] = years;
  }
  return rules;
}
