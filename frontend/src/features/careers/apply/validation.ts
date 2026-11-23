/** Client-side checks of the apply flow. They only spare the candidate a
 * round trip — the backend validates every one of them again
 * (backend/app/schemas/application_draft.py, app/schemas/candidate.py). */

export const MAX_RESUME_MB = 10;
export const MIN_AGE_YEARS = 16;
/** The optional 10th marks card (app/services/candidate_document_service.py). */
export const MAX_MARKS_CARD_MB = 5;
export const DOCUMENT_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg"];
export const DOCUMENT_ACCEPT = ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg";

export function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value.trim());
}

export function fullNameError(value: string): string | null {
  const cleaned = value.trim().replace(/\s+/g, " ");
  if (!cleaned) return "Enter your full name.";
  if ((cleaned.match(/\p{L}/gu) ?? []).length < 2) return "Enter your full name.";
  if (/[\d<>{}[\]@#$%^*=_|\\/]/.test(cleaned)) {
    return "A name can contain letters, spaces, dots, hyphens and apostrophes.";
  }
  return null;
}

export function emailError(value: string): string | null {
  if (!value.trim()) return "Enter your email address.";
  return looksLikeEmail(value) ? null : "Enter a valid email address, e.g. name@example.com.";
}

/** Dial codes offered by the mobile number field; India first (the
 * default region the backend assumes). */
export const COUNTRY_CODES: { code: string; label: string }[] = [
  { code: "+91", label: "India (+91)" },
  { code: "+1", label: "United States / Canada (+1)" },
  { code: "+44", label: "United Kingdom (+44)" },
  { code: "+61", label: "Australia (+61)" },
  { code: "+65", label: "Singapore (+65)" },
  { code: "+971", label: "United Arab Emirates (+971)" },
  { code: "+966", label: "Saudi Arabia (+966)" },
  { code: "+974", label: "Qatar (+974)" },
  { code: "+49", label: "Germany (+49)" },
  { code: "+33", label: "France (+33)" },
  { code: "+81", label: "Japan (+81)" },
  { code: "+977", label: "Nepal (+977)" },
  { code: "+94", label: "Sri Lanka (+94)" },
  { code: "+880", label: "Bangladesh (+880)" },
];

/** "+91 98765 43210" / "+919876543210" -> {code: "+91", number: "98765 43210"}. */
export function splitPhone(value: string): { code: string; number: string } {
  const trimmed = value.trim();
  if (trimmed.startsWith("+")) {
    const digits = trimmed.slice(1).replace(/\D/g, "");
    const match = [...COUNTRY_CODES]
      .sort((a, b) => b.code.length - a.code.length)
      .find(({ code }) => digits.startsWith(code.slice(1)));
    if (match) return { code: match.code, number: digits.slice(match.code.length - 1) };
  }
  return { code: "+91", number: trimmed };
}

export function joinPhone(code: string, number: string): string {
  const national = number.trim();
  return national ? `${code} ${national}` : "";
}

export function phoneError(value: string): string | null {
  const { code, number } = splitPhone(value);
  const digits = number.replace(/\D/g, "").replace(/^0+/, "");
  if (!digits) return "Enter your mobile number.";
  if (/[^\d\s()-]/.test(number)) return "A mobile number can contain only digits.";
  if (code === "+91") {
    return /^[6-9]\d{9}$/.test(digits) ? null : "Enter a valid 10-digit Indian mobile number.";
  }
  return digits.length >= 6 && digits.length <= 14 ? null : "Enter a valid mobile number.";
}

/** A percentage between 0 and 100 with at most two decimals, or blank. */
export function tenthMarksError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(trimmed) || Number(trimmed) > 100) {
    return "Enter a percentage between 0 and 100 (up to two decimals).";
  }
  return null;
}

/** PDF/PNG/JPG within `maxMb` — the academic document and the 10th marks card. */
export function documentFileError(file: File, maxMb: number, what: string): string | null {
  const name = file.name.toLowerCase();
  if (!DOCUMENT_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return `The ${what} must be a PDF, PNG or JPG file.`;
  }
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > maxMb * 1024 * 1024) return `The ${what} must be smaller than ${maxMb} MB.`;
  return null;
}

export function resumeFileError(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "Please upload your resume as a PDF file.";
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > MAX_RESUME_MB * 1024 * 1024) {
    return `Your resume must be smaller than ${MAX_RESUME_MB} MB.`;
  }
  return null;
}

export function formatFileSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function latestAllowedBirthDate(): string {
  const date = new Date();
  date.setFullYear(date.getFullYear() - MIN_AGE_YEARS);
  return date.toISOString().slice(0, 10);
}

export function completionYearError(value: string): string | null {
  const year = Number(value);
  const latest = new Date().getFullYear() + 6;
  if (!value.trim()) return "Enter the year of completion.";
  if (!Number.isInteger(year) || year < 1960 || year > latest) {
    return `Enter a year between 1960 and ${latest}.`;
  }
  return null;
}

/** Validates a URL field the way the backend does (host only). */
export function profileUrlError(value: string, host: "linkedin.com" | "github.com"): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    const url = new URL(trimmed.includes("://") ? trimmed : `https://${trimmed}`);
    const ok =
      (url.protocol === "https:" || url.protocol === "http:") &&
      (url.hostname === host || url.hostname.endsWith(`.${host}`));
    return ok ? null : `Enter a valid ${host} URL.`;
  } catch {
    return `Enter a valid ${host} URL.`;
  }
}
