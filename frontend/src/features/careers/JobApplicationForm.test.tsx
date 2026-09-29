import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/apiClient";
import type { PublicApplicationResult } from "../../types/careers";
import * as careersApi from "./api";
import { JobApplicationForm } from "./JobApplicationForm";

function renderForm() {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <JobApplicationForm slug="acme" jobId="job-1" contactEmail="careers@acme.test" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function submitCurrentStep() {
  // jsdom doesn't enforce `required` on submit; each step's own checks run.
  fireEvent.submit(screen.getByRole("button", { name: /continue/i }).closest("form")!);
}

function fillDetails({ experienced = false } = {}) {
  fill(/Full name/, "Eli Experienced");
  fill(/^Email/, "eli@example.com");
  fill(/Mobile number/, "+91 98765 43210");
  fill(/Date of birth/, "1995-06-01");
  fill(/Place of birth/, "Pune");
  fill(/Languages known/, "English");
  fireEvent.keyDown(screen.getByLabelText(/Languages known/), { key: "Enter" });
  fill(/I am a/, experienced ? "EXPERIENCED" : "FRESHER");
  if (experienced) {
    fill(/Total experience/, "6");
    fill(/Notice period/, "30");
    fill(/Current job title/, "Senior Engineer");
    fill(/Current company/, "Globex");
  }
  fill(/Current location/, "Pune");
  fill(/Preferred location/, "Bengaluru");
  fill(/Highest qualification/, "B.Tech");
  fill(/LinkedIn/, "https://www.linkedin.com/in/eli");
  fill(/GitHub/, "https://github.com/eli");
}

async function verifyEmail() {
  vi.spyOn(careersApi, "requestEmailCode").mockResolvedValue({
    expires_in_seconds: 600,
    resend_available_in_seconds: 60,
  });
  vi.spyOn(careersApi, "verifyEmailCode").mockResolvedValue({
    verification_token: "verified-token",
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  });
  fireEvent.click(await screen.findByRole("button", { name: "Send verification code" }));
  await screen.findByLabelText(/Verification code/);
  fill(/Verification code/, "123456");
  fireEvent.click(await screen.findByRole("button", { name: "Verify email" }));
  // A valid code lands directly on the resume step.
  expect(await screen.findByLabelText(/Resume \(PDF only\)/)).toBeInTheDocument();
}

function attachResume(name = "resume.pdf") {
  const file = new File(["%PDF-1.4"], name, { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText(/Resume \(PDF only\)/), { target: { files: [file] } });
  return file;
}

const result = (overrides: Partial<PublicApplicationResult> = {}): PublicApplicationResult => ({
  id: "app-1",
  job_title: "Backend Engineer",
  candidate_email: "eli@example.com",
  confirmation_email_sent: true,
  careers_contact_email: "careers@acme.test",
  submitted_at: new Date().toISOString(),
  ...overrides,
});

async function completeFlow() {
  fillDetails({ experienced: true });
  submitCurrentStep();
  await verifyEmail();
  const file = attachResume();
  submitCurrentStep();
  fireEvent.click(await screen.findByRole("button", { name: "Submit application" }));
  return file;
}

describe("JobApplicationForm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("marks every field mandatory and shows experience fields only for experienced candidates", () => {
    renderForm();

    for (const label of [/Full name/, /^Email/, /Mobile number/, /Date of birth/, /Place of birth/, /Current location/, /Preferred location/, /Highest qualification/, /LinkedIn/, /GitHub/]) {
      expect(screen.getByLabelText(label)).toBeRequired();
    }
    expect(screen.queryByLabelText(/Total experience/)).not.toBeInTheDocument();

    fill(/I am a/, "EXPERIENCED");
    expect(screen.getByLabelText(/Total experience/)).toBeRequired();
    expect(screen.getByLabelText(/Current job title/)).toBeRequired();
    expect(screen.getByLabelText(/Current company/)).toBeRequired();
  });

  it("requires a language and a candidate type before continuing", async () => {
    renderForm();
    fill(/Full name/, "Someone");
    submitCurrentStep();
    expect(await screen.findByText(/fresher or an experienced professional/)).toBeInTheDocument();

    fill(/I am a/, "FRESHER");
    submitCurrentStep();
    expect(await screen.findByText(/at least one language/)).toBeInTheDocument();
  });

  it("moves straight to the resume upload after a valid code, with no separate verified screen", async () => {
    renderForm();
    fillDetails();
    submitCurrentStep();

    expect(await screen.findByText("Not verified yet")).toBeInTheDocument();
    // Nothing to click past on the verification step.
    expect(screen.queryByRole("button", { name: /continue/i })).not.toBeInTheDocument();

    await verifyEmail();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(/Resume/);
    expect(screen.queryByText("Email verified")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Verify email" })).not.toBeInTheDocument();
  });

  it("doesn't ask for verification again when a verified candidate goes back to edit details", async () => {
    renderForm();
    fillDetails();
    submitCurrentStep();
    await verifyEmail();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByLabelText(/Full name/)).toBeInTheDocument();
    submitCurrentStep();
    expect(await screen.findByLabelText(/Resume \(PDF only\)/)).toBeInTheDocument();
  });

  it.each([
    ["an incorrect", "Incorrect code. 4 attempts remaining.", "otp_incorrect"],
    ["an expired", "This code has expired. Please request a new one.", "otp_expired"],
  ])("stays on verification with the server's message for %s code", async (_kind, message, code) => {
    renderForm();
    fillDetails();
    submitCurrentStep();
    vi.spyOn(careersApi, "requestEmailCode").mockResolvedValue({
      expires_in_seconds: 600,
      resend_available_in_seconds: 60,
    });
    vi.spyOn(careersApi, "verifyEmailCode").mockRejectedValue(new ApiError(message, 422, code));
    fireEvent.click(await screen.findByRole("button", { name: "Send verification code" }));
    await screen.findByLabelText(/Verification code/);
    fill(/Verification code/, "000000");
    fireEvent.click(screen.getByRole("button", { name: "Verify email" }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByLabelText(/Verification code/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Resume \(PDF only\)/)).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(/Verify/i);
  });

  it("shows the server's message for a wrong code", async () => {
    renderForm();
    fillDetails();
    submitCurrentStep();
    vi.spyOn(careersApi, "requestEmailCode").mockResolvedValue({
      expires_in_seconds: 600,
      resend_available_in_seconds: 60,
    });
    vi.spyOn(careersApi, "verifyEmailCode").mockRejectedValue(
      new ApiError("Incorrect code. 4 attempts remaining.", 422, "otp_incorrect"),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Send verification code" }));
    expect(await screen.findByRole("button", { name: /Resend code in/ })).toBeDisabled();
    fill(/Verification code/, "000000");
    fireEvent.click(screen.getByRole("button", { name: "Verify email" }));

    expect(await screen.findByText("Incorrect code. 4 attempts remaining.")).toBeInTheDocument();
  });

  it("accepts PDF resumes only", async () => {
    renderForm();
    fillDetails();
    submitCurrentStep();
    await verifyEmail();

    attachResume("resume.docx");
    expect(await screen.findByText(/upload your resume as a PDF/)).toBeInTheDocument();
  });

  it("submits the full profile with the verification token and confirms only a sent email", async () => {
    const apply = vi.spyOn(careersApi, "applyToJob").mockResolvedValue(result());
    renderForm();

    const file = await completeFlow();

    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply).toHaveBeenCalledWith(
      "acme",
      "job-1",
      expect.objectContaining({
        candidate_type: "EXPERIENCED",
        phone: "+91 98765 43210",
        date_of_birth: "1995-06-01",
        place_of_birth: "Pune",
        languages: ["English"],
        years_experience: "6",
        current_title: "Senior Engineer",
      }),
      file,
      "verified-token",
    );
    expect(await screen.findByRole("heading", { name: "Application Submitted" })).toBeInTheDocument();
    expect(screen.getByText("Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText(/successfully submitted and will be reviewed/)).toBeInTheDocument();
    expect(screen.getByText(/we will contact you by email/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Careers" })).toHaveAttribute("href", "/org/acme");
    expect(screen.getByText(/confirmation email has been sent/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "careers@acme.test" })).toHaveAttribute(
      "href",
      "mailto:careers@acme.test",
    );
  });

  it("never claims an email was sent when it wasn't", async () => {
    vi.spyOn(careersApi, "applyToJob").mockResolvedValue(result({ confirmation_email_sent: false }));
    renderForm();
    await completeFlow();

    expect(await screen.findByRole("heading", { name: "Application Submitted" })).toBeInTheDocument();
    expect(screen.queryByText(/confirmation email has been sent/)).not.toBeInTheDocument();
    expect(screen.getByText(/couldn't send a confirmation email/)).toBeInTheDocument();
  });

  it("never shows the candidate a screening result or decision, whatever the backend sends", async () => {
    // The API no longer returns any screening data; even a stale backend
    // still sending the old outcome field (or anything similar) must not
    // change what the candidate sees.
    const legacy = {
      ...result(),
      outcome: "NOT_SHORTLISTED_FOR_ROLE",
      decision: "NOT_MATCH",
      overall_score: 42,
      missing_requirements: ["React.js"],
      status: "AI_SCREENED_OUT",
    } as PublicApplicationResult;
    vi.spyOn(careersApi, "applyToJob").mockResolvedValue(legacy);
    renderForm();
    await completeFlow();

    expect(await screen.findByRole("heading", { name: "Application Submitted" })).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    for (const leaked of [
      /\bMATCH\b/,
      /NOT[ _]MATCH/i,
      /\bAI\b/,
      /score/i,
      /%/,
      /skills?/i,
      /React\.js/,
      /screen(ed|ing)/i,
      /not eligible/i,
      /shortlist/i,
      /rejected/i,
      /recommend/i,
      /congratulations/i,
      /selected/i,
    ]) {
      expect(text).not.toMatch(leaked);
    }
  });

  it.each([
    ["mobile", 375],
    ["desktop", 1280],
  ])("shows the same complete confirmation on a %s viewport", async (_label, width) => {
    // jsdom has no CSS layout engine: this checks the confirmation's content
    // and actions render at either width; the responsive styling itself is
    // the @media rule in styles/public.css.
    vi.stubGlobal("innerWidth", width);
    vi.spyOn(careersApi, "applyToJob").mockResolvedValue(result());
    renderForm();
    await completeFlow();

    const heading = await screen.findByRole("heading", { name: "Application Submitted" });
    expect(heading.closest('[role="status"]')).toHaveClass("application-outcome-success");
    expect(screen.getByRole("link", { name: "Back to Careers" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("tells a returning candidate they've already applied", async () => {
    vi.spyOn(careersApi, "applyToJob").mockRejectedValue(
      new ApiError("An application has already been submitted with the details provided.", 409, "already_registered"),
    );
    renderForm();
    await completeFlow();

    expect(await screen.findByText("You've already applied")).toBeInTheDocument();
  });

  it("uses the campus drive's own verification and apply endpoints for a drive link", async () => {
    const requestCode = vi.spyOn(careersApi, "requestCampusEmailCode").mockResolvedValue({
      expires_in_seconds: 600,
      resend_available_in_seconds: 60,
    });
    const verifyCode = vi.spyOn(careersApi, "verifyCampusEmailCode").mockResolvedValue({
      verification_token: "campus-token",
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    });
    const careersRequest = vi.spyOn(careersApi, "requestEmailCode");
    const apply = vi
      .spyOn(careersApi, "applyToCampusDrive")
      .mockResolvedValue({ ...result(), assessment_invitation_link: "/assessment/abc" });
    const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <JobApplicationForm campusToken="drive-tok" title="Apply for this drive" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("heading", { name: "Apply for this drive" })).toBeInTheDocument();
    fillDetails();
    submitCurrentStep();
    fireEvent.click(await screen.findByRole("button", { name: "Send verification code" }));
    await screen.findByLabelText(/Verification code/);
    fill(/Verification code/, "123456");
    fireEvent.click(await screen.findByRole("button", { name: "Verify email" }));
    expect(await screen.findByLabelText(/Resume \(PDF only\)/)).toBeInTheDocument();
    expect(requestCode).toHaveBeenCalledWith("drive-tok", "eli@example.com");
    expect(verifyCode).toHaveBeenCalledWith("drive-tok", "eli@example.com", "123456");
    expect(careersRequest).not.toHaveBeenCalled();

    const file = attachResume();
    submitCurrentStep();
    fireEvent.click(await screen.findByRole("button", { name: "Submit application" }));

    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply).toHaveBeenCalledWith(
      "drive-tok",
      expect.objectContaining({ email: "eli@example.com", place_of_birth: "Pune" }),
      file,
      "campus-token",
    );
    expect(await screen.findByRole("link", { name: "Start assessment" })).toHaveAttribute(
      "href",
      "/assessment/abc",
    );
    expect(screen.getByRole("heading", { name: "Application Submitted" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Careers" })).toHaveAttribute("href", "/");
  });
});
