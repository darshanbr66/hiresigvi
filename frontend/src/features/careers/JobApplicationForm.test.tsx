import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "../../lib/apiClient";
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
    // The early "already applied?" check: nobody has, unless a test says so.
    vi.spyOn(careersApi, "checkApplicantStatus").mockResolvedValue({ already_applied: false });
    vi.spyOn(careersApi, "checkCampusApplicantStatus").mockResolvedValue({ already_applied: false });
  });

  it("marks the mandatory fields and shows experience fields only for experienced candidates", () => {
    renderForm();

    for (const label of [/Full name/, /^Email/, /Mobile number/, /Date of birth/, /Place of birth/, /Current location/, /Preferred location/, /Highest qualification/]) {
      expect(screen.getByLabelText(label)).toBeRequired();
    }
    // LinkedIn, GitHub and the 10th marks are optional — and say so.
    for (const label of [/LinkedIn/, /GitHub/, /^10th Marks \(Optional\)/, /10th Marks Card/]) {
      const input = screen.getByLabelText(label);
      expect(input).not.toBeRequired();
      expect(input.closest(".field")).toHaveTextContent("(Optional)");
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
      null,
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

  describe("early 'already applied' check", () => {
    const continueButton = () => screen.getByRole("button", { name: /continue/i });

    it("shows 'You have already applied.' directly below the Email field, with Continue disabled", async () => {
      const check = vi
        .spyOn(careersApi, "checkApplicantStatus")
        .mockResolvedValue({ already_applied: true });
      renderForm();
      fillDetails();

      const message = await screen.findByText("You have already applied.");
      expect(continueButton()).toBeDisabled();
      // Directly below the Email input, in the Email field — not at the
      // bottom of the form — and announced as the input's description.
      const email = screen.getByLabelText(/^Email/);
      expect(message.closest(".field")).toBe(email.closest(".field"));
      expect(email.nextElementSibling).toBe(message);
      expect(email).toHaveAttribute("aria-describedby", message.id);
      expect(email).toHaveAttribute("aria-invalid", "true");
      // Shown once only (no second copy next to the button).
      expect(screen.getAllByText("You have already applied.")).toHaveLength(1);
      // Email and mobile are checked separately — one identifier per request.
      expect(check).toHaveBeenCalledWith("acme", { email: "eli@example.com", job_id: "job-1" });
      expect(check).toHaveBeenCalledWith("acme", { phone: "+91 98765 43210", job_id: "job-1" });
      // Both blocked: one message, under Email, described for both inputs.
      expect(screen.getByLabelText(/Mobile number/)).toHaveAttribute("aria-describedby", message.id);
      // Nothing private comes back or is shown — only the message.
      expect(screen.queryByText(/eligible|reapply|applied on/i)).not.toBeInTheDocument();
    });

    it("never lets a blocked candidate reach the code, resume or submit steps", async () => {
      vi.spyOn(careersApi, "checkApplicantStatus").mockResolvedValue({ already_applied: true });
      const requestCode = vi.spyOn(careersApi, "requestEmailCode");
      renderForm();
      fillDetails();
      await screen.findByText("You have already applied.");

      submitCurrentStep(); // even forcing the form's submit
      expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(/Your details/);
      expect(screen.queryByText("Not verified yet")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Send verification code" })).not.toBeInTheDocument();
      expect(screen.queryByLabelText(/Resume \(PDF only\)/)).not.toBeInTheDocument();
      expect(requestCode).not.toHaveBeenCalled();
    });

    it("waits for typing to pause instead of checking on every keystroke", async () => {
      const check = vi.spyOn(careersApi, "checkApplicantStatus");
      renderForm();
      for (const partial of ["e", "el", "eli@", "eli@ex", "eli@example.co", "eli@example.com"]) {
        fill(/^Email/, partial);
      }
      await waitFor(() => expect(check).toHaveBeenCalledTimes(1));
      expect(check).toHaveBeenCalledWith("acme", { email: "eli@example.com", job_id: "job-1" });
    });

    it("lets a candidate who may (re)apply continue to verification", async () => {
      const check = vi.spyOn(careersApi, "checkApplicantStatus");
      renderForm();
      fillDetails();
      await waitFor(() => expect(check).toHaveBeenCalled());

      expect(screen.queryByText("You have already applied.")).not.toBeInTheDocument();
      expect(continueButton()).toBeEnabled();
      submitCurrentStep();
      expect(await screen.findByText("Not verified yet")).toBeInTheDocument();
    });

    it("returns to details with the message when the server refuses the code", async () => {
      // e.g. the early check hadn't answered yet, or the client skipped it.
      vi.spyOn(careersApi, "requestEmailCode").mockRejectedValue(
        new ApiError("You have already applied.", 409, "already_applied"),
      );
      renderForm();
      fillDetails();
      submitCurrentStep();
      fireEvent.click(await screen.findByRole("button", { name: "Send verification code" }));

      expect(await screen.findByText("You have already applied.")).toBeInTheDocument();
      expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(/Your details/);
      expect(continueButton()).toBeDisabled();
      // A different email is a different person: the block lifts.
      fill(/^Email/, "someone.else@example.com");
      expect(screen.queryByText("You have already applied.")).not.toBeInTheDocument();
    });

    it("prefills a returning candidate's stored profile only after the code is verified", async () => {
      const verify = vi.spyOn(careersApi, "verifyEmailCode").mockResolvedValue({
        verification_token: "verified-token",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        existing_profile: {
          full_name: "Rita Returning",
          phone: "+91 91234 56789",
          date_of_birth: "1994-02-03",
          place_of_birth: "Chennai",
          languages: ["English", "Tamil"],
          candidate_type: "FRESHER",
          years_experience: null,
          notice_period_days: null,
          immediate_joiner: null,
          current_title: null,
          current_company: null,
          location: "Chennai",
          preferred_location: "Hyderabad",
          qualification: "M.Sc",
          linkedin_url: "https://www.linkedin.com/in/rita",
          github_url: "https://github.com/rita",
          tenth_marks_percentage: 82.4,
        },
      });
      vi.spyOn(careersApi, "requestEmailCode").mockResolvedValue({
        expires_in_seconds: 600,
        resend_available_in_seconds: 60,
      });
      renderForm();
      // Only some details typed; what is typed must win over the stored value.
      fill(/^Email/, "rita@example.com");
      fill(/Place of birth/, "Pune");
      fill(/Languages known/, "English");
      fireEvent.keyDown(screen.getByLabelText(/Languages known/), { key: "Enter" });
      fill(/I am a/, "FRESHER");
      // Nothing about the profile is on screen before the code — not the
      // name, nor the stored mobile number.
      expect(screen.queryByDisplayValue("Rita Returning")).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue("+91 91234 56789")).not.toBeInTheDocument();
      expect(screen.getByLabelText(/Mobile number/)).toHaveValue("");
      submitCurrentStep();

      fireEvent.click(await screen.findByRole("button", { name: "Send verification code" }));
      await screen.findByLabelText(/Verification code/);
      fill(/Verification code/, "123456");
      fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
      await screen.findByLabelText(/Resume \(PDF only\)/);
      expect(verify).toHaveBeenCalledWith("acme", "rita@example.com", "123456", "job-1");

      fireEvent.click(screen.getByRole("button", { name: "Back" }));
      expect(await screen.findByDisplayValue("Rita Returning")).toBeInTheDocument();
      expect(screen.getByLabelText(/Date of birth/)).toHaveValue("1994-02-03");
      expect(screen.getByLabelText(/Highest qualification/)).toHaveValue("M.Sc");
      expect(screen.getByLabelText(/Current location/)).toHaveValue("Chennai");
      expect(screen.getByLabelText(/Place of birth/)).toHaveValue("Pune"); // typed value kept
      // The stored mobile number and marks are filled in after verification.
      expect(screen.getByLabelText(/Mobile number/)).toHaveValue("+91 91234 56789");
      expect(screen.getByLabelText(/^10th Marks \(Optional\)/)).toHaveValue("82.4");
    });

    it("never fills the stored mobile number from the existing-email check (regression)", async () => {
      const check = vi
        .spyOn(careersApi, "checkApplicantStatus")
        .mockResolvedValue({ already_applied: true });
      const requestCode = vi.spyOn(careersApi, "requestEmailCode");
      const verify = vi.spyOn(careersApi, "verifyEmailCode");
      renderForm();
      // Only the email is entered — as when an existing candidate starts over.
      fill(/^Email/, "dbr@example.com");

      const message = await screen.findByText("You have already applied.");
      const email = screen.getByLabelText(/^Email/);
      expect(email.nextElementSibling).toBe(message);
      expect(check).toHaveBeenCalledWith("acme", { email: "dbr@example.com", job_id: "job-1" });
      // The check answered; nothing was put into the Mobile number field,
      // and no code was requested or verified (so no profile came back).
      expect(screen.getByLabelText(/Mobile number/)).toHaveValue("");
      expect(screen.queryByDisplayValue(/^\+91/)).not.toBeInTheDocument();
      expect(requestCode).not.toHaveBeenCalled();
      expect(verify).not.toHaveBeenCalled();
    });

    it("fills the stored mobile number only once the code is verified (regression)", async () => {
      vi.spyOn(careersApi, "requestEmailCode").mockResolvedValue({
        expires_in_seconds: 600,
        resend_available_in_seconds: 60,
      });
      vi.spyOn(careersApi, "verifyEmailCode").mockResolvedValue({
        verification_token: "verified-token",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        existing_profile: {
          full_name: "Dee Returning",
          phone: "+916363276931",
          date_of_birth: "1994-02-03",
          place_of_birth: "Chennai",
          languages: ["English"],
          candidate_type: "FRESHER",
          years_experience: null,
          notice_period_days: null,
          immediate_joiner: null,
          current_title: null,
          current_company: null,
          location: "Chennai",
          preferred_location: "Pune",
          qualification: "B.E",
          linkedin_url: null,
          github_url: null,
          tenth_marks_percentage: null,
        },
      });
      const check = vi.spyOn(careersApi, "checkApplicantStatus");
      renderForm();
      fill(/^Email/, "dee@example.com");
      fill(/I am a/, "FRESHER");
      fill(/Languages known/, "English");
      fireEvent.keyDown(screen.getByLabelText(/Languages known/), { key: "Enter" });
      await waitFor(() => expect(check).toHaveBeenCalled());
      // Existing email, may apply: still nothing filled before the code.
      expect(screen.getByLabelText(/Mobile number/)).toHaveValue("");

      submitCurrentStep();
      fireEvent.click(await screen.findByRole("button", { name: "Send verification code" }));
      await screen.findByLabelText(/Verification code/);
      fill(/Verification code/, "123456");
      fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
      await screen.findByLabelText(/Resume \(PDF only\)/);

      fireEvent.click(screen.getByRole("button", { name: "Back" }));
      expect(await screen.findByLabelText(/Mobile number/)).toHaveValue("+916363276931");
      expect(screen.getByLabelText(/Full name/)).toHaveValue("Dee Returning");
    });

    describe("message placement", () => {
      const BLOCKED_EMAIL = "blocked@example.com";
      const BLOCKED_PHONE = "+91 91111 22222";
      function mockStatus() {
        return vi
          .spyOn(careersApi, "checkApplicantStatus")
          .mockImplementation(async (_slug, query) => ({
            already_applied: query.email === BLOCKED_EMAIL || query.phone === BLOCKED_PHONE,
          }));
      }
      const emailInput = () => screen.getByLabelText(/^Email/);
      const mobileInput = () => screen.getByLabelText(/Mobile number/);
      const messages = () => screen.queryAllByText("You have already applied.");

      it("blocked email, empty mobile: only under Email, and no OTP request", async () => {
        const check = mockStatus();
        const requestCode = vi.spyOn(careersApi, "requestEmailCode");
        renderForm();
        fill(/^Email/, BLOCKED_EMAIL);

        const message = await screen.findByText("You have already applied.");
        expect(emailInput().nextElementSibling).toBe(message);
        expect(messages()).toHaveLength(1);
        expect(mobileInput()).toHaveValue("");
        expect(mobileInput()).not.toHaveAttribute("aria-invalid");
        expect(check).toHaveBeenCalledTimes(1);
        expect(check).toHaveBeenCalledWith("acme", { email: BLOCKED_EMAIL, job_id: "job-1" });
        submitCurrentStep();
        expect(requestCode).not.toHaveBeenCalled();
      });

      it("blocked mobile: under Mobile, not under Email", async () => {
        mockStatus();
        const requestCode = vi.spyOn(careersApi, "requestEmailCode");
        renderForm();
        fill(/Mobile number/, BLOCKED_PHONE);

        const message = await screen.findByText("You have already applied.");
        expect(mobileInput().nextElementSibling).toBe(message);
        expect(mobileInput()).toHaveAttribute("aria-describedby", message.id);
        expect(mobileInput()).toHaveAttribute("aria-invalid", "true");
        expect(emailInput()).not.toHaveAttribute("aria-invalid");
        expect(messages()).toHaveLength(1);
        expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
        expect(requestCode).not.toHaveBeenCalled();
      });

      it("allowed email + blocked mobile: only Mobile shows the message", async () => {
        const check = mockStatus();
        const requestCode = vi.spyOn(careersApi, "requestEmailCode");
        renderForm();
        fill(/^Email/, "allowed@example.com");
        await waitFor(() =>
          expect(check).toHaveBeenCalledWith("acme", { email: "allowed@example.com", job_id: "job-1" }),
        );
        expect(messages()).toHaveLength(0);

        fill(/Mobile number/, BLOCKED_PHONE);
        const message = await screen.findByText("You have already applied.");
        expect(mobileInput().nextElementSibling).toBe(message);
        expect(emailInput().nextElementSibling).not.toBe(message);
        expect(emailInput()).not.toHaveAttribute("aria-invalid");
        expect(messages()).toHaveLength(1);
        submitCurrentStep();
        expect(requestCode).not.toHaveBeenCalled();
      });

      it("blocked email + blocked mobile: a single message under Email", async () => {
        mockStatus();
        renderForm();
        fill(/^Email/, BLOCKED_EMAIL);
        fill(/Mobile number/, BLOCKED_PHONE);

        const message = await screen.findByText("You have already applied.");
        await waitFor(() => expect(mobileInput()).toHaveAttribute("aria-invalid", "true"));
        expect(emailInput().nextElementSibling).toBe(message);
        expect(messages()).toHaveLength(1);
        expect(mobileInput()).toHaveAttribute("aria-describedby", message.id);
      });
    });

    it("checks the campus drive's own endpoint for a drive link", async () => {
      const campusCheck = vi
        .spyOn(careersApi, "checkCampusApplicantStatus")
        .mockResolvedValue({ already_applied: true });
      const careersCheck = vi.spyOn(careersApi, "checkApplicantStatus");
      const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <JobApplicationForm campusToken="drive-tok" />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      fill(/^Email/, "eli@example.com");

      expect(await screen.findByText("You have already applied.")).toBeInTheDocument();
      expect(continueButton()).toBeDisabled();
      expect(campusCheck).toHaveBeenCalledWith("drive-tok", { email: "eli@example.com" });
      expect(careersCheck).not.toHaveBeenCalled();
    });
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
      null,
    );
    expect(await screen.findByRole("link", { name: "Start assessment" })).toHaveAttribute(
      "href",
      "/assessment/abc",
    );
    expect(screen.getByRole("heading", { name: "Application Submitted" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Careers" })).toHaveAttribute("href", "/");
  });

  describe("registration fields", () => {
    it("puts no length limit on the full name", () => {
      renderForm();
      const name = screen.getByLabelText(/Full name/);
      expect(name).not.toHaveAttribute("maxLength");
      const longName = "Venkata ".repeat(150).trim();
      fill(/Full name/, longName);
      expect(name).toHaveValue(longName);
      expect(name).toBeRequired();
    });

    it("continues without LinkedIn and GitHub, and doesn't send them", async () => {
      const postForm = vi.spyOn(apiClient, "postForm").mockResolvedValue(result());
      renderForm();
      fillDetails({ experienced: true });
      fill(/LinkedIn/, "");
      fill(/GitHub/, "");
      submitCurrentStep();
      await verifyEmail();
      attachResume();
      submitCurrentStep();
      fireEvent.click(await screen.findByRole("button", { name: "Submit application" }));

      await waitFor(() => expect(postForm).toHaveBeenCalledTimes(1));
      const sent = postForm.mock.calls[0][1] as FormData;
      expect(sent.has("linkedin_url")).toBe(false);
      expect(sent.has("github_url")).toBe(false);
      expect(sent.has("tenth_marks_percentage")).toBe(false);
      expect(sent.has("tenth_marks_card")).toBe(false);
      expect(sent.get("full_name")).toBe("Eli Experienced");
    });

    it("keeps URL validation for LinkedIn and GitHub when a value is entered", () => {
      renderForm();
      expect(screen.getByLabelText(/LinkedIn/)).toHaveAttribute("type", "url");
      expect(screen.getByLabelText(/GitHub/)).toHaveAttribute("type", "url");
    });
  });

  describe("languages multi-select", () => {
    const languages = () => screen.getByLabelText(/Languages known/);
    const chips = () =>
      screen.queryAllByRole("listitem").filter((item) => item.classList.contains("lang-chip"));

    it("opens a compact list, adds a language on click and removes it on a second click", () => {
      renderForm();
      fireEvent.focus(languages());
      const listbox = screen.getByRole("listbox", { name: "Available languages" });
      expect(listbox).toHaveAttribute("aria-multiselectable", "true");
      expect(languages()).toHaveAttribute("aria-expanded", "true");

      fireEvent.click(screen.getByRole("option", { name: "Hindi" }));
      expect(chips().map((chip) => chip.textContent)).toEqual(["Hindi"]);
      expect(screen.getByRole("option", { name: "Hindi" })).toHaveAttribute("aria-selected", "true");

      fireEvent.click(screen.getByRole("option", { name: "Hindi" }));
      expect(chips()).toHaveLength(0);
    });

    it("never adds the same language twice", () => {
      renderForm();
      fill(/Languages known/, "English");
      fireEvent.keyDown(languages(), { key: "Enter" });
      // Another spelling of a selected language is not offered as new.
      fill(/Languages known/, "ENGLISH");
      expect(screen.queryByRole("option", { name: /Add/ })).not.toBeInTheDocument();
      // A custom language can be added once; then it isn't offered again.
      fill(/Languages known/, "Sanskrit");
      fireEvent.click(screen.getByRole("option", { name: "Add “Sanskrit”" }));
      fill(/Languages known/, "sanskrit");
      expect(screen.queryByRole("option", { name: /Add/ })).not.toBeInTheDocument();
      expect(chips().map((chip) => chip.textContent)).toEqual(["English", "Sanskrit"]);
    });

    it("works from the keyboard and removes chips", () => {
      renderForm();
      fireEvent.focus(languages());
      fireEvent.keyDown(languages(), { key: "ArrowDown" });
      fireEvent.keyDown(languages(), { key: "Enter" }); // Hindi (2nd option)
      expect(chips().map((chip) => chip.textContent)).toEqual(["Hindi"]);
      expect(languages()).toHaveAttribute("aria-activedescendant");

      fireEvent.keyDown(languages(), { key: "Escape" });
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

      fireEvent.keyDown(languages(), { key: "Backspace" });
      expect(chips()).toHaveLength(0);

      fireEvent.click(languages()); // reopens the list
      fireEvent.click(screen.getByRole("option", { name: "Tamil" }));
      fireEvent.click(screen.getByRole("button", { name: "Remove Tamil" }));
      expect(chips()).toHaveLength(0);
    });
  });

  describe("10th marks", () => {
    function attachMarksCard(file: File) {
      fireEvent.change(screen.getByLabelText(/10th Marks Card/), { target: { files: [file] } });
    }

    it("is optional: registration continues without marks or a marks card", async () => {
      renderForm();
      fillDetails();
      submitCurrentStep();
      expect(await screen.findByText("Not verified yet")).toBeInTheDocument();
    });

    it("validates the marks value and blocks Continue until it's fixed or cleared", async () => {
      renderForm();
      fillDetails();
      fill(/^10th Marks \(Optional\)/, "101");
      expect(await screen.findByText(/between 0 and 100/)).toBeInTheDocument();
      expect(screen.getByLabelText(/^10th Marks \(Optional\)/)).toHaveAttribute("aria-invalid", "true");
      submitCurrentStep();
      expect(await screen.findByText(/correct the 10th marks/)).toBeInTheDocument();
      expect(screen.queryByText("Not verified yet")).not.toBeInTheDocument();

      fill(/^10th Marks \(Optional\)/, "87.555");
      expect(screen.getByText(/between 0 and 100/)).toBeInTheDocument();
      fill(/^10th Marks \(Optional\)/, "");
      submitCurrentStep();
      expect(await screen.findByText("Not verified yet")).toBeInTheDocument();
    });

    it("rejects other file types and oversized files", () => {
      renderForm();
      attachMarksCard(new File(["MZ"], "setup.exe", { type: "application/octet-stream" }));
      expect(screen.getByText(/must be a PDF, PNG or JPG/)).toBeInTheDocument();

      const big = new File(["x"], "marks.pdf", { type: "application/pdf" });
      Object.defineProperty(big, "size", { value: 5 * 1024 * 1024 + 1 });
      attachMarksCard(big);
      expect(screen.getByText(/smaller than 5 MB/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Remove marks/ })).not.toBeInTheDocument();
    });

    it("sends the marks and the card with the application, and the card can be removed", async () => {
      const postForm = vi.spyOn(apiClient, "postForm").mockResolvedValue(result());
      renderForm();
      fillDetails({ experienced: true });
      fill(/^10th Marks \(Optional\)/, "87.5");
      attachMarksCard(new File(["png"], "marks-old.png", { type: "image/png" }));
      fireEvent.click(screen.getByRole("button", { name: "Remove marks-old.png" }));
      expect(screen.queryByText("marks-old.png")).not.toBeInTheDocument();
      const card = new File(["%PDF-1.4"], "marks.pdf", { type: "application/pdf" });
      attachMarksCard(card);
      expect(screen.getByText("marks.pdf")).toBeInTheDocument();

      submitCurrentStep();
      await verifyEmail();
      attachResume();
      submitCurrentStep();
      expect(await screen.findByText("87.5%")).toBeInTheDocument();
      fireEvent.click(await screen.findByRole("button", { name: "Submit application" }));

      await waitFor(() => expect(postForm).toHaveBeenCalledTimes(1));
      const sent = postForm.mock.calls[0][1] as FormData;
      expect(sent.get("tenth_marks_percentage")).toBe("87.5");
      expect(sent.get("tenth_marks_card")).toBe(card);
      expect(sent.get("resume")).toBeInstanceOf(File);
    });
  });
});
