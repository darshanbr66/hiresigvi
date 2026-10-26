/**
 * Turns a failed API response into a message a person can act on
 * (docs/developer-guide.md § Error handling).
 *
 * The backend's own business messages ("Only a deactivated account can be
 * deleted. Deactivate it first.") are written for users and are kept. What
 * gets replaced is anything generic or technical: framework defaults ("Not
 * Found"), Pydantic validation summaries ("questions[2].options[1].label:
 * String should have at least 1 character"), server failures, and anything
 * that looks like internal detail (exception names, SQL, file paths).
 */

export type ApiErrorKind =
  | "validation"
  | "auth"
  | "permission"
  | "not_found"
  | "conflict"
  | "rate_limit"
  | "network"
  | "server"
  | "unknown";

export interface FieldIssue {
  field: string;
  message: string;
}

export const NETWORK_MESSAGE = "We couldn't reach the server. Check your connection and try again.";
export const SERVER_MESSAGE = "We couldn't complete this request. Please try again.";
const SESSION_MESSAGE = "Your session has expired. Please sign in again.";
const PERMISSION_MESSAGE = "You don't have permission to do this.";
const NOT_FOUND_MESSAGE = "We couldn't find that. It may have been removed, or you may not have access to it.";
const RATE_LIMIT_MESSAGE = "Too many attempts. Please wait a moment and try again.";
const TOO_LARGE_MESSAGE = "That file is too large to upload.";

/** Codes whose wording the product prefers over the server's. */
const CODE_MESSAGES: Record<string, string> = {
  user_active: "Deactivate this account before deleting it.",
};

/** Server messages that are framework/generic defaults, never authored for users. */
const GENERIC_SERVER_MESSAGES = new Set([
  "request failed.",
  "not found",
  "method not allowed",
  "internal server error",
  "an unexpected error occurred.",
  "request validation failed.",
]);

const TECHNICAL_PATTERN =
  /traceback|exception|sqlalchemy|psycopg|asyncpg|integrityerror|violates|duplicate key|stack|\.py\b|errno|<class|\bnull value\b/i;

export function kindForStatus(status: number): ApiErrorKind {
  if (status === 0) return "network";
  if (status === 401) return "auth";
  if (status === 403) return "permission";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 429) return "rate_limit";
  if (status === 400 || status === 413 || status === 422) return "validation";
  if (status >= 500) return "server";
  return "unknown";
}

function isUsable(message: string | undefined | null): message is string {
  if (!message) return false;
  const trimmed = message.trim();
  return trimmed !== "" && !GENERIC_SERVER_MESSAGES.has(trimmed.toLowerCase()) && !TECHNICAL_PATTERN.test(trimmed);
}

/** "questions[2].options[1].label" -> "Question 3, option 2, label". */
export function humanizeField(field: string): string {
  if (!field || field === "(request)") return "";
  return field
    .split(".")
    .map((part) => {
      const match = /^([a-z_]+)((?:\[\d+\])*)$/i.exec(part);
      if (!match) return part;
      const name = match[1].replace(/_/g, " ");
      const indexes = [...match[2].matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1]) + 1);
      if (indexes.length === 0) return name;
      const singular = name.endsWith("s") ? name.slice(0, -1) : name;
      return `${singular} ${indexes.join(".")}`;
    })
    .join(", ")
    .replace(/^./, (c) => c.toUpperCase());
}

/** Pydantic wording -> plain wording. */
export function humanizeIssue(message: string): string {
  const text = message.replace(/^Value error,\s*/i, "").trim();
  if (/^field required$/i.test(text)) return "This field is required.";
  if (/valid email/i.test(text)) return "Enter a valid email address.";
  const min = /^String should have at least (\d+) characters?$/i.exec(text);
  if (min) return min[1] === "1" ? "This field is required." : `Use at least ${min[1]} characters.`;
  const max = /^String should have at most (\d+) characters?$/i.exec(text);
  if (max) return `Use at most ${max[1]} characters.`;
  if (/^Input should be a valid (integer|number)/i.test(text)) return "Enter a number.";
  if (/^Input should be a valid date/i.test(text)) return "Enter a valid date.";
  if (/^Input should be a valid UUID/i.test(text)) return "Choose a valid option.";
  // Numeric bounds before the "Input should be ..." catch-all below.
  const ge = /^Input should be greater than or equal to (\S+)$/i.exec(text);
  if (ge) return `Enter ${ge[1]} or more.`;
  const le = /^Input should be less than or equal to (\S+)$/i.exec(text);
  if (le) return `Enter ${le[1]} or less.`;
  const gt = /^Input should be greater than (\S+)$/i.exec(text);
  if (gt) return `Enter a number greater than ${gt[1]}.`;
  const lt = /^Input should be less than (\S+)$/i.exec(text);
  if (lt) return `Enter a number less than ${lt[1]}.`;
  if (/^Input should be/i.test(text)) return "Choose one of the allowed values.";
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

export function humanizeDetails(details: FieldIssue[]): FieldIssue[] {
  return details.map((d) => ({ field: d.field, message: humanizeIssue(d.message) }));
}

/** The message to show for a failed request. */
export function friendlyMessage(input: {
  status: number;
  code: string;
  message?: string | null;
  details?: FieldIssue[];
}): string {
  const { status, code, message, details = [] } = input;
  if (CODE_MESSAGES[code]) return CODE_MESSAGES[code];

  switch (kindForStatus(status)) {
    case "network":
      return NETWORK_MESSAGE;
    case "server":
      return status === 503 && isUsable(message) && code !== "internal_error" ? message : SERVER_MESSAGE;
    case "auth":
      // A failed sign-in keeps its own wording; anything else is a lost session.
      return /password|credential/i.test(message ?? "") ? (message as string) : SESSION_MESSAGE;
    case "permission":
      return isUsable(message) && code !== "http_error" ? message : PERMISSION_MESSAGE;
    case "not_found":
      return isUsable(message) && code !== "http_error" ? message : NOT_FOUND_MESSAGE;
    case "rate_limit":
      return isUsable(message) ? message : RATE_LIMIT_MESSAGE;
    case "validation": {
      if (status === 413) return TOO_LARGE_MESSAGE;
      if (code === "validation_error" && details.length > 0) {
        const issues = humanizeDetails(details);
        const [first] = issues;
        const label = humanizeField(first.field);
        const lead = label ? `${label}: ${first.message}` : first.message;
        return issues.length > 1 ? `Please check the form. ${lead} (and ${issues.length - 1} more)` : lead;
      }
      return isUsable(message) ? message : "Please check what you entered and try again.";
    }
    default:
      return isUsable(message) ? message : SERVER_MESSAGE;
  }
}
