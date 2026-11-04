import { EDUCATION_LEVEL_LABELS, type EducationLevel } from "../../../types/program";
import type { StudyYearRuleText } from "./policyRules";

/** One "year(s) of study" input per eligible education level. */
export function StudyYearRulesInput({
  levels,
  value,
  onChange,
  disabled,
  fallbackHint,
}: {
  levels: EducationLevel[];
  value: StudyYearRuleText;
  onChange: (value: StudyYearRuleText) => void;
  disabled: boolean;
  /** What applies to a level left empty. */
  fallbackHint: string;
}) {
  if (!levels.length) {
    return <p className="field-hint">Choose eligible education levels to set study-year rules per level.</p>;
  }
  return (
    <fieldset className="field">
      <legend>Eligible year(s) of study per level</legend>
      <div className="field-row">
        {levels.map((level) => (
          <label key={level} className="field">
            <span>{EDUCATION_LEVEL_LABELS[level]}</span>
            <input
              value={value[level] ?? ""}
              onChange={(e) => onChange({ ...value, [level]: e.target.value })}
              disabled={disabled}
              placeholder="e.g. 2"
              aria-label={`Eligible study years for ${EDUCATION_LEVEL_LABELS[level]}`}
            />
          </label>
        ))}
      </div>
      <span className="field-hint">Comma-separated years. {fallbackHint}</span>
    </fieldset>
  );
}
