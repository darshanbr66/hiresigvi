/** The aria props a control inside `FormField` needs. */
export function fieldAria(id: string, message?: string | null, hint?: string) {
  const describedBy = [message ? `${id}-message` : null, hint ? `${id}-hint` : null]
    .filter(Boolean)
    .join(" ");
  return {
    id,
    "aria-invalid": message ? true : undefined,
    "aria-describedby": describedBy || undefined,
  } as const;
}
