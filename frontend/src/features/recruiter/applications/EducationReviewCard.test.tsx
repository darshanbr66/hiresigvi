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
      // Submitted before the Education-step selections were recorded.
      highest_qualification: null,
      specialization_category: null,
      specialization: null,
      job_fields_of_study: [],
      job_field_mandatory: false,
      matched_field_of_study: null,
      eligibility_review_reasons: [],
      institution: "IIT Madras",
      qualification_year: 2022,
      document: { filename: "mtech.pdf", content_type: "application/pdf", size_bytes: 1, uploaded_at: "" },
      manual_review_reason:
        "AI could not confidently classify the document: the document could not be read clearly.",
      checks: [
        {
          outcome: "UNCLEAR",
          reason_code: "illegible",
          confidence: 0.4,
          candidate_message: "We couldn't read the uploaded document clearly.",
          selected_qualification: null,
          selected_specialization_category: null,
          selected_specialization: null,
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
    expect(screen.getByText("Manual verification required")).toBeInTheDocument();
    expect(
      await screen.findByText(/AI could not confidently classify the document: the document could not be read clearly\./),
    ).toBeInTheDocument();
    expect(await screen.findByText("Unclear · confidence 40%")).toBeInTheDocument();
    expect(screen.getByText("Scan is blurred.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download mtech.pdf" })).toBeInTheDocument();
    expect(screen.getByText(/not a verification of authenticity/)).toBeInTheDocument();
    expect(screen.getByText(/Eligibility needs checking/)).toBeInTheDocument();
    // Legacy record: the profile's qualification text.
    expect(screen.getByText("M.Tech in Mechanical Engineering")).toBeInTheDocument();
    expect(screen.queryByText("Specialization category")).not.toBeInTheDocument();
    // An older flag has no stored reason: the generic notice.
    expect(screen.getByText(/couldn't be matched to this job's requirements automatically/)).toBeInTheDocument();
  });

  it("shows the three Education-step selections and what the document was checked against", async () => {
    vi.spyOn(api, "getApplicationEducation").mockResolvedValue({
      education_verification_status: "VERIFIED",
      eligibility_review_required: false,
      qualification: "M.Tech in Robotics and Automation",
      highest_qualification: "M.Tech",
      specialization_category: "Mechanical & Manufacturing",
      specialization: "Robotics and Automation",
      job_fields_of_study: ["Robotics"],
      job_field_mandatory: true,
      matched_field_of_study: "Robotics",
      eligibility_review_reasons: [],
      institution: "IIT Madras",
      qualification_year: 2022,
      document: { filename: "mtech.pdf", content_type: "application/pdf", size_bytes: 1, uploaded_at: "" },
      manual_review_reason: null,
      checks: [
        {
          outcome: "MATCH",
          reason_code: "match",
          confidence: 0.9,
          candidate_message: "Your document appears consistent with M.Tech.",
          selected_qualification: "M.Tech",
          selected_specialization_category: "Mechanical & Manufacturing",
          selected_specialization: "Robotics and Automation",
          document_type: "MARKS_CARD",
          detected_level: "MASTERS",
          degree_text: "Master of Technology",
          branch_text: "Robotics and Automation",
          institution_text: null,
          name_on_document: null,
          notes: null,
          provider: "gemini",
          model: "m",
          created_at: "",
        },
      ],
    });
    renderCard("VERIFIED");
    expect(screen.getByText("AI verified")).toBeInTheDocument();
    const label = await screen.findByText("Highest qualification");
    expect(label.nextElementSibling).toHaveTextContent("M.Tech");
    expect(screen.getByText("Specialization category").nextElementSibling).toHaveTextContent(
      "Mechanical & Manufacturing",
    );
    expect(screen.getByText("Specialization").nextElementSibling).toHaveTextContent(
      "Robotics and Automation",
    );
    expect(screen.getByText("Checked against").nextElementSibling).toHaveTextContent(
      "M.Tech · Mechanical & Manufacturing · Robotics and Automation",
    );
    expect(screen.getByText("Job's required field of study").nextElementSibling).toHaveTextContent(
      "RoboticsMatches specialization",
    );
    expect(screen.queryByText(/Eligibility needs checking/)).not.toBeInTheDocument();
  });

  it("shows the job's required field and why the application was flagged", async () => {
    const reason =
      "The job requires Mechanical Engineering, and the selected specialization (“Robotics and Automation”) couldn't be matched to it automatically.";
    vi.spyOn(api, "getApplicationEducation").mockResolvedValue({
      education_verification_status: "VERIFIED",
      eligibility_review_required: true,
      qualification: "M.Tech in Robotics and Automation",
      highest_qualification: "M.Tech",
      specialization_category: "Mechanical & Manufacturing",
      specialization: "Robotics and Automation",
      job_fields_of_study: ["Mechanical Engineering"],
      job_field_mandatory: true,
      matched_field_of_study: null,
      eligibility_review_reasons: [reason],
      institution: "IIT Madras",
      qualification_year: 2022,
      document: null,
      manual_review_reason: null,
      checks: [],
    });
    renderCard("VERIFIED");
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.getByText(/Eligibility needs checking/)).toBeInTheDocument();
    expect(screen.queryByText(/couldn't be matched to this job's requirements automatically/)).not.toBeInTheDocument();
    expect(screen.getByText("Job's required field of study").nextElementSibling).toHaveTextContent(
      "Mechanical EngineeringNot matched automatically",
    );
  });
});
