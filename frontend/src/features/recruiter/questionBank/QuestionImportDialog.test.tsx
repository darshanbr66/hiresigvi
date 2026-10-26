import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import * as download from "../../../lib/downloadBlob";
import type { BankImportCandidate, BankImportPreview } from "../../../types/questionBank";
import * as api from "./api";
import { QuestionImportDialog } from "./QuestionImportDialog";

function candidate(overrides: Partial<BankImportCandidate>): BankImportCandidate {
  return {
    source: "Row 2",
    prompt: "Which keyword defines a function in Python?",
    content: {
      type: "MCQ_SINGLE",
      prompt: "Which keyword defines a function in Python?",
      points: 2,
      negative_points: 0,
      options: [
        { label: "def", is_correct: true },
        { label: "func", is_correct: false },
      ],
      accepted_answers: [],
      case_sensitive: false,
      grading_notes: null,
      explanation: null,
    },
    issue: null,
    difficulty: "EASY",
    subject: "Programming",
    topic: "Python",
    category_path: "Programming › Python",
    creates_category: true,
    tags: ["basics"],
    duplicate_of_item_id: null,
    duplicate_in_file: false,
    ...overrides,
  };
}

const preview: BankImportPreview = {
  filename: "questions.csv",
  questions: [
    candidate({}),
    candidate({ source: "Row 3", prompt: "Already there?", duplicate_of_item_id: "item-9" }),
    candidate({ source: "Row 4", prompt: "Two answers?", content: null, issue: "Mark exactly one correct option." }),
  ],
  warnings: ["Row 5: skipped — no correct answer given."],
};

function renderDialog(onImported = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <QuestionImportDialog accessToken="test-token" onClose={() => {}} onImported={onImported} />
    </QueryClientProvider>,
  );
  return onImported;
}

function chooseFile(name = "questions.csv") {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], name, { type: "text/csv" })] } });
}

describe("QuestionImportDialog", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("explains the formats and offers a real template download", async () => {
    const getTemplate = vi
      .spyOn(api, "downloadImportTemplate")
      .mockResolvedValue({ blob: new Blob(["question"]), filename: "question-bank-import-template.csv" });
    const save = vi.spyOn(download, "triggerBlobDownload").mockImplementation(() => {});

    renderDialog();

    expect(screen.getByText(/Upload a PDF, DOCX, XLSX or CSV file/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose file to import" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download CSV template" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.any(Blob), "question-bank-import-template.csv"));
    expect(getTemplate).toHaveBeenCalledWith("test-token");
  });

  it("previews what was read, leaves duplicates and broken rows out by default", async () => {
    vi.spyOn(api, "previewImport").mockResolvedValue(preview);

    renderDialog();
    chooseFile();

    expect(await screen.findByText(/2 questions ready to import/)).toBeInTheDocument();
    expect(screen.getByText(/1 need fixing in the file/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing has been saved yet/)).toBeInTheDocument();
    expect(screen.getByText("Row 5: skipped — no correct answer given.")).toBeInTheDocument();
    expect(screen.getByText("Already in the bank with the same text.")).toBeInTheDocument();
    expect(screen.getByText("Can't import: Mark exactly one correct option.")).toBeInTheDocument();

    expect(screen.getByLabelText("Import Row 2")).toBeChecked();
    expect(screen.getByLabelText("Import Row 3")).not.toBeChecked();
    expect(screen.getByLabelText("Import Row 4")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import 1 question" })).toBeEnabled();
  });

  it("imports only the ticked questions with the chosen status", async () => {
    vi.spyOn(api, "previewImport").mockResolvedValue(preview);
    const importSpy = vi
      .spyOn(api, "importQuestions")
      .mockResolvedValue({ imported: 2, item_ids: ["a", "b"], categories_created: 2, status: "PUBLISHED" });
    const onImported = renderDialog();
    chooseFile();

    fireEvent.click(await screen.findByLabelText("Import Row 3"));
    fireEvent.click(screen.getByLabelText(/Published — available to tests/));
    fireEvent.click(screen.getByRole("button", { name: "Import 2 questions" }));

    await waitFor(() => expect(onImported).toHaveBeenCalledWith(2, 2, "PUBLISHED"));
    const [payload, token] = importSpy.mock.calls[0];
    expect(token).toBe("test-token");
    expect(payload.status).toBe("PUBLISHED");
    expect(payload.items.map((i) => i.content.prompt)).toHaveLength(2);
    expect(payload.items[0]).toMatchObject({ subject: "Programming", topic: "Python", difficulty: "EASY", tags: ["basics"] });
  });

  it("shows the server's explanation when a file can't be read", async () => {
    vi.spyOn(api, "previewImport").mockRejectedValue(
      new ApiError("This file's contents don't match a .pdf file.", 400, "question_import_failed"),
    );

    renderDialog();
    chooseFile("questions.pdf");

    expect(await screen.findByRole("alert")).toHaveTextContent("This file's contents don't match a .pdf file.");
    expect(screen.queryByText(/ready to import/)).not.toBeInTheDocument();
  });

  it("keeps the preview and reports the failure if saving fails", async () => {
    vi.spyOn(api, "previewImport").mockResolvedValue(preview);
    vi.spyOn(api, "importQuestions").mockRejectedValue(
      new ApiError("We couldn't complete this request. Please try again.", 500, "internal_error"),
    );
    const onImported = renderDialog();
    chooseFile();

    fireEvent.click(await screen.findByRole("button", { name: "Import 1 question" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("We couldn't complete this request.");
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.getByText(/ready to import/)).toBeInTheDocument();
  });
});
