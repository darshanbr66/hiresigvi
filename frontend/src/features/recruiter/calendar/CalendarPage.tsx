import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { ConfirmDialog } from "../../../shared/components/ConfirmDialog";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Icon } from "../../../shared/components/Icon";
import { Modal } from "../../../shared/components/Modal";
import { SkeletonList } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import type {
  CalendarEventResponse,
  CalendarEventType,
} from "../../../types/calendar";
import { useAuth } from "../../auth/AuthContext";
import { listApplications } from "../applications/api";
import { listCandidates } from "../candidates/api";
import { listJobs } from "../jobs/api";
import {
  createEvent,
  deleteEvent,
  getEvent,
  listAttendeeOptions,
  listEvents,
  updateEvent,
} from "./api";
import { AttendeeSelector } from "./AttendeeSelector";
import {
  addDays,
  browserTimezone,
  eventsOnDay,
  fromDateTimeLocalValue,
  getMonthGridDays,
  getWeekDays,
  isSameDay,
  rangeForView,
  startOfDay,
  toDateTimeLocalValue,
  type CalendarView,
} from "./calendarDates";

const CALENDAR_QUERY_KEY = ["recruiter", "calendar", "events"];

const EVENT_TYPE_LABELS: Record<CalendarEventType, string> = {
  INTERVIEW: "Interview",
  HR_MEETING: "HR meeting",
  TEAM_MEETING: "Team meeting",
  ASSESSMENT_DEADLINE: "Assessment deadline",
  FOLLOW_UP: "Follow-up",
  RECRUITMENT_EVENT: "Recruitment event",
  CAMPUS_EVENT: "Campus event",
  GENERAL_REMINDER: "Reminder",
};

/** One class per event type, defined in dashboard.css, so each type reads as
 * a distinct color at a glance across month/week/day views. */
const EVENT_TYPE_CLASS: Record<CalendarEventType, string> = {
  INTERVIEW: "cal-event-interview",
  HR_MEETING: "cal-event-hr",
  TEAM_MEETING: "cal-event-team",
  ASSESSMENT_DEADLINE: "cal-event-assessment",
  FOLLOW_UP: "cal-event-followup",
  RECRUITMENT_EVENT: "cal-event-recruitment",
  CAMPUS_EVENT: "cal-event-campus",
  GENERAL_REMINDER: "cal-event-reminder",
};

const REMINDER_OPTIONS = [
  { value: "", label: "No reminder" },
  { value: "5", label: "5 minutes before" },
  { value: "15", label: "15 minutes before" },
  { value: "30", label: "30 minutes before" },
  { value: "60", label: "1 hour before" },
  { value: "1440", label: "1 day before" },
];

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

interface EventFormState {
  title: string;
  description: string;
  eventType: CalendarEventType;
  start: string;
  end: string;
  allDay: boolean;
  reminderMinutes: string;
  candidateId: string;
  jobId: string;
  applicationId: string;
  attendeeIds: string[];
}

function emptyForm(start: Date): EventFormState {
  const end = new Date(start.getTime() + 30 * 60 * 1000);
  return {
    title: "",
    description: "",
    eventType: "INTERVIEW",
    start: toDateTimeLocalValue(start),
    end: toDateTimeLocalValue(end),
    allDay: false,
    reminderMinutes: "15",
    candidateId: "",
    jobId: "",
    applicationId: "",
    attendeeIds: [],
  };
}

function eventToForm(event: CalendarEventResponse): EventFormState {
  return {
    title: event.title,
    description: event.description ?? "",
    eventType: event.event_type,
    start: toDateTimeLocalValue(new Date(event.start_at)),
    end: toDateTimeLocalValue(new Date(event.end_at)),
    allDay: event.all_day,
    reminderMinutes: event.reminder_minutes_before != null ? String(event.reminder_minutes_before) : "",
    candidateId: event.candidate_id ?? "",
    jobId: event.job_id ?? "",
    applicationId: event.application_id ?? "",
    attendeeIds: event.attendee_ids,
  };
}

function formatEventTime(event: CalendarEventResponse): string {
  if (event.all_day) return "All day";
  const start = new Date(event.start_at);
  const end = new Date(event.end_at);
  const opts: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" };
  return `${start.toLocaleTimeString(undefined, opts)} – ${end.toLocaleTimeString(undefined, opts)}`;
}

export function CalendarPage() {
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [view, setView] = useState<CalendarView>("month");
  const [anchorDate, setAnchorDate] = useState(() => startOfDay(new Date()));
  const [search, setSearch] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editingEvent, setEditingEvent] = useState<CalendarEventResponse | null>(null);
  const [detailEvent, setDetailEvent] = useState<CalendarEventResponse | null>(null);
  const [form, setForm] = useState<EventFormState>(() => emptyForm(new Date()));
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<CalendarEventResponse | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const range = rangeForView(anchorDate, view);
  const trimmedSearch = search.trim();

  const eventsQuery = useQuery({
    queryKey: [...CALENDAR_QUERY_KEY, range.start.toISOString(), range.end.toISOString(), trimmedSearch],
    queryFn: () =>
      listEvents(token, {
        start: range.start.toISOString(),
        end: range.end.toISOString(),
        search: trimmedSearch || undefined,
      }),
    enabled: accessToken !== null,
  });

  // Arriving from a reminder notification link (`?event=<id>`) opens that
  // event's detail view directly, regardless of which date range/view is
  // currently displayed — then clears the param so it isn't re-triggered
  // by a later navigation back to this page.
  const linkedEventId = searchParams.get("event");
  const linkedEventQuery = useQuery({
    queryKey: ["recruiter", "calendar", "linked-event", linkedEventId],
    queryFn: () => getEvent(linkedEventId as string, token),
    enabled: accessToken !== null && linkedEventId !== null,
  });
  useEffect(() => {
    if (!linkedEventQuery.data) return;
    setDetailEvent(linkedEventQuery.data);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("event");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedEventQuery.data]);

  const candidatesQuery = useQuery({
    queryKey: ["recruiter", "candidates"],
    queryFn: () => listCandidates(token),
    enabled: accessToken !== null && showForm,
  });
  const jobsQuery = useQuery({
    queryKey: ["recruiter", "jobs"],
    queryFn: () => listJobs(token),
    enabled: accessToken !== null && showForm,
  });
  const applicationsQuery = useQuery({
    queryKey: ["recruiter", "applications"],
    queryFn: () => listApplications(token),
    enabled: accessToken !== null && showForm,
  });
  // Attendee options come from the calendar's own directory endpoint rather
  // than /recruiter/users: that one needs `user.read` (ORG_ADMIN and HR only),
  // so other roles creating an event would get an empty attendee list. Also loaded for
  // the detail modal, to show attendees by name instead of by id.
  const attendeesQuery = useQuery({
    queryKey: ["recruiter", "calendar", "attendee-options"],
    queryFn: () => listAttendeeOptions(token),
    enabled: accessToken !== null && (showForm || detailEvent !== null),
  });
  const attendeeOptions = attendeesQuery.data ?? [];
  const attendeeNameById = new Map(attendeeOptions.map((option) => [option.id, option.full_name]));

  const events = eventsQuery.data ?? [];

  function openCreateForm(start: Date) {
    setEditingEvent(null);
    setForm(emptyForm(start));
    setFormError(null);
    setDetailEvent(null);
    setShowForm(true);
  }

  function openEditForm(event: CalendarEventResponse) {
    setEditingEvent(event);
    setForm(eventToForm(event));
    setFormError(null);
    setDetailEvent(null);
    setShowForm(true);
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const timezone = browserTimezone();
      const payload = {
        title: form.title,
        description: form.description || null,
        event_type: form.eventType,
        start_at: fromDateTimeLocalValue(form.start).toISOString(),
        end_at: fromDateTimeLocalValue(form.end).toISOString(),
        all_day: form.allDay,
        timezone,
        candidate_id: form.candidateId || null,
        job_id: form.jobId || null,
        application_id: form.applicationId || null,
        reminder_minutes_before: form.reminderMinutes ? Number(form.reminderMinutes) : null,
        attendee_ids: form.attendeeIds,
      };
      return editingEvent ? updateEvent(editingEvent.id, payload, token) : createEvent(payload, token);
    },
    onSuccess: () => {
      showToast(editingEvent ? "Event updated." : "Event created.", "success");
      setShowForm(false);
      void queryClient.invalidateQueries({ queryKey: CALENDAR_QUERY_KEY });
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteEvent(pendingDelete!.id, token),
    onSuccess: () => {
      showToast("Event deleted.", "success");
      setPendingDelete(null);
      void queryClient.invalidateQueries({ queryKey: CALENDAR_QUERY_KEY });
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    saveMutation.mutate();
  }

  function navigate(direction: -1 | 1) {
    if (view === "month") {
      setAnchorDate((prev) => new Date(prev.getFullYear(), prev.getMonth() + direction, 1));
    } else if (view === "week") {
      setAnchorDate((prev) => addDays(prev, direction * 7));
    } else {
      setAnchorDate((prev) => addDays(prev, direction));
    }
  }

  const today = startOfDay(new Date());
  const headerLabel =
    view === "month"
      ? anchorDate.toLocaleDateString(undefined, { month: "long", year: "numeric" })
      : view === "week"
        ? `${getWeekDays(anchorDate)[0].toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${getWeekDays(anchorDate)[6].toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`
        : anchorDate.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });

  const isOwn = (event: CalendarEventResponse) => event.organizer_id === user?.id;

  function renderEventChip(event: CalendarEventResponse, compact = true) {
    return (
      <button
        key={event.id}
        type="button"
        className={`cal-event-chip ${EVENT_TYPE_CLASS[event.event_type]}`}
        onClick={(e) => {
          // The chip sits inside a day cell that opens the "new event" form
          // on click — without this, opening an existing event's detail view
          // would immediately be clobbered by that parent handler.
          e.stopPropagation();
          setDetailEvent(event);
        }}
        title={event.title}
      >
        {!compact && <span className="cal-event-time">{formatEventTime(event)}</span>}
        <span className="cal-event-title">{event.title}</span>
      </button>
    );
  }

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Calendar</h1>
          <p className="muted">Interviews, reviews, and team meetings — with in-app reminders.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => openCreateForm(new Date())}>
          + New event
        </button>
      </div>

      <div className="toolbar">
        <div className="btn-group">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => navigate(-1)} aria-label="Previous">
            ‹
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAnchorDate(startOfDay(new Date()))}>
            Today
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => navigate(1)} aria-label="Next">
            ›
          </button>
        </div>
        <h2 className="cal-header-label">{headerLabel}</h2>
        <div className="btn-group">
          {(["month", "week", "day"] as CalendarView[]).map((v) => (
            <button
              key={v}
              type="button"
              className={`btn btn-sm ${view === v ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setView(v)}
            >
              {v[0].toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-input-wrap">
          <Icon name="search" size={16} className="search-input-icon" />
          <input
            className="search-input"
            placeholder="Search events, candidates, jobs, people…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button type="button" className="search-input-clear" aria-label="Clear search" onClick={() => setSearch("")}>
              <Icon name="x" size={14} />
            </button>
          )}
        </div>
      </div>

      {eventsQuery.isPending && <SkeletonList rows={4} />}
      {eventsQuery.isError && (
        <Alert>{eventsQuery.error instanceof ApiError ? eventsQuery.error.message : "Could not load events."}</Alert>
      )}

      {eventsQuery.isSuccess && events.length === 0 && (
        <EmptyState
          icon="clock"
          title={trimmedSearch ? "No matching events" : "No events in this range"}
          action={
            !trimmedSearch && (
              <button type="button" className="btn btn-primary" onClick={() => openCreateForm(new Date())}>
                Schedule your first event
              </button>
            )
          }
        >
          {trimmedSearch
            ? "Try a different search term, or widen the date range."
            : "Interviews, follow-ups, and team meetings you create will appear here."}
        </EmptyState>
      )}

      {eventsQuery.isSuccess && events.length > 0 && view === "month" && (
        <div className="card cal-month-card">
          <div className="cal-weekday-row">
            {WEEKDAY_LABELS.map((label) => (
              <div key={label} className="cal-weekday-label">
                {label}
              </div>
            ))}
          </div>
          <div className="cal-month-grid">
            {getMonthGridDays(anchorDate).map((day) => {
              const dayEvents = eventsOnDay(events, day);
              const inMonth = day.getMonth() === anchorDate.getMonth();
              return (
                <div
                  key={day.toISOString()}
                  className={`cal-month-cell${inMonth ? "" : " cal-month-cell-outside"}${isSameDay(day, today) ? " cal-month-cell-today" : ""}`}
                  onClick={() => openCreateForm(new Date(day.getFullYear(), day.getMonth(), day.getDate(), 9))}
                >
                  <span className="cal-month-cell-date">{day.getDate()}</span>
                  <div className="cal-month-cell-events">
                    {dayEvents.slice(0, 3).map((event) => renderEventChip(event))}
                    {dayEvents.length > 3 && <span className="muted cal-more">+{dayEvents.length - 3} more</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {eventsQuery.isSuccess && events.length > 0 && view === "week" && (
        <div className="card cal-week-card">
          <div className="cal-week-grid">
            {getWeekDays(anchorDate).map((day) => {
              const dayEvents = eventsOnDay(events, day);
              return (
                <div
                  key={day.toISOString()}
                  className={`cal-week-col${isSameDay(day, today) ? " cal-month-cell-today" : ""}`}
                >
                  <div className="cal-week-col-head" onClick={() => openCreateForm(new Date(day.getFullYear(), day.getMonth(), day.getDate(), 9))}>
                    <span className="cal-weekday-label">{day.toLocaleDateString(undefined, { weekday: "short" })}</span>
                    <span className="cal-month-cell-date">{day.getDate()}</span>
                  </div>
                  <div className="cal-week-col-events">
                    {dayEvents.map((event) => renderEventChip(event, false))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {eventsQuery.isSuccess && events.length > 0 && view === "day" && (
        <div className="card cal-day-card">
          {eventsOnDay(events, anchorDate).length === 0 && (
            <p className="muted">No events scheduled for this day.</p>
          )}
          <div className="stack-sm">
            {eventsOnDay(events, anchorDate).map((event) => (
              <button
                key={event.id}
                type="button"
                className={`cal-day-row ${EVENT_TYPE_CLASS[event.event_type]}`}
                onClick={() => setDetailEvent(event)}
              >
                <span className="cal-event-time">{formatEventTime(event)}</span>
                <span className="cal-event-title">{event.title}</span>
                <span className="chip">{EVENT_TYPE_LABELS[event.event_type]}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {detailEvent && (
        <Modal title={detailEvent.title} onClose={() => setDetailEvent(null)}>
          <div className="stack-sm">
            <span className={`badge badge-active`}>{EVENT_TYPE_LABELS[detailEvent.event_type]}</span>
            <p className="muted" style={{ margin: 0 }}>
              {formatEventTime(detailEvent)} · {new Date(detailEvent.start_at).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}
            </p>
            <p className="muted" style={{ margin: 0 }}>Timezone: {detailEvent.timezone}</p>
            {detailEvent.description && <p>{detailEvent.description}</p>}
            <p className="muted" style={{ margin: 0 }}>Organizer: {detailEvent.organizer_name}</p>
            {detailEvent.attendee_ids.length > 0 && (
              <p className="muted" style={{ margin: 0 }}>
                Attendees:{" "}
                {detailEvent.attendee_ids
                  .map((id) => attendeeNameById.get(id) ?? "Former team member")
                  .join(", ")}
              </p>
            )}
            {detailEvent.reminder_minutes_before != null && (
              <p className="muted" style={{ margin: 0 }}>
                Reminder {detailEvent.reminder_minutes_before} minute(s) before
                {detailEvent.reminder_fired_at ? " (already sent)" : ""}
              </p>
            )}
            <div className="btn-group" style={{ marginTop: "0.5rem" }}>
              {isOwn(detailEvent) && (
                <>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => openEditForm(detailEvent)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={() => {
                      setPendingDelete(detailEvent);
                      setDetailEvent(null);
                    }}
                  >
                    Delete
                  </button>
                </>
              )}
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDetailEvent(null)}>
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}

      {showForm && (
        <Modal title={editingEvent ? "Edit event" : "New event"} onClose={() => setShowForm(false)} wide>
          <form onSubmit={handleSubmit}>
            <label className="field">
              <span>Title</span>
              <input
                required
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                disabled={saveMutation.isPending}
              />
            </label>
            <label className="field">
              <span>Description</span>
              <textarea
                rows={3}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                disabled={saveMutation.isPending}
              />
            </label>

            <div className="field-row">
              <label className="field">
                <span>Type</span>
                <select
                  value={form.eventType}
                  onChange={(e) => setForm({ ...form, eventType: e.target.value as CalendarEventType })}
                  disabled={saveMutation.isPending}
                >
                  {(Object.keys(EVENT_TYPE_LABELS) as CalendarEventType[]).map((type) => (
                    <option key={type} value={type}>
                      {EVENT_TYPE_LABELS[type]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field checkbox-row" style={{ alignSelf: "flex-end" }}>
                <input
                  type="checkbox"
                  checked={form.allDay}
                  onChange={(e) => setForm({ ...form, allDay: e.target.checked })}
                  disabled={saveMutation.isPending}
                />
                <span>All day</span>
              </label>
            </div>

            <div className="field-row">
              <label className="field">
                <span>Starts</span>
                <input
                  required
                  type="datetime-local"
                  value={form.start}
                  onChange={(e) => setForm({ ...form, start: e.target.value })}
                  disabled={saveMutation.isPending}
                />
              </label>
              <label className="field">
                <span>Ends</span>
                <input
                  required
                  type="datetime-local"
                  value={form.end}
                  onChange={(e) => setForm({ ...form, end: e.target.value })}
                  disabled={saveMutation.isPending}
                />
              </label>
            </div>
            <p className="muted" style={{ fontSize: "0.78rem", margin: "-0.5rem 0 0.5rem" }}>
              Timezone: {browserTimezone()}
            </p>

            <label className="field">
              <span>Reminder</span>
              <select
                value={form.reminderMinutes}
                onChange={(e) => setForm({ ...form, reminderMinutes: e.target.value })}
                disabled={saveMutation.isPending}
              >
                {REMINDER_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <div className="field-row">
              <label className="field">
                <span>Related candidate (optional)</span>
                <select
                  value={form.candidateId}
                  onChange={(e) => setForm({ ...form, candidateId: e.target.value })}
                  disabled={saveMutation.isPending || candidatesQuery.isPending}
                >
                  <option value="">None</option>
                  {candidatesQuery.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.full_name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Related job (optional)</span>
                <select
                  value={form.jobId}
                  onChange={(e) => setForm({ ...form, jobId: e.target.value })}
                  disabled={saveMutation.isPending || jobsQuery.isPending}
                >
                  <option value="">None</option>
                  {jobsQuery.data?.map((j) => (
                    <option key={j.id} value={j.id}>
                      {j.title}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="field">
              <span>Related application (optional)</span>
              <select
                value={form.applicationId}
                onChange={(e) => setForm({ ...form, applicationId: e.target.value })}
                disabled={saveMutation.isPending || applicationsQuery.isPending}
              >
                <option value="">None</option>
                {applicationsQuery.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.candidate_full_name} — {a.job_title}
                  </option>
                ))}
              </select>
            </label>

            <fieldset className="field" style={{ border: "none", padding: 0 }}>
              <legend style={{ padding: 0, marginBottom: "0.35rem" }}>Attendees</legend>
              <AttendeeSelector
                options={attendeeOptions.filter((option) => option.id !== user?.id)}
                selectedIds={form.attendeeIds}
                onChange={(attendeeIds) => setForm((prev) => ({ ...prev, attendeeIds }))}
                isPending={attendeesQuery.isPending}
                isError={attendeesQuery.isError}
                disabled={saveMutation.isPending}
              />
            </fieldset>

            {formError && <Alert>{formError}</Alert>}

            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? <Spinner label="Saving…" /> : editingEvent ? "Save changes" : "Create event"}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setShowForm(false)} disabled={saveMutation.isPending}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this event?"
          message="This permanently removes the event and cancels its reminder. This cannot be undone."
          confirmLabel="Delete event"
          isConfirming={deleteMutation.isPending}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => deleteMutation.mutate()}
        />
      )}
    </div>
  );
}
