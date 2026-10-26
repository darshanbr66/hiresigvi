import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BankItemListEntry } from "../../../types/questionBank";
import * as bankApi from "../questionBank/api";
import { BankQuestionPicker } from "./BankQuestionPicker";

const published: BankItemListEntry = {
  id: "item-1",
  status: "PUBLISHED",
  difficulty: "EASY",
  type: "MCQ_SINGLE",
  prompt: "What does SQL stand for?",
  points: 1,
  category_id: null,
  category_path: null,
  tags: [],
  version_number: 1,
  current_version_id: "version-1",
  updated_at: new Date().toISOString(),
};

// The app's real defaults: a 30s staleTime is what used to hide questions
// published (in another tab) after the picker had already been opened once.
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });

function renderPicker() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <BankQuestionPicker accessToken="t" alreadySelected={[]} onAdd={() => {}} onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("BankQuestionPicker", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
    vi.spyOn(bankApi, "listCategories").mockResolvedValue([]);
  });

  it("asks only for published questions and lists them", async () => {
    const search = vi.spyOn(bankApi, "searchQuestions").mockResolvedValue({ items: [published], total: 1 });
    renderPicker();
    await screen.findByText("What does SQL stand for?");
    expect(search.mock.calls[0][0]).toMatchObject({ status: "PUBLISHED" });
  });

  it("shows questions published since the picker was last opened", async () => {
    const search = vi
      .spyOn(bankApi, "searchQuestions")
      .mockImplementation(async (filters) =>
        filters.status === "DRAFT" ? { items: [published], total: 1 } : { items: [], total: 0 },
      );
    const first = renderPicker();
    await screen.findByText("No published questions match these filters.");
    await screen.findByText(/1 matching question is still a draft/);
    first.unmount();

    // The recruiter publishes it, then reopens the picker within 30s.
    search.mockResolvedValue({ items: [{ ...published }], total: 1 });
    renderPicker();
    await screen.findByText("What does SQL stand for?");
    await waitFor(() =>
      expect(screen.queryByText("No published questions match these filters.")).not.toBeInTheDocument(),
    );
  });
});
