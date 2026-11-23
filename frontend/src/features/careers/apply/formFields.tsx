import type { ReactNode } from "react";

export function RequiredMark({ optional }: { optional?: boolean }) {
  return optional ? (
    <span className="optional-mark">(Optional)</span>
  ) : (
    <span className="required-mark" aria-hidden="true">
      *
    </span>
  );
}

/**
 * One labelled control. Its inline message sits directly below the control —
 * outside the <label>, so the message never becomes part of the input's
 * accessible name — and is linked with aria-describedby by the caller
 * (`describedBy(id, message, hint)`). The control itself gets `id`.
 */
export function FormField({
  id,
  label,
  optional,
  hint,
  message,
  children,
}: {
  id: string;
  label: string;
  optional?: boolean;
  hint?: string;
  message?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {label} <RequiredMark optional={optional} />
      </label>
      {children}
      {message && (
        <p id={`${id}-message`} className="field-message" role="alert">
          {message}
        </p>
      )}
      {hint && (
        <span id={`${id}-hint`} className="field-hint">
          {hint}
        </span>
      )}
    </div>
  );
}
