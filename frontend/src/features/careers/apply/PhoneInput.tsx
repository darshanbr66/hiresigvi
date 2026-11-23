import { COUNTRY_CODES, joinPhone, splitPhone } from "./validation";

/**
 * A country dial code + the national number, kept as one string
 * ("+91 98765 43210") so the rest of the form — and the backend, which
 * normalizes it to E.164 — sees a single value.
 */
export function PhoneInput({
  id,
  value,
  onChange,
  invalid,
  describedBy,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  invalid?: boolean;
  describedBy?: string;
  disabled?: boolean;
}) {
  const { code, number } = splitPhone(value);
  return (
    <div className="phone-input">
      <select
        aria-label="Country code"
        className="phone-input-code"
        value={code}
        onChange={(e) => onChange(joinPhone(e.target.value, number) || `${e.target.value} `)}
        disabled={disabled}
      >
        {COUNTRY_CODES.map((option) => (
          <option key={option.code} value={option.code}>
            {option.label}
          </option>
        ))}
      </select>
      <input
        id={id}
        type="tel"
        inputMode="tel"
        required
        maxLength={20}
        autoComplete="tel-national"
        placeholder="98765 43210"
        value={number}
        onChange={(e) => onChange(joinPhone(code, e.target.value))}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        disabled={disabled}
      />
    </div>
  );
}
