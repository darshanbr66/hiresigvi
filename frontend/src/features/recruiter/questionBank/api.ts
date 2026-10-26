import { apiClient } from "../../../lib/apiClient";
import type {
  BankBulkStatusResult,
  BankImportPreview,
  BankImportRequest,
  BankImportResult,
  BankItemCreateRequest,
  BankItemListEntry,
  BankItemPreview,
  BankItemResponse,
  BankItemUpdateRequest,
  BankSearchFilters,
  BankVersionResponse,
  CategoryResponse,
  QuestionBankStatus,
  TagResponse,
} from "../../../types/questionBank";

const BASE = "/api/v1/recruiter/question-bank";

export function listCategories(accessToken: string) {
  return apiClient.get<CategoryResponse[]>(`${BASE}/categories`, accessToken);
}

export function createCategory(
  payload: { name: string; parent_id: string | null; description?: string | null },
  accessToken: string,
) {
  return apiClient.post<CategoryResponse>(`${BASE}/categories`, payload, accessToken);
}

export function updateCategory(
  categoryId: string,
  payload: { name?: string; description?: string | null; is_active?: boolean },
  accessToken: string,
) {
  return apiClient.patch<CategoryResponse>(`${BASE}/categories/${categoryId}`, payload, accessToken);
}

export function listTags(accessToken: string) {
  return apiClient.get<TagResponse[]>(`${BASE}/tags`, accessToken);
}

export function searchQuestions(filters: BankSearchFilters, accessToken: string) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    params.set(key, String(value));
  }
  const query = params.toString();
  return apiClient.getPage<BankItemListEntry>(
    `${BASE}/questions${query ? `?${query}` : ""}`,
    accessToken,
  );
}

export function getQuestion(itemId: string, accessToken: string) {
  return apiClient.get<BankItemResponse>(`${BASE}/questions/${itemId}`, accessToken);
}

export function listQuestionVersions(itemId: string, accessToken: string) {
  return apiClient.get<BankVersionResponse[]>(`${BASE}/questions/${itemId}/versions`, accessToken);
}

export function previewQuestion(itemId: string, accessToken: string) {
  return apiClient.get<BankItemPreview>(`${BASE}/questions/${itemId}/preview`, accessToken);
}

export function createQuestion(payload: BankItemCreateRequest, accessToken: string) {
  return apiClient.post<BankItemResponse>(`${BASE}/questions`, payload, accessToken);
}

export function updateQuestion(itemId: string, payload: BankItemUpdateRequest, accessToken: string) {
  return apiClient.patch<BankItemResponse>(`${BASE}/questions/${itemId}`, payload, accessToken);
}

export function changeQuestionStatus(itemId: string, status: QuestionBankStatus, accessToken: string) {
  return apiClient.post<BankItemResponse>(
    `${BASE}/questions/${itemId}/status`,
    { status },
    accessToken,
  );
}

/** All or nothing: every selected question moves, or none does. */
export function changeQuestionsStatus(itemIds: string[], status: QuestionBankStatus, accessToken: string) {
  return apiClient.post<BankBulkStatusResult>(
    `${BASE}/questions/bulk-status`,
    { item_ids: itemIds, status },
    accessToken,
  );
}

// --- File import ---

export function previewImport(file: File, accessToken: string) {
  const formData = new FormData();
  formData.append("file", file);
  return apiClient.postForm<BankImportPreview>(`${BASE}/import/preview`, formData, accessToken);
}

export function importQuestions(payload: BankImportRequest, accessToken: string) {
  return apiClient.post<BankImportResult>(`${BASE}/import`, payload, accessToken);
}

export function downloadImportTemplate(accessToken: string) {
  return apiClient.getBlob(`${BASE}/import/template`, accessToken);
}
