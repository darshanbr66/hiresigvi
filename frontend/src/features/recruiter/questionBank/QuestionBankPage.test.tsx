import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { BankItemListEntry, BankItemResponse } from "../../../types/questionBank";
import * as api from "./api";
import { QuestionBankPage } from "./QuestionBankPage";

const auth = vi.hoisted(() => ({ roles: ["RECRUITER"] as string[] }));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: auth.roles } }),
}));

const entry: BankItemListEntry = {
  id: "item-1",
  status: "DRAFT",
  difficulty: "EASY",
  type: "MCQ_SINGLE",
  prompt: "Which keyword defines a function in Python?",
  points: 2,
  category_id: "cat-1",
  category_path: "Programming / Python",
  tags: ["basics"],
  version_number: 1,
  current_version_id: "ver-1",
  updated_at: new Date().toISOString(),
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>
          <QuestionBankPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("QuestionBankPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    auth.roles = ["RECRUITER"];
    vi.spyOn(api, "listCategories").mockResolvedValue([
      {
        id: "cat-1",
        name: "Python",
        parent_id: null,
        description: null,
        is_active: true,
        path: "Programming / Python",
        question_count: 1,
      },
    ]);
    vi.spyOn(api, "listTags").mockResolvedValue([{ id: "tag-1", name: "basics", question_count: 1 }]);
  });

  it("lists questions and sends filters to the server, back on page 1", async () => {
    const search = vi.spyOn(api, "searchQuestions").mockResolvedValue({ items: [entry], total: 1 });

    renderPage();

    expect(await screen.findByText(entry.prompt)).toBeInTheDocument();
    expect(screen.getByText("Programming / Python", { selector: "td" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "HARD" } });
    await waitFor(() =>
      expect(search).toHaveBeenLastCalledWith(
        expect.objectContaining({ difficulty: "HARD", offset: 0 }),
        "test-token",
      ),
    );

    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "tag-1" } });
    await waitFor(() =>
      expect(search).toHaveBeenLastCalledWith(expect.objectContaining({ tag_id: "tag-1" }), "test-token"),
    );
  });

  it("previews a question with the candidate's view only", async () => {
    vi.spyOn(api, "searchQuestions").mockResolvedValue({ items: [entry], total: 1 });
    const preview = vi.spyOn(api, "previewQuestion").mockResolvedValue({
      type: "MCQ_SINGLE",
      prompt: entry.prompt,
      points: 2,
      options: [
        { key: "a", label: "def" },
        { key: "b", label: "func" },
      ],
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Preview" }));

    const dialog = await screen.findByRole("dialog", { name: "Candidate preview" });
    expect(await within(dialog).findByText("def")).toBeInTheDocument();
    expect(within(dialog).getByText(/no correct answers or notes/)).toBeInTheDocument();
    expect(preview).toHaveBeenCalledWith("item-1", "test-token");
  });

  it("publishes a draft through the status endpoint", async () => {
    vi.spyOn(api, "searchQuestions").mockResolvedValue({ items: [entry], total: 1 });
    const change = vi
      .spyOn(api, "changeQuestionStatus")
      .mockResolvedValue({ status: "PUBLISHED" } as BankItemResponse);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Publish" }));

    await waitFor(() => expect(change).toHaveBeenCalledWith("item-1", "PUBLISHED", "test-token"));
  });

  it("offers no create, edit or status controls to a read-only role", async () => {
    auth.roles = ["HIRING_MANAGER"];
    vi.spyOn(api, "searchQuestions").mockResolvedValue({ items: [entry], total: 1 });

    renderPage();

    expect(await screen.findByText(entry.prompt)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ New question" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    // Preview stays available: it shows nothing a candidate wouldn't see.
    expect(screen.getByRole("button", { name: "Preview" })).toBeInTheDocument();
  });

  it("shows a permission message on 403 instead of the list", async () => {
    const { ApiError } = await import("../../../lib/apiClient");
    vi.spyOn(api, "searchQuestions").mockRejectedValue(new ApiError("Forbidden", 403, "FORBIDDEN"));

    renderPage();

    expect(await screen.findByText("You do not have permission to view the question bank.")).toBeInTheDocument();
  });
});
