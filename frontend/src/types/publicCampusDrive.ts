/** Mirrors backend/app/schemas/public_campus_drive.py. */

import type { PublicApplicationResult } from "./careers";
import type { CampusDriveStatus } from "./campusDrive";

export interface PublicCampusDriveView {
  kind: "drive";
  name: string;
  college_name: string;
  description: string | null;
  job_title: string;
  job_description: string;
  organization_name: string;
  registration_deadline: string | null;
  status: CampusDriveStatus;
  has_assessment: boolean;
  /** The recruitment team's published contact address (null = not published). */
  careers_contact_email: string | null;
}

/** Returned once a drive is CLOSED — deliberately carries no JD, drive
 * description, or assessment info (SIGVITAS platform overhaul § 16). */
export interface PublicCampusDriveUnavailable {
  kind: "unavailable";
  message: string;
}

export type PublicCampusDriveResult = PublicCampusDriveView | PublicCampusDriveUnavailable;

/** The careers-site confirmation (no screening outcome) plus the drive's assessment
 * link — set only when the application was fast-tracked into the drive's
 * default assessment. */
export interface PublicCampusDriveApplicationResult extends PublicApplicationResult {
  assessment_invitation_link: string | null;
}
