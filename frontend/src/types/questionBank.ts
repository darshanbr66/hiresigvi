/** Mirrors backend/app/schemas/question_bank.py. Staff-only: these shapes
 * carry answer keys and are never used by candidate-facing code. */

import type { QuestionType } from "./assessment";

export type QuestionDifficulty = "EASY" | "MEDIUM" | "HARD";
export type QuestionBankStatus = "DRAFT" | "PUBLISHED" | "ARCHIVED";

export const DIFFICULTY_LABELS: Record<QuestionDifficulty, string> = {
  EASY: "Easy",
  MEDIUM: "Medium",
  HARD: "Hard",
};

export const BANK_STATUS_LABELS: Record<QuestionBankStatus, string> = {
  DRAFT: "Draft",
  PUBLISHED: "Published",
  ARCHIVED: "Archived",
};

export interface CategoryResponse {
  id: string;
  name: string;
  parent_id: string | null;
  description: string | null;
  is_active: boolean;
  path: string;
  question_count: number;
}

export interface TagResponse {
  id: string;
  name: string;
  question_count: number;
}

export interface BankOptionInput {
  label: string;
  is_correct: boolean;
}

export interface BankQuestionContent {
  type: QuestionType;
  prompt: string;
  points: number;
  negative_points: number;
  options: BankOptionInput[];
  accepted_answers: string[];
  case_sensitive: boolean;
  grading_notes: string | null;
  explanation: string | null;
}

export interface BankItemCreateRequest extends BankQuestionContent {
  category_id: string | null;
  difficulty: QuestionDifficulty;
  tags: string[];
  status: "DRAFT" | "PUBLISHED";
}

export interface BankItemUpdateRequest {
  content?: BankQuestionContent;
  category_id?: string | null;
  clear_category?: boolean;
  difficulty?: QuestionDifficulty;
  tags?: string[];
}

export interface BankOptionResponse {
  key: string;
  label: string;
  is_correct: boolean;
}

export interface BankVersionResponse {
  id: string;
  version_number: number;
  type: QuestionType;
  prompt: string;
  points: number;
  negative_points: number;
  options: BankOptionResponse[];
  accepted_answers: string[];
  case_sensitive: boolean;
  grading_notes: string | null;
  explanation: string | null;
  created_at: string;
}

export interface BankItemResponse {
  id: string;
  status: QuestionBankStatus;
  difficulty: QuestionDifficulty;
  category_id: string | null;
  category_path: string | null;
  tags: string[];
  current_version: BankVersionResponse;
  version_count: number;
  assessment_usage_count: number;
  created_at: string;
  updated_at: string;
}

export interface BankItemListEntry {
  id: string;
  status: QuestionBankStatus;
  difficulty: QuestionDifficulty;
  type: QuestionType;
  prompt: string;
  points: number;
  category_id: string | null;
  category_path: string | null;
  tags: string[];
  version_number: number;
  current_version_id: string;
  updated_at: string;
}

export interface BankItemPreview {
  type: QuestionType;
  prompt: string;
  points: number;
  options: { key: string; label: string }[];
}

export interface BankSearchFilters {
  q?: string;
  category_id?: string;
  difficulty?: QuestionDifficulty | "";
  type?: QuestionType | "";
  status?: QuestionBankStatus | "";
  tag_id?: string;
  include_archived?: boolean;
  limit?: number;
  offset?: number;
}

// --- File import (docs/assessment.md § 10a) ---

export interface BankImportCandidate {
  /** "Row 4" / "Question 2" — where it came from in the file. */
  source: string;
  /** null when the question breaks the bank's content rules (see `issue`). */
  content: BankQuestionContent | null;
  prompt: string;
  issue: string | null;
  difficulty: QuestionDifficulty;
  subject: string | null;
  topic: string | null;
  category_path: string | null;
  creates_category: boolean;
  tags: string[];
  duplicate_of_item_id: string | null;
  duplicate_in_file: boolean;
}

export interface BankImportPreview {
  filename: string;
  questions: BankImportCandidate[];
  warnings: string[];
}

export interface BankImportItem {
  content: BankQuestionContent;
  difficulty: QuestionDifficulty;
  subject: string | null;
  topic: string | null;
  tags: string[];
}

export interface BankImportRequest {
  items: BankImportItem[];
  status: "DRAFT" | "PUBLISHED";
}

export interface BankImportResult {
  imported: number;
  item_ids: string[];
  categories_created: number;
  /** What the imported questions were saved as — drafts can't be added to tests. */
  status: QuestionBankStatus;
}

export interface BankBulkStatusResult {
  status: QuestionBankStatus;
  item_ids: string[];
}
