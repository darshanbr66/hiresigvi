/** Mirrors backend/app/schemas/report.py. */

export interface StatusCount {
  status: string;
  count: number;
}

export interface JobApplicationCount {
  job_id: string;
  job_title: string;
  count: number;
}

export interface ScreeningSummary {
  total_runs: number;
  completed: number;
  failed: number;
  /** 0 when no completed run carries a score. */
  average_score: number;
}

export interface AssessmentSummaryStats {
  total_invitations: number;
  submitted: number;
  passed: number;
}

export interface CampusDriveCount {
  drive_id: string;
  drive_name: string;
  application_count: number;
}

export interface ReportOverview {
  total_jobs: number;
  open_jobs: number;
  total_candidates: number;
  total_applications: number;
  selected_candidates: number;
  rejected_candidates: number;
  hired_candidates: number;
  applications_by_status: StatusCount[];
  applications_by_job: JobApplicationCount[];
  screening: ScreeningSummary;
  assessments: AssessmentSummaryStats;
  campus_drives: CampusDriveCount[];
}
