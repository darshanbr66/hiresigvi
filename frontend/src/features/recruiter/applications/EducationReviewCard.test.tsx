import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { EducationReviewCard } from "./EducationReviewCard";

function renderCard(status: "VERIFIED" | "MANUAL_REVIEW") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EducationReviewCard applicationId="app-1" token="tok" status={status} />
    </QueryClientProvider>,
  );
}

describe("EducationReviewCard", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the manual-review flag, the latest check and the document, as advisory", async () => {
    vi.spyOn(api, "getApplicationEducation").mockResolvedValue({
      education_verification_status: "MANUAL_REVIEW",
      eligibility_review_required: true,
      qualification: "M.Tech in Mechanical Engineering",
      institution: "IIT Madras",
      qualification_year: 2022,
      document: { filename: "mtech.pdf", content_type: "application/pdf", size_bytes: 1, uploaded_at: "" },
      checks: [
        {
          outcome: "UNCLEAR",
          reason_code: "illegible",
          confidence: 0.4,
          candidate_message: "We couldn't read the uploaded document clearly.",
          document_type: "MARKS_CARD",
          detected_level: "MASTERS",
          degree_text: "Master of Technology",
          branch_text: null,
          institution_text: null,
          name_on_document: null,
          notes: "Scan is blurred.",
          provider: "gemini",
          model: "m",
          created_at: "",
        },
      ],
    });
    renderCard("MANUAL_REVIEW");
    expect(screen.getByText("Manual review required")).toBeInTheDocument();
    expect(await screen.findByText("Unclear · confidence 40%")).toBeInTheDocument();
    expect(screen.getByText("Scan is blurred.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download mtech.pdf" })).toBeInTheDocument();
    expect(screen.getByText(/not a verification of authenticity/)).toBeInTheDocument();
    expect(screen.getByText(/Eligibility needs checking/)).toBeInTheDocument();
  });
});
