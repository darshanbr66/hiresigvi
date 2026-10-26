/**
 * Thin fetch wrapper — the one place that knows the backend base URL and
 * how to turn a non-2xx response into a typed error. Feature code calls
 * through this (or a feature-specific service built on top of it), never
 * `fetch` directly, so audience base paths and error handling stay
 * consistent (see docs/api.md).
 *
 * `credentials: "include"` on every call so the recruiter refresh-token
 * cookie (httpOnly, set by the backend) is sent/received automatically —
 * see docs/architecture.md § 4.
 */

import {
  NETWORK_MESSAGE,
  friendlyMessage,
  humanizeDetails,
  kindForStatus,
  type ApiErrorKind,
} from "./apiErrors";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: { field: string; message: string }[];
  /** Structured context the backend attached to this error (the envelope's
   * `error.data`) — e.g. the id of the candidate a create collided with, or
   * the date a reapply window opens. Shape varies by `code`. */
  readonly data: Record<string, unknown> | null;
  /** The server's own message, before `friendlyMessage` (for diagnostics;
   * show `message`). */
  readonly serverMessage: string;
  /** The backend's request id, for support/log correlation. */
  readonly requestId: string | null;

  constructor(
    message: string,
    status: number,
    code: string,
    details: { field: string; message: string }[] = [],
    data: Record<string, unknown> | null = null,
    extra: { serverMessage?: string; requestId?: string | null } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.data = data;
    this.serverMessage = extra.serverMessage ?? message;
    this.requestId = extra.requestId ?? null;
  }

  /** What kind of failure this is (validation, auth, permission, …). */
  get kind(): ApiErrorKind {
    return kindForStatus(this.status);
  }

  /** Field-level problems from a validation error, in plain wording. */
  get fieldIssues(): { field: string; message: string }[] {
    return humanizeDetails(this.details);
  }
}

interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id: string | null;
    details?: { field: string; message: string }[];
    data?: Record<string, unknown>;
  };
}

interface RequestOptions {
  accessToken?: string;
  body?: unknown;
}

/** `fetch`, with a failure to connect reported as an ApiError (status 0)
 * instead of the browser's raw "Failed to fetch". */
async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE_URL}${path}`, { credentials: "include", ...init });
  } catch {
    throw new ApiError(NETWORK_MESSAGE, 0, "network_error");
  }
}

/** The one place a non-2xx response becomes an ApiError. */
async function toApiError(response: Response): Promise<ApiError> {
  let code = "unknown_error";
  let serverMessage = "";
  let details: { field: string; message: string }[] = [];
  let data: Record<string, unknown> | null = null;
  let requestId: string | null = null;
  try {
    const body = (await response.json()) as ErrorEnvelope;
    code = body.error?.code ?? code;
    serverMessage = body.error?.message ?? "";
    details = body.error?.details ?? [];
    data = body.error?.data ?? null;
    requestId = body.error?.request_id ?? null;
  } catch {
    // Non-JSON error body (e.g. a proxy's HTML page) — never shown as-is.
  }
  const message = friendlyMessage({ status: response.status, code, message: serverMessage, details });
  return new ApiError(message, response.status, code, details, data, { serverMessage, requestId });
}

function authHeaders(accessToken?: string): Record<string, string> {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

async function request<T>(path: string, method: string, options?: RequestOptions): Promise<T> {
  const headers = authHeaders(options?.accessToken);
  if (options?.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await send(path, {
    method,
    headers,
    body: options?.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

async function requestForm<T>(path: string, formData: FormData, accessToken?: string): Promise<T> {
  const response = await send(path, { method: "POST", headers: authHeaders(accessToken), body: formData });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response.json() as Promise<T>;
}

async function requestBlob(
  path: string,
  accessToken: string,
): Promise<{ blob: Blob; filename: string | null }> {
  const response = await send(path, { headers: authHeaders(accessToken) });

  if (!response.ok) {
    throw await toApiError(response);
  }

  const disposition = response.headers.get("content-disposition");
  const match = disposition ? /filename="([^"]+)"/.exec(disposition) : null;
  return { blob: await response.blob(), filename: match?.[1] ?? null };
}

export interface PagedResult<T> {
  items: T[];
  /** Matches across *all* pages (the `X-Total-Count` header), not `items.length`. */
  total: number;
}

/** A list endpoint that pages server-side: the body is the page, and the total
 * number of matches travels in the `X-Total-Count` response header. */
async function requestPage<T>(path: string, accessToken: string): Promise<PagedResult<T>> {
  const response = await send(path, { headers: authHeaders(accessToken) });

  if (!response.ok) {
    throw await toApiError(response);
  }

  const items = (await response.json()) as T[];
  const header = response.headers.get("X-Total-Count");
  const total = header === null || header.trim() === "" ? Number.NaN : Number(header);
  // A missing/garbled header (e.g. a proxy stripped it) degrades to "what we got".
  return { items, total: Number.isInteger(total) && total >= 0 ? total : items.length };
}

export const apiClient = {
  /** Like `get`, for list endpoints that page on the server. */
  getPage: <T>(path: string, accessToken: string) => requestPage<T>(path, accessToken),
  get: <T>(path: string, accessToken?: string) => request<T>(path, "GET", { accessToken }),
  post: <T>(path: string, body?: unknown, accessToken?: string) =>
    request<T>(path, "POST", { body, accessToken }),
  patch: <T>(path: string, body?: unknown, accessToken?: string) =>
    request<T>(path, "PATCH", { body, accessToken }),
  /** `body` is optional: bulk deletes send the ids/confirmation as JSON. */
  delete: <T = void>(path: string, accessToken?: string, body?: unknown) =>
    request<T>(path, "DELETE", { accessToken, body }),
  /** For multipart/form-data submissions (e.g. a resume upload) — the
   * browser sets the Content-Type boundary itself, so this deliberately
   * skips the JSON headers/serialization `request()` always applies.
   * `accessToken` is optional since public (unauthenticated) endpoints use
   * this too. */
  postForm: <T>(path: string, formData: FormData, accessToken?: string) =>
    requestForm<T>(path, formData, accessToken),
  getBlob: (path: string, accessToken: string) => requestBlob(path, accessToken),
};
