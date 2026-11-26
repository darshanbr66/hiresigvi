import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import * as downloadBlob from "../../../lib/downloadBlob";
import type { EducationDocument } from "../../../types/recruitment";
import * as api from "./api";
import { EducationDocumentsCard } from "./EducationDocumentsCard";

const doc = (overrides: Partial<EducationDocument>): EducationDocument => ({
  kind: "academic-document",
  label: "Academic document",
  description: "Marks card or certificate for M.Tech",
  required: true,
  uploaded: true,
  available: true,
  filename: "mtech.pdf",
  content_type: "application/pdf",
  size_bytes: 20480,
  uploaded_at: "2026-11-20T10:00:00Z",
  verification: "AI_VERIFIED",
  verification_reason: null,
  ...overrides,
});

const MARKS = doc({
  kind: "tenth-marks-card",
  label: "10th marks card",
  description: "Optional",
  required: false,
  filename: "10th.png",
  content_type: "image/png",
  verification: null,
});

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EducationDocumentsCard applicationId="app-1" token="tok" />
    </QueryClientProvider>,
  );
}

const row = (name: string) => screen.getByText(name).closest("li") as HTMLElement;

describe("EducationDocumentsCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    URL.createObjectURL = vi.fn(() => "blob:preview");
    URL.revokeObjectURL = vi.fn();
  });

  it("lists each document with its filename, size and availability", async () => {
    vi.spyOn(api, "listEducationDocuments").mockResolvedValue({ documents: [doc({}), MARKS] });
    renderCard();
    expect(screen.getByRole("heading", { name: "Education documents" })).toBeInTheDocument();
    await screen.findByText("Academic document");
    const academic = row("Academic document");
    expect(academic).toHaveTextContent("Marks card or certificate for M.Tech");
    expect(academic).toHaveTextContent("mtech.pdf · 20 KB");
    expect(within(academic).getByText("Available")).toBeInTheDocument();
    expect(within(row("10th marks card")).getByText("10th.png", { exact: false })).toBeInTheDocument();
  });

  it("marks an AI-verified academic document as such, with no review reason", async () => {
    vi.spyOn(api, "listEducationDocuments").mockResolvedValue({ documents: [doc({}), MARKS] });
    renderCard();
    const academic = (await screen.findByText("Academic document")).closest("li") as HTMLElement;
    expect(within(academic).getByText("AI verified")).toBeInTheDocument();
    expect(within(academic).queryByText(/Reason for manual review/)).not.toBeInTheDocument();
    // The 10th marks card is never checked: no verification chip.
    expect(within(row("10th marks card")).queryByText("AI verified")).not.toBeInTheDocument();
  });

  it("flags a document needing manual verification, with the reason and secure download", async () => {
    vi.spyOn(api, "listEducationDocuments").mockResolvedValue({
      documents: [
        doc({
          verification: "MANUAL_VERIFICATION_REQUIRED",
          verification_reason: "AI identified a possible document mismatch.",
        }),
      ],
    });
    renderCard();
    const academic = (await screen.findByText("Academic document")).closest("li") as HTMLElement;
    expect(within(academic).getByText("Manual verification required")).toBeInTheDocument();
    expect(academic).toHaveTextContent(
      "Reason for manual review: AI identified a possible document mismatch.",
    );
    expect(academic).toHaveTextContent("mtech.pdf");
    expect(academic).toHaveTextContent("uploaded");
    expect(screen.getByRole("button", { name: "Download academic document" })).toBeInTheDocument();
  });

  it("downloads and views a document through the authorized endpoint", async () => {
    vi.spyOn(api, "listEducationDocuments").mockResolvedValue({ documents: [doc({}), MARKS] });
    const blob = new Blob(["%PDF"], { type: "application/pdf" });
    const fetchDoc = vi
      .spyOn(api, "downloadEducationDocument")
      .mockResolvedValue({ blob, filename: "mtech.pdf" });
    const save = vi.spyOn(downloadBlob, "triggerBlobDownload").mockImplementation(() => undefined);
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Download academic document" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(blob, "mtech.pdf"));
    expect(fetchDoc).toHaveBeenCalledWith("app-1", "academic-document", "tok");

    fireEvent.click(screen.getByRole("button", { name: "View 10th marks card" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await waitFor(() => expect(fetchDoc).toHaveBeenLastCalledWith("app-1", "tenth-marks-card", "tok"));
  });

  it("handles documents that are missing, optional or unavailable", async () => {
    vi.spyOn(api, "listEducationDocuments").mockResolvedValue({
      documents: [
        doc({ available: false }),
        doc({ ...MARKS, uploaded: false, available: false, filename: null, size_bytes: null }),
      ],
    });
    renderCard();
    const academic = await screen.findByText("Academic document");
    expect(within(academic.closest("li") as HTMLElement).getByText("File unavailable")).toBeInTheDocument();
    expect(within(row("10th marks card")).getByText("Not uploaded (optional)")).toBeInTheDocument();
    // Nothing to view or download.
    expect(screen.queryByRole("button", { name: /View|Download/ })).not.toBeInTheDocument();
  });

  it("shows the server's message when a download fails", async () => {
    vi.spyOn(api, "listEducationDocuments").mockResolvedValue({ documents: [doc({})] });
    vi.spyOn(api, "downloadEducationDocument").mockRejectedValue(
      new ApiError("The document file could not be found in storage.", 404, "not_found"),
    );
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Download academic document" }));
    expect(await screen.findByText("The document file could not be found in storage.")).toBeInTheDocument();
  });
});
