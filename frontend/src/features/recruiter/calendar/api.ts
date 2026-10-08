import { apiClient } from "../../../lib/apiClient";
import type {
  CalendarAttendeeOption,
  CalendarEventCreateRequest,
  CalendarEventResponse,
  CalendarEventUpdateRequest,
} from "../../../types/calendar";

export function listEvents(
  accessToken: string,
  range: { start: string; end: string; candidateId?: string; jobId?: string; search?: string },
) {
  const params = new URLSearchParams({ start: range.start, end: range.end });
  if (range.candidateId) params.set("candidate_id", range.candidateId);
  if (range.jobId) params.set("job_id", range.jobId);
  if (range.search) params.set("search", range.search);
  return apiClient.get<CalendarEventResponse[]>(
    `/api/v1/recruiter/calendar/events?${params.toString()}`,
    accessToken,
  );
}

/** Fetches one event directly by id, regardless of the currently displayed
 * date range — used to open a specific event's detail view when arriving
 * from a reminder notification link. */
export function getEvent(eventId: string, accessToken: string) {
  return apiClient.get<CalendarEventResponse>(
    `/api/v1/recruiter/calendar/events/${eventId}`,
    accessToken,
  );
}

export function createEvent(payload: CalendarEventCreateRequest, accessToken: string) {
  return apiClient.post<CalendarEventResponse>("/api/v1/recruiter/calendar/events", payload, accessToken);
}

export function updateEvent(eventId: string, payload: CalendarEventUpdateRequest, accessToken: string) {
  return apiClient.patch<CalendarEventResponse>(
    `/api/v1/recruiter/calendar/events/${eventId}`,
    payload,
    accessToken,
  );
}

export function deleteEvent(eventId: string, accessToken: string) {
  return apiClient.delete(`/api/v1/recruiter/calendar/events/${eventId}`, accessToken);
}

/** People the caller may invite to an event. Distinct from the Team
 * directory (`/recruiter/users`, `user.read` — ORG_ADMIN and HR only): anyone who
 * can see the calendar can pick attendees. */
export function listAttendeeOptions(accessToken: string) {
  return apiClient.get<CalendarAttendeeOption[]>(
    "/api/v1/recruiter/calendar/attendee-options",
    accessToken,
  );
}
