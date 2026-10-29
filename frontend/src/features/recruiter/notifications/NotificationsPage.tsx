import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
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
  AnnouncementTarget,
  NotificationResponse,
  NotificationType,
  SentNotificationResponse,
} from "../../../types/notification";
import { useAuth } from "../../auth/AuthContext";
import { listUsers } from "../../auth/api";
import { listDepartments } from "../team/api";
import {
  acknowledgeAllNotifications,
  acknowledgeNotifications,
  deleteNotification,
  listAllNotifications,
  listSentNotifications,
  sendAnnouncement,
  sendDirectMessage,
} from "./api";

const NOTIFICATIONS_QUERY_KEY = ["recruiter", "notifications", "all"];
const SENT_QUERY_KEY = ["recruiter", "notifications", "sent"];

const TYPE_LABEL: Record<NotificationType, string> = {
  ASSESSMENT_STARTED: "Assessment",
  ASSESSMENT_SUBMITTED: "Assessment",
  ANNOUNCEMENT: "Announcement",
  DIRECT_MESSAGE: "Message",
  CALENDAR_REMINDER: "Reminder",
  INTERVIEW_ASSIGNED: "Interview",
};

const TYPE_BADGE: Record<NotificationType, string> = {
  ASSESSMENT_STARTED: "badge-info",
  ASSESSMENT_SUBMITTED: "badge-info",
  ANNOUNCEMENT: "badge-accent",
  DIRECT_MESSAGE: "badge-active",
  CALENDAR_REMINDER: "badge-warn",
  INTERVIEW_ASSIGNED: "badge-info",
};

function relatedEntityLink(notification: NotificationResponse): { to: string; label: string } | null {
  if (!notification.related_entity_type || !notification.related_entity_id) return null;
  switch (notification.related_entity_type) {
    case "APPLICATION":
      return { to: `/recruiter/applications/${notification.related_entity_id}`, label: "View application" };
    case "CANDIDATE":
      return { to: `/recruiter/candidates/${notification.related_entity_id}`, label: "View candidate" };
    case "CALENDAR_EVENT":
      return { to: `/recruiter/calendar?event=${notification.related_entity_id}`, label: "View event" };
    default:
      return null;
  }
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return `${date.toLocaleDateString()} · ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

/** A short, single-line preview for the compact card — the full text is
 * always available in the detail view, never lost, just not crammed into
 * the list. */
function preview(text: string, maxLength = 140): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > maxLength ? `${collapsed.slice(0, maxLength - 1)}…` : collapsed;
}

type Tab = "received" | "sent";

export function NotificationsPage() {
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [tab, setTab] = useState<Tab>("received");
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [search, setSearch] = useState("");
  const [composer, setComposer] = useState<"announcement" | "message" | null>(null);
  const [detail, setDetail] = useState<NotificationResponse | null>(null);
  const [sentDetail, setSentDetail] = useState<SentNotificationResponse | null>(null);
  const [pendingDelete, setPendingDelete] = useState<NotificationResponse | null>(null);

  const isOrgAdmin = user?.roles.includes("ORG_ADMIN") ?? false;
  const trimmedSearch = search.trim();

  const receivedQuery = useQuery({
    queryKey: [...NOTIFICATIONS_QUERY_KEY, filter, trimmedSearch],
    queryFn: () =>
      listAllNotifications(token, {
        unreadOnly: filter === "unread",
        search: trimmedSearch || undefined,
        limit: 100,
      }),
    enabled: accessToken !== null && tab === "received",
  });

  const sentQuery = useQuery({
    queryKey: [...SENT_QUERY_KEY, trimmedSearch],
    queryFn: () => listSentNotifications(token, { search: trimmedSearch || undefined, limit: 100 }),
    enabled: accessToken !== null && tab === "sent",
  });

  const markReadMutation = useMutation({
    mutationFn: (id: string) => acknowledgeNotifications([id], token),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY }),
  });

  const markAllReadMutation = useMutation({
    mutationFn: () => acknowledgeAllNotifications(token),
    onSuccess: (result) => {
      showToast(result.updated > 0 ? `Marked ${result.updated} notification(s) as read.` : "Nothing to mark.", "success");
      void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteNotification(id, token),
    onSuccess: () => {
      showToast("Notification deleted.", "success");
      setPendingDelete(null);
      setDetail(null);
      void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
    },
    onError: (err) => {
      showToast(err instanceof ApiError ? err.message : "Could not delete the notification.", "error");
      setPendingDelete(null);
    },
  });

  const unreadCount = useMemo(
    () => receivedQuery.data?.filter((n) => n.read_at === null).length ?? 0,
    [receivedQuery.data],
  );

  function openDetail(notification: NotificationResponse) {
    setDetail(notification);
    if (notification.read_at === null) markReadMutation.mutate(notification.id);
  }

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Notifications</h1>
          <p className="muted">Announcements, messages and updates for your organization.</p>
        </div>
        <div className="page-header-actions">
          {isOrgAdmin && (
            <button type="button" className="btn btn-ghost" onClick={() => setComposer("announcement")}>
              + Announcement
            </button>
          )}
          {/* Two distinct features, deliberately side by side: "Message a
              colleague" fires a one-off direct notification at a chosen
              person, while Talk to Admin opens the caller's ongoing thread
              with the organization's admins (features/recruiter/adminMessages). */}
          <button type="button" className="btn btn-ghost" onClick={() => setComposer("message")}>
            + Message a colleague
          </button>
          <Link to="/recruiter/talk-to-admin" className="btn btn-ghost">
            <Icon name="send" size={16} /> Talk to Admin
          </Link>
          {tab === "received" && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => markAllReadMutation.mutate()}
              disabled={markAllReadMutation.isPending || unreadCount === 0}
            >
              {markAllReadMutation.isPending ? <Spinner label="Marking…" /> : "Mark all as read"}
            </button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="segmented-control">
          <button
            type="button"
            className={`btn btn-sm ${tab === "received" ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setTab("received")}
          >
            Received
          </button>
          <button
            type="button"
            className={`btn btn-sm ${tab === "sent" ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setTab("sent")}
          >
            Sent
          </button>
        </div>
        <div className="search-input-wrap">
          <Icon name="search" size={16} className="search-input-icon" />
          <input
            className="search-input"
            placeholder="Search notifications…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button type="button" className="search-input-clear" aria-label="Clear search" onClick={() => setSearch("")}>
              <Icon name="x" size={14} />
            </button>
          )}
        </div>
        {tab === "received" && (
          <div className="segmented-control">
            <button
              type="button"
              className={`btn btn-sm ${filter === "all" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setFilter("all")}
            >
              All
            </button>
            <button
              type="button"
              className={`btn btn-sm ${filter === "unread" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setFilter("unread")}
            >
              Unread{unreadCount > 0 ? ` (${unreadCount})` : ""}
            </button>
          </div>
        )}
      </div>

      {tab === "received" && (
        <>
          {receivedQuery.isPending && <SkeletonList rows={6} />}
          {receivedQuery.isError && (
            <Alert>
              {receivedQuery.error instanceof ApiError ? receivedQuery.error.message : "Could not load notifications."}
            </Alert>
          )}

          {receivedQuery.isSuccess && receivedQuery.data.length === 0 && (
            <EmptyState icon="inbox" title={trimmedSearch ? "No matching notifications" : filter === "unread" ? "You're all caught up" : "No notifications yet"}>
              {trimmedSearch
                ? "Try a different search term."
                : filter === "unread"
                  ? "Nothing new right now — announcements, messages and updates will show up here."
                  : "Announcements, direct messages and system updates for your organization will appear here."}
            </EmptyState>
          )}

          {receivedQuery.isSuccess && receivedQuery.data.length > 0 && (
            <ul className="stack-sm notification-list" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {receivedQuery.data.map((notification) => {
                const isUnread = notification.read_at === null;
                return (
                  <li key={notification.id}>
                    <button
                      type="button"
                      className={`card notification-item notification-item-clickable${isUnread ? " notification-item-unread" : ""}`}
                      onClick={() => openDetail(notification)}
                    >
                      <div className="notification-item-head">
                        <span className={`badge ${TYPE_BADGE[notification.type]}`}>{TYPE_LABEL[notification.type]}</span>
                        <span className="muted notification-item-time">{formatTimestamp(notification.created_at)}</span>
                      </div>
                      <h3 style={{ margin: "0.4rem 0 0.15rem" }}>{notification.title}</h3>
                      <p style={{ margin: 0 }} className="muted">
                        {preview(notification.message)}
                      </p>
                      <div className="notification-item-foot">
                        <span className="muted" style={{ fontSize: "0.78rem" }}>
                          {notification.sender_name ? `From ${notification.sender_name}` : "System"}
                        </span>
                        {isUnread && <span className="notification-dot" aria-hidden="true" />}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}

      {tab === "sent" && (
        <>
          {sentQuery.isPending && <SkeletonList rows={6} />}
          {sentQuery.isError && (
            <Alert>{sentQuery.error instanceof ApiError ? sentQuery.error.message : "Could not load sent messages."}</Alert>
          )}

          {sentQuery.isSuccess && sentQuery.data.length === 0 && (
            <EmptyState icon="send" title={trimmedSearch ? "No matching messages" : "You haven't sent anything yet"}>
              {trimmedSearch
                ? "Try a different search term."
                : "Announcements and direct messages you send will be listed here."}
            </EmptyState>
          )}

          {sentQuery.isSuccess && sentQuery.data.length > 0 && (
            <ul className="stack-sm notification-list" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {sentQuery.data.map((sent) => (
                <li key={sent.id}>
                  <button type="button" className="card notification-item notification-item-clickable" onClick={() => setSentDetail(sent)}>
                    <div className="notification-item-head">
                      <span className={`badge ${TYPE_BADGE[sent.type]}`}>{TYPE_LABEL[sent.type]}</span>
                      <span className="muted notification-item-time">{formatTimestamp(sent.created_at)}</span>
                    </div>
                    <h3 style={{ margin: "0.4rem 0 0.15rem" }}>{sent.title}</h3>
                    <p style={{ margin: 0 }} className="muted">
                      {preview(sent.message)}
                    </p>
                    <div className="notification-item-foot">
                      <span className="muted" style={{ fontSize: "0.78rem" }}>
                        {sent.recipient_name
                          ? `To ${sent.recipient_name}`
                          : `${sent.target_description ?? "Multiple recipients"} · ${sent.recipient_count} recipient(s)`}
                      </span>
                      <span className="muted" style={{ fontSize: "0.78rem" }}>
                        {sent.read_count}/{sent.recipient_count} read
                      </span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {detail && (
        <Modal title={detail.title} onClose={() => setDetail(null)}>
          <div className="stack-sm">
            <div className="notification-item-head">
              <span className={`badge ${TYPE_BADGE[detail.type]}`}>{TYPE_LABEL[detail.type]}</span>
              <span className="muted notification-item-time">{formatTimestamp(detail.created_at)}</span>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              {detail.sender_name ? `From ${detail.sender_name}` : "From System"}
            </p>
            <div className="notification-detail-body">{detail.message}</div>
            {relatedEntityLink(detail) && (
              <Link className="btn btn-ghost btn-sm" to={relatedEntityLink(detail)!.to} onClick={() => setDetail(null)}>
                {relatedEntityLink(detail)!.label}
              </Link>
            )}
            <div className="btn-group" style={{ marginTop: "0.5rem" }}>
              <button type="button" className="btn btn-danger btn-sm" onClick={() => setPendingDelete(detail)}>
                Delete
              </button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDetail(null)}>
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}

      {sentDetail && (
        <Modal title={sentDetail.title} onClose={() => setSentDetail(null)}>
          <div className="stack-sm">
            <div className="notification-item-head">
              <span className={`badge ${TYPE_BADGE[sentDetail.type]}`}>{TYPE_LABEL[sentDetail.type]}</span>
              <span className="muted notification-item-time">{formatTimestamp(sentDetail.created_at)}</span>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              {sentDetail.recipient_name
                ? `Sent to ${sentDetail.recipient_name}`
                : `Sent to: ${sentDetail.target_description ?? "Multiple recipients"} (${sentDetail.recipient_count} recipient(s))`}
            </p>
            <p className="muted" style={{ margin: 0 }}>
              {sentDetail.read_count} of {sentDetail.recipient_count} recipient(s) have read this.
            </p>
            <div className="notification-detail-body">{sentDetail.message}</div>
            <div className="btn-group" style={{ marginTop: "0.5rem" }}>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSentDetail(null)}>
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}

      {composer === "announcement" && (
        <AnnouncementComposer
          token={token}
          onClose={() => setComposer(null)}
          onSent={(count) => {
            showToast(`Announcement sent to ${count} recipient(s).`, "success");
            setComposer(null);
            void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
            void queryClient.invalidateQueries({ queryKey: SENT_QUERY_KEY });
          }}
        />
      )}

      {composer === "message" && (
        <DirectMessageComposer
          token={token}
          onClose={() => setComposer(null)}
          onSent={() => {
            showToast("Message sent.", "success");
            setComposer(null);
            void queryClient.invalidateQueries({ queryKey: SENT_QUERY_KEY });
          }}
        />
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this notification?"
          message="This removes it from your notification center only. This cannot be undone."
          confirmLabel="Delete notification"
          isConfirming={deleteMutation.isPending}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => deleteMutation.mutate(pendingDelete.id)}
        />
      )}
    </div>
  );
}

function AnnouncementComposer({
  token,
  onClose,
  onSent,
}: {
  token: string;
  onClose: () => void;
  onSent: (recipients: number) => void;
}) {
  const [target, setTarget] = useState<AnnouncementTarget>("EVERYONE");
  const [departmentId, setDepartmentId] = useState("");
  const [userIds, setUserIds] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const departmentsQuery = useQuery({
    queryKey: ["recruiter", "departments"],
    queryFn: () => listDepartments(token),
    enabled: target === "DEPARTMENT",
  });
  const usersQuery = useQuery({
    queryKey: ["recruiter", "users"],
    queryFn: () => listUsers(token),
    enabled: target === "EMPLOYEES",
  });

  const sendMutation = useMutation({
    mutationFn: () =>
      sendAnnouncement(
        {
          title,
          message,
          target,
          department_id: target === "DEPARTMENT" ? departmentId || null : null,
          user_ids: target === "EMPLOYEES" ? Array.from(userIds) : [],
        },
        token,
      ),
    onSuccess: (result) => onSent(result.recipients_notified),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  function toggleUser(id: string) {
    setUserIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (target === "DEPARTMENT" && !departmentId) {
      setError("Choose a department.");
      return;
    }
    if (target === "EMPLOYEES" && userIds.size === 0) {
      setError("Select at least one person.");
      return;
    }
    setError(null);
    sendMutation.mutate();
  }

  return (
    <Modal title="Send an announcement" onClose={onClose} wide>
      <form onSubmit={handleSubmit}>
        <label className="field">
          <span>Recipients</span>
          <select value={target} onChange={(e) => setTarget(e.target.value as AnnouncementTarget)}>
            <option value="EVERYONE">Everyone in the organization</option>
            <option value="DEPARTMENT">A department</option>
            <option value="EMPLOYEES">Specific people</option>
          </select>
        </label>
        <p className="field-hint" style={{ marginTop: "-0.5rem" }}>
          You will not receive a notification for your own announcement.
        </p>

        {target === "DEPARTMENT" && (
          <label className="field">
            <span>Department</span>
            {departmentsQuery.isPending ? (
              <Spinner label="Loading departments…" />
            ) : (
              <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                <option value="" disabled>
                  Select a department…
                </option>
                {departmentsQuery.data?.map((dept) => (
                  <option key={dept.id} value={dept.id}>
                    {dept.name}
                  </option>
                ))}
              </select>
            )}
          </label>
        )}

        {target === "EMPLOYEES" && (
          <div className="field">
            <span>People</span>
            {usersQuery.isPending ? (
              <Spinner label="Loading people…" />
            ) : (
              <div className="stack-sm" style={{ maxHeight: "12rem", overflowY: "auto" }}>
                {usersQuery.data?.map((teamUser) => (
                  <label key={teamUser.id} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={userIds.has(teamUser.id)}
                      onChange={() => toggleUser(teamUser.id)}
                    />
                    {teamUser.full_name} <span className="muted">({teamUser.email})</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        )}

        <label className="field">
          <span>Title</span>
          <input required value={title} onChange={(e) => setTitle(e.target.value)} disabled={sendMutation.isPending} />
        </label>
        <label className="field">
          <span>Message</span>
          <textarea
            required
            rows={4}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            disabled={sendMutation.isPending}
            placeholder="e.g. Tomorrow the office will be closed due to maintenance."
          />
        </label>

        {error && <Alert>{error}</Alert>}

        <div className="btn-group" style={{ marginTop: "1rem" }}>
          <button type="submit" className="btn btn-primary" disabled={sendMutation.isPending}>
            {sendMutation.isPending ? <Spinner label="Sending…" /> : "Send announcement"}
          </button>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={sendMutation.isPending}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DirectMessageComposer({
  token,
  onClose,
  onSent,
}: {
  token: string;
  onClose: () => void;
  onSent: () => void;
}) {
  const [recipientId, setRecipientId] = useState("");
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const usersQuery = useQuery({ queryKey: ["recruiter", "users"], queryFn: () => listUsers(token) });

  const sendMutation = useMutation({
    mutationFn: () => sendDirectMessage({ recipient_user_id: recipientId, title, message }, token),
    onSuccess: onSent,
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!recipientId) {
      setError("Choose a recipient.");
      return;
    }
    setError(null);
    sendMutation.mutate();
  }

  return (
    <Modal title="Message a colleague" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <label className="field">
          <span>To</span>
          {usersQuery.isPending ? (
            <Spinner label="Loading people…" />
          ) : (
            <select value={recipientId} onChange={(e) => setRecipientId(e.target.value)}>
              <option value="" disabled>
                Select a person…
              </option>
              {usersQuery.data?.map((teamUser) => (
                <option key={teamUser.id} value={teamUser.id}>
                  {teamUser.full_name} ({teamUser.email})
                </option>
              ))}
            </select>
          )}
        </label>
        <label className="field">
          <span>Title</span>
          <input required value={title} onChange={(e) => setTitle(e.target.value)} disabled={sendMutation.isPending} />
        </label>
        <label className="field">
          <span>Message</span>
          <textarea
            required
            rows={4}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            disabled={sendMutation.isPending}
          />
        </label>

        {error && <Alert>{error}</Alert>}

        <div className="btn-group" style={{ marginTop: "1rem" }}>
          <button type="submit" className="btn btn-primary" disabled={sendMutation.isPending}>
            {sendMutation.isPending ? <Spinner label="Sending…" /> : "Send message"}
          </button>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={sendMutation.isPending}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
