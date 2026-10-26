import {
  COMPENSATION_PERIOD_LABELS,
  EMPLOYMENT_TYPE_FIELDS,
  WORK_MODE_LABELS,
  type CompensationPeriod,
  type EmploymentDetailField,
  type EmploymentDetails,
  type EmploymentType,
  type JobResponse,
  type WorkMode,
} from "../../../types/recruitment";

/** Every detail as the form edits it: inputs hold strings, and the two
 * yes/no details use "" (not stated), "true" or "false". */
export type EmploymentDetailsForm = Record<EmploymentDetailField, string>;

const DETAIL_FIELDS: EmploymentDetailField[] = [
  "work_mode",
  "duration_months",
  "is_paid",
  "compensation_min",
  "compensation_max",
  "compensation_currency",
  "compensation_period",
  "hours_per_week",
  "work_schedule",
  "min_experience_years",
  "max_experience_years",
  "eligibility_criteria",
  "conversion_to_full_time",
  "expected_joining_date",
  "employment_terms",
];

const INTEGER_FIELDS = new Set<EmploymentDetailField>([
  "duration_months",
  "hours_per_week",
  "min_experience_years",
  "max_experience_years",
]);
const BOOLEAN_FIELDS = new Set<EmploymentDetailField>(["is_paid", "conversion_to_full_time"]);
const COMPENSATION_FIELDS: EmploymentDetailField[] = [
  "compensation_min",
  "compensation_max",
  "compensation_currency",
  "compensation_period",
];

export const EMPTY_DETAILS: EmploymentDetailsForm = Object.fromEntries(
  DETAIL_FIELDS.map((field) => [field, ""]),
) as EmploymentDetailsForm;

export function detailsFromJob(job: JobResponse): EmploymentDetailsForm {
  return Object.fromEntries(
    DETAIL_FIELDS.map((field) => {
      const value = job[field];
      return [field, value === null || value === undefined ? "" : String(value)];
    }),
  ) as EmploymentDetailsForm;
}

/** Hides the stipend for an unpaid internship — the server rejects one. */
function hiddenFields(type: EmploymentType, form: EmploymentDetailsForm): Set<EmploymentDetailField> {
  return new Set(type === "INTERNSHIP" && form.is_paid === "false" ? COMPENSATION_FIELDS : []);
}

/** The details that apply to `type`, typed for the API. Empty inputs are
 * sent as null so an edit can clear a value; details that don't apply are
 * left out (the server drops leftovers from the job's previous type). */
export function detailsPayload(
  type: EmploymentType,
  form: EmploymentDetailsForm,
): Partial<EmploymentDetails> {
  const hidden = hiddenFields(type, form);
  const payload: Record<string, string | number | boolean | null> = {};
  for (const field of EMPLOYMENT_TYPE_FIELDS[type]) {
    const raw = hidden.has(field) ? "" : form[field].trim();
    if (raw === "") payload[field] = null;
    else if (INTEGER_FIELDS.has(field)) payload[field] = Number(raw);
    else if (BOOLEAN_FIELDS.has(field)) payload[field] = raw === "true";
    else if (field === "compensation_currency") payload[field] = raw.toUpperCase();
    else payload[field] = raw;
  }
  return payload as Partial<EmploymentDetails>;
}

const PAY_LABEL: Record<EmploymentType, string> = {
  INTERNSHIP: "Stipend",
  PART_TIME: "Pay",
  FULL_TIME: "Salary",
};

export function EmploymentDetailsFields({
  type,
  value,
  onChange,
  disabled,
}: {
  type: EmploymentType;
  value: EmploymentDetailsForm;
  onChange: (next: EmploymentDetailsForm) => void;
  disabled: boolean;
}) {
  const applies = new Set(EMPLOYMENT_TYPE_FIELDS[type]);
  const hidden = hiddenFields(type, value);
  const shows = (field: EmploymentDetailField) => applies.has(field) && !hidden.has(field);
  const set = (field: EmploymentDetailField) => (
    e: { target: { value: string } },
  ) => onChange({ ...value, [field]: e.target.value });
  const pay = PAY_LABEL[type];

  return (
    <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: "0.5rem 0 0" }}>
      <legend className="muted" style={{ padding: 0 }}>
        Details (optional)
      </legend>

      <div className="field-row">
        <label className="field">
          <span>Work mode</span>
          <select value={value.work_mode} onChange={set("work_mode")} disabled={disabled}>
            <option value="">Not specified</option>
            {(Object.keys(WORK_MODE_LABELS) as WorkMode[]).map((mode) => (
              <option key={mode} value={mode}>
                {WORK_MODE_LABELS[mode]}
              </option>
            ))}
          </select>
        </label>
        {shows("duration_months") && (
          <label className="field">
            <span>Duration (months)</span>
            <input
              type="number"
              min={1}
              max={60}
              step={1}
              value={value.duration_months}
              onChange={set("duration_months")}
              disabled={disabled}
            />
          </label>
        )}
        {shows("hours_per_week") && (
          <label className="field">
            <span>Hours per week</span>
            <input
              type="number"
              min={1}
              max={168}
              step={1}
              value={value.hours_per_week}
              onChange={set("hours_per_week")}
              disabled={disabled}
            />
          </label>
        )}
      </div>

      {(shows("is_paid") || shows("conversion_to_full_time")) && (
        <div className="field-row">
          {shows("is_paid") && (
            <label className="field">
              <span>Paid or unpaid</span>
              <select value={value.is_paid} onChange={set("is_paid")} disabled={disabled}>
                <option value="">Not specified</option>
                <option value="true">Paid</option>
                <option value="false">Unpaid</option>
              </select>
            </label>
          )}
          {shows("conversion_to_full_time") && (
            <label className="field">
              <span>Full-time conversion possible</span>
              <select
                value={value.conversion_to_full_time}
                onChange={set("conversion_to_full_time")}
                disabled={disabled}
              >
                <option value="">Not specified</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </label>
          )}
        </div>
      )}

      {shows("compensation_min") && (
        <div className="field-row">
          <label className="field">
            <span>{pay} from</span>
            <input
              type="number"
              min={0}
              step="0.01"
              value={value.compensation_min}
              onChange={set("compensation_min")}
              disabled={disabled}
            />
          </label>
          <label className="field">
            <span>{pay} up to</span>
            <input
              type="number"
              min={0}
              step="0.01"
              value={value.compensation_max}
              onChange={set("compensation_max")}
              disabled={disabled}
            />
          </label>
          <label className="field">
            <span>Currency</span>
            <input
              maxLength={3}
              placeholder="INR"
              value={value.compensation_currency}
              onChange={set("compensation_currency")}
              disabled={disabled}
            />
          </label>
          <label className="field">
            <span>Period</span>
            <select value={value.compensation_period} onChange={set("compensation_period")} disabled={disabled}>
              <option value="">Not specified</option>
              {(Object.keys(COMPENSATION_PERIOD_LABELS) as CompensationPeriod[]).map((period) => (
                <option key={period} value={period}>
                  {COMPENSATION_PERIOD_LABELS[period]}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {(shows("min_experience_years") || shows("expected_joining_date")) && (
        <div className="field-row">
          {shows("min_experience_years") && (
            <>
              <label className="field">
                <span>Min. experience (years)</span>
                <input
                  type="number"
                  min={0}
                  max={60}
                  step={1}
                  value={value.min_experience_years}
                  onChange={set("min_experience_years")}
                  disabled={disabled}
                />
              </label>
              <label className="field">
                <span>Max. experience (years)</span>
                <input
                  type="number"
                  min={0}
                  max={60}
                  step={1}
                  value={value.max_experience_years}
                  onChange={set("max_experience_years")}
                  disabled={disabled}
                />
              </label>
            </>
          )}
          {shows("expected_joining_date") && (
            <label className="field">
              <span>Expected joining date</span>
              <input
                type="date"
                value={value.expected_joining_date}
                onChange={set("expected_joining_date")}
                disabled={disabled}
              />
            </label>
          )}
        </div>
      )}

      {shows("work_schedule") && (
        <label className="field">
          <span>Schedule</span>
          <input
            placeholder="e.g. Weekday evenings, 6–10 pm"
            value={value.work_schedule}
            onChange={set("work_schedule")}
            disabled={disabled}
          />
        </label>
      )}

      <label className="field">
        <span>Eligibility</span>
        <textarea
          rows={2}
          value={value.eligibility_criteria}
          onChange={set("eligibility_criteria")}
          disabled={disabled}
        />
      </label>

      {shows("employment_terms") && (
        <label className="field">
          <span>Employment terms</span>
          <textarea rows={2} value={value.employment_terms} onChange={set("employment_terms")} disabled={disabled} />
        </label>
      )}
    </fieldset>
  );
}
