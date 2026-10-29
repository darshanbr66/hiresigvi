/** Mirrors backend/app/schemas/notification.py. */

export type NotificationType =
  | "ASSESSMENT_STARTED"
  | "ASSESSMENT_SUBMITTED"
  | "ANNOUNCEMENT"
  | "DIRECT_MESSAGE"
  | "CALENDAR_REMINDER"
  | "INTERVIEW_ASSIGNED";

export interface NotificationResponse {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  created_at: string;
  read_at: string | null;
  sender_id: string | null;
  sender_name: string | null;
  related_entity_type: string | null;
  related_entity_id: string | null;
}

export interface NotificationAcknowledgeResult {
  updated: number;
}

/** The Notification Center's "Sent" tab — one entry per broadcast (an
 * announcement's whole fan-out counts as one), never one per recipient. */
export interface SentNotificationResponse {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  created_at: string;
  target_description: string | null;
  recipient_count: number;
  read_count: number;
  recipient_name: string | null;
}

export interface UnreadCountResponse {
  unread: number;
}

export type AnnouncementTarget = "EVERYONE" | "DEPARTMENT" | "EMPLOYEES";

export interface AnnouncementCreateRequest {
  title: string;
  message: string;
  target: AnnouncementTarget;
  department_id?: string | null;
  user_ids?: string[];
}

export interface AnnouncementResult {
  recipients_notified: number;
}

export interface DirectMessageCreateRequest {
  recipient_user_id: string;
  title: string;
  message: string;
}
