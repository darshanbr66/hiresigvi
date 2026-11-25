import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/apiClient";
import type {
  DraftView,
  EducationCheck,
  QualificationOptions,
  SpecializationCategoryView,
} from "../../types/applyFlow";
import type { PublicApplicationResult } from "../../types/careers";
import type { ProgramDeclaration } from "../../types/program";
import * as careersApi from "./api";
import { JobApplicationForm } from "./JobApplicationForm";

const JOB_BASE = "/api/v1/public/organizations/acme/jobs/job-1";

function renderForm(props: Partial<ComponentProps<typeof JobApplicationForm>> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const formProps = (
    "campusToken" in props || "program" in props
      ? props
      : { slug: "acme", jobId: "job-1", contactEmail: "careers@acme.test", ...props }
  ) as ComponentProps<typeof JobApplicationForm>;
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <JobApplicationForm {...formProps} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function click(name: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}

const check = (overrides: Partial<EducationCheck> = {}): EducationCheck => ({
  status: "NOT_STARTED",
  message: null,
  can_continue: false,
  can_retry: false,
  can_request_manual_review: false,
  attempts_remaining: 8,
  ...overrides,
});

const draftView = (overrides: Partial<DraftView> = {}): DraftView => ({
  email: "eli@example.com",
  full_name: "Eli Candidate",
  phone: "+919876543210",
  background: null,
  education: null,
  document: null,
  education_check: check(),
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  ...overrides,
});

const DOCUMENT = {
  filename: "mtech.pdf",
  content_type: "application/pdf",
  size_bytes: 2048,
  uploaded_at: new Date().toISOString(),
};

const EDUCATION = {
  qualification_key: "ME_MTECH",
  degree_name: null,
  specialization_category: "MECHANICAL_MANUFACTURING",
  specialization: "MECHANICAL_ENGINEERING",
  branch: "Mechanical Engineering",
  institution: "IIT Madras",
  completion_year: 2022,
  tenth_marks_percentage: null,
};

const category = (key: string, label: string, items: [string, string][]) => ({
  key,
  label,
  specializations: items.map(([itemKey, itemLabel]) => ({ key: itemKey, label: itemLabel })),
});

/** What the server serves (app/services/education/specializations.py). */
const SPECIALIZATION_CATEGORIES: SpecializationCategoryView[] = [
  category("CS_IT", "Computer Science & Information Technology", [
    ["COMPUTER_SCIENCE_ENGINEERING", "Computer Science and Engineering"],
    ["INFORMATION_TECHNOLOGY", "Information Technology"],
    ["AI_ML", "Artificial Intelligence and Machine Learning"],
    ["DATA_SCIENCE", "Data Science"],
    ["SOFTWARE_ENGINEERING", "Software Engineering"],
    ["CYBERSECURITY", "Cybersecurity"],
    ["COMPUTER_NETWORKS", "Computer Networks"],
    ["CLOUD_COMPUTING", "Cloud Computing"],
    ["COMPUTER_ENGINEERING", "Computer Engineering"],
  ]),
  category("ELECTRONICS_ELECTRICAL", "Electronics & Electrical", [
    ["ELECTRONICS_COMMUNICATION", "Electronics and Communication Engineering"],
    ["VLSI_DESIGN", "VLSI Design"],
  ]),
  category("MECHANICAL_MANUFACTURING", "Mechanical & Manufacturing", [
    ["MECHANICAL_ENGINEERING", "Mechanical Engineering"],
    ["MECHATRONICS", "Mechatronics"],
    ["ROBOTICS_AUTOMATION", "Robotics and Automation"],
    ["AUTOMOTIVE_ENGINEERING", "Automotive Engineering"],
    ["AEROSPACE_ENGINEERING", "Aerospace Engineering"],
    ["MANUFACTURING_ENGINEERING", "Manufacturing Engineering"],
    ["PRODUCTION_ENGINEERING", "Production Engineering"],
    ["THERMAL_ENGINEERING", "Thermal Engineering"],
    ["INDUSTRIAL_ENGINEERING", "Industrial Engineering"],
  ]),
  category("CIVIL_INFRASTRUCTURE", "Civil & Infrastructure", [
    ["CIVIL_ENGINEERING", "Civil Engineering"],
  ]),
  category("CHEMICAL_MATERIALS", "Chemical & Materials", [
    ["CHEMICAL_ENGINEERING", "Chemical Engineering"],
  ]),
  category("BIOTECH_BIOMEDICAL", "Biotechnology & Biomedical", [
    ["BIOTECHNOLOGY", "Biotechnology"],
  ]),
  category("OTHER_TECHNOLOGY", "Other Technology Domains", [
    ["ENERGY_ENGINEERING", "Energy Engineering"],
  ]),
];

const MTECH_OPTIONS: QualificationOptions = {
  restricted: true,
  summary: "Eligible qualifications for this role: M.Tech, Ph.D.",
  options: [
    {
      key: "ME_MTECH",
      label: "M.Tech",
      level: "MASTERS",
      branch_label: "Specialization",
      branch_required: true,
      requires_degree_name: false,
      document_hint: "your final-year marks card or degree certificate",
    },
    {
      key: "PHD",
      label: "Ph.D.",
      level: "DOCTORATE",
      branch_label: "Research area",
      branch_required: true,
      requires_degree_name: false,
      document_hint: "your Ph.D. degree certificate",
    },
  ],
  fields_of_study: [],
  field_mandatory: false,
  specialization_categories: SPECIALIZATION_CATEGORIES,
  accepted_document_types: ["PDF", "PNG", "JPG"],
  max_document_size_mb: 5,
};

const result = (overrides: Partial<PublicApplicationResult> = {}): PublicApplicationResult => ({
  id: "app-1",
  job_title: "Patent Engineer",
  candidate_email: "eli@example.com",
  confirmation_email_sent: true,
  careers_contact_email: "careers@acme.test",
  submitted_at: new Date().toISOString(),
  ...overrides,
});

type DraftSpy = { mock: { settledResults: { type: string; value: unknown }[]; invocationCallOrder: number[] } };

/** The draft the mocked server last returned (by call order), so the
 * education save behaves like the real one: it keeps the stored document. */
function latestDraft(spies: DraftSpy[]): DraftView {
  let latest: { order: number; value: DraftView } | null = null;
  for (const spy of spies) {
    spy.mock.settledResults.forEach((settled, index) => {
      const order = spy.mock.invocationCallOrder[index];
      if (settled.type === "fulfilled" && (!latest || order > latest.order)) {
        latest = { order, value: settled.value as DraftView };
      }
    });
  }
  return (latest as { value: DraftView } | null)?.value ?? draftView();
}

function mockApi() {
  const draftSpies: DraftSpy[] = [];
  const spies = {
    requestCode: vi.spyOn(careersApi, "requestEmailCode").mockResolvedValue({
      expires_in_seconds: 600,
      resend_available_in_seconds: 60,
    }),
    verifyCode: vi.spyOn(careersApi, "verifyEmailCode").mockResolvedValue({
      verification_token: "verified-token",
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    }),
    startDraft: vi.spyOn(careersApi, "startApplicationDraft").mockResolvedValue(draftView()),
    saveBackground: vi
      .spyOn(careersApi, "saveDraftBackground")
      .mockImplementation(async (_b, _a, background) => ({ ...latestDraft(draftSpies), background })),
    options: vi.spyOn(careersApi, "getQualificationOptions").mockResolvedValue(MTECH_OPTIONS),
    // Like the server: the document and its check survive a save, and a
    // changed qualification or specialization resets the check; the branch
    // is the specialization's label.
    saveEducation: vi.spyOn(careersApi, "saveDraftEducation").mockImplementation(
      async (_b, _a, education) => {
        const current = latestDraft(draftSpies);
        const changed =
          current.education?.qualification_key !== education.qualification_key ||
          current.education?.specialization !== education.specialization;
        const branch =
          SPECIALIZATION_CATEGORIES.flatMap((item) => item.specializations).find(
            (item) => item.key === education.specialization,
          )?.label ?? null;
        return {
          ...current,
          education: { ...education, branch },
          education_check: changed ? check() : current.education_check,
        };
      },
    ),
    upload: vi.spyOn(careersApi, "uploadAcademicDocument").mockResolvedValue(
      draftView({
        education: EDUCATION,
        document: DOCUMENT,
        education_check: check({
          status: "VERIFIED",
          can_continue: true,
          message: "Your document appears consistent with M.Tech. You can continue.",
        }),
      }),
    ),
    recheck: vi.spyOn(careersApi, "recheckAcademicDocument"),
    manualReview: vi.spyOn(careersApi, "requestEducationManualReview"),
    submit: vi.spyOn(careersApi, "submitApplicationDraft").mockResolvedValue(result()),
  };
  draftSpies.push(
    spies.upload,
    spies.recheck,
    spies.manualReview,
    spies.saveEducation,
    spies.saveBackground,
  );
  return spies;
}

const AUTH = { email: "eli@example.com", email_verification_token: "verified-token" };

async function completeDetails() {
  fill(/Full name/, "Eli Candidate");
  fill(/Email address/, "eli@example.com");
  fill(/Mobile number/, "98765 43210");
  click(/Continue & verify email/);
  await screen.findByLabelText(/Verification code/);
}

async function completeVerify() {
  fill(/Verification code/, "123456");
  click("Verify email");
  await screen.findByRole("heading", { name: /Professional background/ });
}

async function completeBackground() {
  fill(/Date of birth/, "1998-06-01");
  fill(/Place of birth/, "Pune");
  fill(/Languages known/, "English");
  fireEvent.keyDown(screen.getByLabelText(/Languages known/), { key: "Enter" });
  fill(/I am a/, "FRESHER");
  fill(/Current location/, "Pune");
  fill(/Preferred location/, "Bengaluru");
  click(/^Continue/);
  await screen.findByRole("heading", { name: /Step \d of \d: Education/ });
}

/** The specific-specialization dropdown (not "Specialization category"). */
const SPECIALIZATION = /^Specialization(?! category)/;

function optionLabels(select: HTMLElement): string[] {
  return within(select)
    .getAllByRole("option")
    .map((option) => option.textContent ?? "");
}

async function fillEducationDetails() {
  await screen.findByLabelText(/Highest qualification/);
  fill(/Highest qualification/, "ME_MTECH");
  fill(/Specialization category/, "MECHANICAL_MANUFACTURING");
  fill(SPECIALIZATION, "MECHANICAL_ENGINEERING");
  fill(/Year of completion/, "2022");
  fill(/Institution \/ university/, "IIT Madras");
}

function uploadDocument(file = new File(["%PDF-1.4"], "mtech.pdf", { type: "application/pdf" })) {
  fireEvent.change(screen.getByLabelText("Academic document"), { target: { files: [file] } });
}

async function reachEducation() {
  renderForm();
  await completeDetails();
  await completeVerify();
  await completeBackground();
}

async function reachResume(api: ReturnType<typeof mockApi>) {
  await reachEducation();
  await fillEducationDetails();
  uploadDocument();
  await screen.findByText(/appears consistent with M\.Tech/);
  click(/^Continue/);
  await screen.findByRole("heading", { name: /Resume/ });
  return api;
}

function attachResume(name = "resume.pdf") {
  const file = new File(["%PDF-1.4"], name, { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText(/^Resume/), { target: { files: [file] } });
  return file;
}

describe("JobApplicationForm — progressive apply flow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(careersApi, "checkApplicantStatus").mockResolvedValue({ already_applied: false });
    vi.spyOn(careersApi, "checkCampusApplicantStatus").mockResolvedValue({ already_applied: false });
  });

  describe("stepper", () => {
    it("shows the six steps with the current one marked, and is not clickable", () => {
      renderForm();
      const progress = screen.getByRole("navigation", { name: "Application progress" });
      const items = within(progress).getAllByRole("listitem");
      expect(items).toHaveLength(6);
      expect(items[0]).toHaveAttribute("aria-current", "step");
      expect(items[0]).toHaveTextContent("Step 1 of 6: Your details, current step");
      expect(items[5]).toHaveTextContent("Review & submit, not started");
      expect(within(progress).queryByRole("button")).not.toBeInTheDocument();
      expect(within(progress).queryByRole("link")).not.toBeInTheDocument();
    });

    it("marks completed steps as the candidate moves on", async () => {
      mockApi();
      renderForm();
      await completeDetails();
      await completeVerify();
      const items = within(screen.getByRole("navigation", { name: "Application progress" })).getAllByRole(
        "listitem",
      );
      expect(items[0]).toHaveTextContent("completed");
      expect(items[1]).toHaveTextContent("completed");
      expect(items[2]).toHaveAttribute("aria-current", "step");
    });
  });

  describe("step 1 — basic information", () => {
    it("asks only for name, email and mobile number", () => {
      renderForm();
      expect(screen.getByLabelText(/Full name/)).toBeInTheDocument();
      expect(screen.getByLabelText(/Email address/)).toBeInTheDocument();
      expect(screen.getByLabelText(/Mobile number/)).toBeInTheDocument();
      expect(screen.getByLabelText("Country code")).toHaveValue("+91");
      for (const absent of [/Date of birth/, /Place of birth/, /Languages known/, /qualification/i, /LinkedIn/, /GitHub/, /Resume/]) {
        expect(screen.queryByLabelText(absent)).not.toBeInTheDocument();
      }
    });

    it("validates every field before sending a code", () => {
      const api = mockApi();
      renderForm();
      click(/Continue & verify email/);
      expect(screen.getByText("Enter your full name.")).toBeInTheDocument();
      expect(screen.getByText("Enter your email address.")).toBeInTheDocument();
      expect(screen.getByText("Enter your mobile number.")).toBeInTheDocument();

      fill(/Full name/, "J0hn");
      fill(/Email address/, "not-an-email");
      fill(/Mobile number/, "12345");
      click(/Continue & verify email/);
      expect(screen.getByText(/A name can contain letters/)).toBeInTheDocument();
      expect(screen.getByText(/Enter a valid email address/)).toBeInTheDocument();
      expect(screen.getByText("Enter a valid 10-digit Indian mobile number.")).toBeInTheDocument();
      expect(screen.getByLabelText(/Full name/)).toHaveAttribute("aria-invalid", "true");
      expect(api.requestCode).not.toHaveBeenCalled();
    });

    it("validates the number for the selected country code", () => {
      mockApi();
      renderForm();
      fill("Country code", "+44");
      fill(/Mobile number/, "7911 123456");
      fill(/Full name/, "Eli Candidate");
      fill(/Email address/, "eli@example.com");
      click(/Continue & verify email/);
      expect(screen.queryByText(/valid .*mobile number/)).not.toBeInTheDocument();
    });

    it("shows 'You have already applied.' below Email with Continue disabled", async () => {
      vi.spyOn(careersApi, "checkApplicantStatus").mockResolvedValue({ already_applied: true });
      renderForm();
      fill(/Email address/, "taken@example.com");
      expect(await screen.findByText("You have already applied.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Continue & verify email/ })).toBeDisabled();
    });
  });

  describe("step 2 — email verification", () => {
    it("sends the code once, shows where it went and the resend cooldown", async () => {
      const api = mockApi();
      renderForm();
      await completeDetails();
      expect(api.requestCode).toHaveBeenCalledTimes(1);
      expect(api.requestCode).toHaveBeenCalledWith("acme", "eli@example.com", "job-1");
      expect(screen.getByText(/We sent a 6-digit code to/)).toHaveTextContent("eli@example.com");
      expect(screen.getByRole("button", { name: /Resend code in 60s/ })).toBeDisabled();
    });

    it("keeps the candidate here with the server's message for a wrong code", async () => {
      const api = mockApi();
      api.verifyCode.mockRejectedValue(
        new ApiError("Incorrect code. 4 attempts remaining.", 422, "otp_incorrect"),
      );
      renderForm();
      await completeDetails();
      fill(/Verification code/, "000000");
      click("Verify email");
      expect(await screen.findByText("Incorrect code. 4 attempts remaining.")).toBeInTheDocument();
      expect(screen.getByLabelText(/Verification code/)).toBeInTheDocument();
      expect(api.startDraft).not.toHaveBeenCalled();
    });

    it("starts the server-side draft with the normalized mobile number once verified", async () => {
      const api = mockApi();
      renderForm();
      await completeDetails();
      await completeVerify();
      expect(api.startDraft).toHaveBeenCalledWith(JOB_BASE, AUTH, "Eli Candidate", "+919876543210");
      expect(screen.getByText(/Email verified: eli@example.com/)).toBeInTheDocument();
    });

    it("lets the candidate change the email, which needs a new verification", async () => {
      const api = mockApi();
      renderForm();
      await completeDetails();
      await completeVerify();
      click(/Back/);
      expect(screen.getByRole("button", { name: /^Continue$/ })).toBeInTheDocument();
      expect(screen.getByLabelText(/Full name/)).toHaveValue("Eli Candidate");
      fill(/Email address/, "eli.new@example.com");
      click(/Continue & verify email/);
      await screen.findByLabelText(/Verification code/);
      expect(api.requestCode).toHaveBeenLastCalledWith("acme", "eli.new@example.com", "job-1");
    });

    it("returns to Step 1 when the mobile number belongs to someone else", async () => {
      const api = mockApi();
      api.startDraft.mockRejectedValue(
        new ApiError("This mobile number is already registered.", 409, "phone_already_registered"),
      );
      renderForm();
      await completeDetails();
      fill(/Verification code/, "123456");
      click("Verify email");
      expect(await screen.findByText("This mobile number is already registered.")).toBeInTheDocument();
      expect(screen.getByLabelText(/Mobile number/)).toHaveAttribute("aria-invalid", "true");
    });
  });

  describe("step 3 — professional background", () => {
    it("validates the step and saves it to the draft", async () => {
      const api = mockApi();
      renderForm();
      await completeDetails();
      await completeVerify();
      click(/^Continue/);
      expect(screen.getByText("Enter your date of birth.")).toBeInTheDocument();
      expect(screen.getByText("Add at least one language you know.")).toBeInTheDocument();
      expect(api.saveBackground).not.toHaveBeenCalled();

      fill(/I am a/, "EXPERIENCED");
      expect(screen.getByLabelText(/Total experience/)).toBeInTheDocument();
      fill(/I am a/, "FRESHER");
      expect(screen.queryByLabelText(/Total experience/)).not.toBeInTheDocument();
      await completeBackground();
      expect(api.saveBackground).toHaveBeenCalledWith(
        JOB_BASE,
        AUTH,
        expect.objectContaining({
          date_of_birth: "1998-06-01",
          languages: ["English"],
          candidate_type: "FRESHER",
          location: "Pune",
          preferred_location: "Bengaluru",
          linkedin_url: null,
        }),
      );
    });

    it("preserves entered values when going back and forward", async () => {
      mockApi();
      await reachEducation();
      await screen.findByLabelText(/Highest qualification/);
      click(/Back/);
      expect(await screen.findByLabelText(/Place of birth/)).toHaveValue("Pune");
      expect(screen.getByLabelText(/Preferred location/)).toHaveValue("Bengaluru");
    });
  });

  describe("step 4 — education and AI document check", () => {
    it("offers only the qualifications the job's JD allows", async () => {
      mockApi();
      await reachEducation();
      await screen.findByText(MTECH_OPTIONS.summary);
      const select = screen.getByLabelText(/Highest qualification/);
      const labels = within(select).getAllByRole("option").map((option) => option.textContent);
      expect(labels).toEqual(["Select…", "M.Tech", "Ph.D."]);
    });

    it("explains instead of showing an empty dropdown when nothing is selectable", async () => {
      const api = mockApi();
      const summary =
        "The qualifications this role accepts can't be selected in the online application. Please contact the recruitment team.";
      api.options.mockResolvedValue({ ...MTECH_OPTIONS, options: [], summary });
      await reachEducation();
      expect(await screen.findByText(summary)).toBeInTheDocument();
      expect(screen.queryByLabelText(/Highest qualification/)).not.toBeInTheDocument();
    });

    it("requires the document and the details before uploading", async () => {
      const api = mockApi();
      await reachEducation();
      await screen.findByLabelText(/Highest qualification/);
      click(/Save and continue/);
      expect(screen.getByText("Select your qualification.")).toBeInTheDocument();
      fill(/Highest qualification/, "ME_MTECH");
      expect(screen.getByText(/Upload your final-year marks card or degree certificate/)).toBeInTheDocument();
      expect(screen.getByText(/PDF, PNG, JPG, up to 5 MB/)).toBeInTheDocument();
      uploadDocument();
      expect(screen.getByText("Select your specialization category.")).toBeInTheDocument();
      expect(api.upload).not.toHaveBeenCalled();
    });

    it("offers all seven specialization categories, with the specialization disabled until one is chosen", async () => {
      mockApi();
      await reachEducation();
      const categorySelect = await screen.findByLabelText(/Specialization category/);
      expect(optionLabels(categorySelect).slice(1)).toEqual([
        "Computer Science & Information Technology",
        "Electronics & Electrical",
        "Mechanical & Manufacturing",
        "Civil & Infrastructure",
        "Chemical & Materials",
        "Biotechnology & Biomedical",
        "Other Technology Domains",
      ]);
      const specializationSelect = screen.getByLabelText(SPECIALIZATION);
      expect(specializationSelect).toBeDisabled();
      expect(optionLabels(specializationSelect)).toEqual(["Select a category first"]);

      fill(/Specialization category/, "MECHANICAL_MANUFACTURING");
      expect(specializationSelect).toBeEnabled();
      expect(specializationSelect).toBeRequired();
      expect(optionLabels(specializationSelect).slice(1)).toEqual([
        "Mechanical Engineering",
        "Mechatronics",
        "Robotics and Automation",
        "Automotive Engineering",
        "Aerospace Engineering",
        "Manufacturing Engineering",
        "Production Engineering",
        "Thermal Engineering",
        "Industrial Engineering",
      ]);
    });

    it("clears the specialization when the category changes", async () => {
      mockApi();
      await reachEducation();
      await screen.findByLabelText(/Specialization category/);
      fill(/Specialization category/, "MECHANICAL_MANUFACTURING");
      fill(SPECIALIZATION, "ROBOTICS_AUTOMATION");
      expect(screen.getByLabelText(SPECIALIZATION)).toHaveValue("ROBOTICS_AUTOMATION");
      fill(/Specialization category/, "CS_IT");
      const specializationSelect = screen.getByLabelText(SPECIALIZATION);
      expect(specializationSelect).toHaveValue("");
      expect(optionLabels(specializationSelect)).not.toContain("Robotics and Automation");
      expect(optionLabels(specializationSelect)).toContain("Data Science");
    });

    it("requires the qualification, the category and the specialization", async () => {
      const api = mockApi();
      await reachEducation();
      await screen.findByLabelText(/Highest qualification/);
      click(/Save and continue/);
      expect(screen.getByText("Select your qualification.")).toBeInTheDocument();
      expect(screen.getByText("Select your specialization category.")).toBeInTheDocument();
      fill(/Highest qualification/, "ME_MTECH");
      fill(/Specialization category/, "ELECTRONICS_ELECTRICAL");
      fill(/Year of completion/, "2022");
      fill(/Institution \/ university/, "IIT Madras");
      click(/Save and continue/);
      expect(screen.getByText("Select your specialization.")).toBeInTheDocument();
      expect(screen.queryByText("Select your specialization category.")).not.toBeInTheDocument();
      expect(api.saveEducation).not.toHaveBeenCalled();
    });

    it("sends the selected keys to the server", async () => {
      const api = mockApi();
      await reachEducation();
      await fillEducationDetails();
      uploadDocument();
      await screen.findByText("Document checked");
      expect(api.saveEducation).toHaveBeenCalledWith(
        JOB_BASE,
        AUTH,
        expect.objectContaining({
          qualification_key: "ME_MTECH",
          specialization_category: "MECHANICAL_MANUFACTURING",
          specialization: "MECHANICAL_ENGINEERING",
        }),
      );
    });

    it("keeps the selections when going back and forward", async () => {
      mockApi();
      await reachEducation();
      await fillEducationDetails();
      click(/Back/);
      await screen.findByLabelText(/Place of birth/);
      click(/^Continue/);
      expect(await screen.findByLabelText(/Highest qualification/)).toHaveValue("ME_MTECH");
      expect(screen.getByLabelText(/Specialization category/)).toHaveValue("MECHANICAL_MANUFACTURING");
      expect(screen.getByLabelText(SPECIALIZATION)).toHaveValue("MECHANICAL_ENGINEERING");
    });

    it("restores both selections from a resumed draft", async () => {
      const api = mockApi();
      api.startDraft.mockResolvedValue(
        draftView({
          background: {
            date_of_birth: "1998-06-01",
            place_of_birth: "Pune",
            languages: ["English"],
            candidate_type: "FRESHER",
            location: "Pune",
            preferred_location: "Bengaluru",
            linkedin_url: null,
            github_url: null,
            years_experience: null,
            current_title: null,
            current_company: null,
            notice_period_days: null,
            immediate_joiner: null,
          },
          education: { ...EDUCATION, specialization: "ROBOTICS_AUTOMATION", branch: "Robotics and Automation" },
        }),
      );
      renderForm();
      await completeDetails();
      await completeVerify();
      // The saved background is prefilled: continue straight on.
      expect(screen.getByLabelText(/Place of birth/)).toHaveValue("Pune");
      click(/^Continue/);
      expect(await screen.findByLabelText(/Specialization category/)).toHaveValue("MECHANICAL_MANUFACTURING");
      expect(screen.getByLabelText(SPECIALIZATION)).toHaveValue("ROBOTICS_AUTOMATION");
      expect(screen.getByLabelText(SPECIALIZATION)).toBeEnabled();
    });

    it("rejects other file types and oversized files without uploading", async () => {
      const api = mockApi();
      await reachEducation();
      await fillEducationDetails();
      uploadDocument(new File(["x"], "card.docx"));
      expect(screen.getByText("The document must be a PDF, PNG or JPG file.")).toBeInTheDocument();
      const big = new File(["x"], "card.pdf", { type: "application/pdf" });
      Object.defineProperty(big, "size", { value: 6 * 1024 * 1024 });
      uploadDocument(big);
      expect(screen.getByText("The document must be smaller than 5 MB.")).toBeInTheDocument();
      expect(api.upload).not.toHaveBeenCalled();
    });

    it("shows the checking state, then the verified outcome", async () => {
      const api = mockApi();
      let resolve: (view: DraftView) => void = () => undefined;
      api.upload.mockImplementation(
        (_base, _auth, _file, onProgress) =>
          new Promise((done) => {
            onProgress(1);
            resolve = done;
          }),
      );
      await reachEducation();
      await fillEducationDetails();
      uploadDocument();
      expect(await screen.findByText(/Checking your document/)).toBeInTheDocument();
      expect(api.saveEducation).toHaveBeenCalledWith(
        JOB_BASE,
        AUTH,
        expect.objectContaining({ qualification_key: "ME_MTECH", completion_year: 2022 }),
      );
      await act(async () =>
        resolve(
          draftView({
            education: EDUCATION,
            document: DOCUMENT,
            education_check: check({
              status: "VERIFIED",
              can_continue: true,
              message: "Your document appears consistent with M.Tech. You can continue.",
            }),
          }),
        ),
      );
      expect(screen.getByText("Document checked")).toBeInTheDocument();
      expect(screen.getByText(/not a verification of authenticity/)).toBeInTheDocument();
    });

    it("explains a mismatch and lets the candidate upload the right document", async () => {
      const api = mockApi();
      const mismatch =
        "The qualification selected does not appear to match the uploaded document. You selected M.Tech, but the uploaded document appears to be a B.E. / B.Tech academic record. Please upload the correct document.";
      api.upload.mockResolvedValueOnce(
        draftView({
          education: EDUCATION,
          document: { ...DOCUMENT, filename: "btech.pdf" },
          education_check: check({ status: "MISMATCH", message: mismatch }),
        }),
      );
      await reachEducation();
      await fillEducationDetails();
      uploadDocument(new File(["%PDF"], "btech.pdf", { type: "application/pdf" }));
      expect(await screen.findByText(mismatch)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /manual review/ })).not.toBeInTheDocument();

      api.saveEducation.mockResolvedValueOnce(
        draftView({ education: EDUCATION, document: DOCUMENT, education_check: check({ status: "MISMATCH", message: mismatch }) }),
      );
      click(/Save and continue/);
      expect(
        await screen.findByText("Your academic document needs to be verified before you continue."),
      ).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText("Replace academic document"), {
        target: { files: [new File(["%PDF"], "mtech.pdf", { type: "application/pdf" })] },
      });
      expect(await screen.findByText(/appears consistent with M\.Tech/)).toBeInTheDocument();
      expect(api.upload).toHaveBeenCalledTimes(2);
    });

    it("offers manual review for an unclear document", async () => {
      const api = mockApi();
      api.upload.mockResolvedValueOnce(
        draftView({
          education: EDUCATION,
          document: DOCUMENT,
          education_check: check({
            status: "UNCLEAR",
            message: "We couldn't read the uploaded document clearly.",
            can_request_manual_review: true,
          }),
        }),
      );
      api.manualReview.mockResolvedValue(
        draftView({
          education: EDUCATION,
          document: DOCUMENT,
          education_check: check({ status: "MANUAL_REVIEW", can_continue: true, message: "Your document will be reviewed by our recruitment team." }),
        }),
      );
      await reachEducation();
      await fillEducationDetails();
      uploadDocument();
      expect(await screen.findByText("We couldn't confirm your document")).toBeInTheDocument();
      click("Continue with manual review");
      expect(await screen.findByText("Document sent for manual review")).toBeInTheDocument();
      expect(api.manualReview).toHaveBeenCalledWith(JOB_BASE, AUTH);
    });

    it("keeps the document after an AI failure and retries the check", async () => {
      const api = mockApi();
      api.upload.mockResolvedValueOnce(
        draftView({
          education: EDUCATION,
          document: DOCUMENT,
          education_check: check({
            status: "FAILED",
            can_retry: true,
            message: "We couldn't check your document right now.",
          }),
        }),
      );
      api.recheck.mockResolvedValue(
        draftView({ education: EDUCATION, document: DOCUMENT, education_check: check({ status: "VERIFIED", can_continue: true, message: "Your document appears consistent with M.Tech." }) }),
      );
      await reachEducation();
      await fillEducationDetails();
      uploadDocument();
      expect(await screen.findByText("Check not completed")).toBeInTheDocument();
      expect(screen.getByText("mtech.pdf")).toBeInTheDocument();
      click("Try again");
      expect(await screen.findByText("Document checked")).toBeInTheDocument();
      expect(api.recheck).toHaveBeenCalledTimes(1);
    });

    it("re-checks the document when the qualification changes after verification", async () => {
      const api = mockApi();
      await reachEducation();
      await fillEducationDetails();
      uploadDocument();
      await screen.findByText("Document checked");
      fill(/Highest qualification/, "PHD");
      expect(screen.getByText("Details changed")).toBeInTheDocument();
      // The category and specialization are kept across a qualification change.
      expect(screen.getByLabelText(/Specialization category/)).toHaveValue("MECHANICAL_MANUFACTURING");
      expect(screen.getByLabelText(SPECIALIZATION)).toHaveValue("MECHANICAL_ENGINEERING");
      api.saveEducation.mockResolvedValueOnce(
        draftView({ education: { ...EDUCATION, qualification_key: "PHD" }, document: DOCUMENT, education_check: check() }),
      );
      api.recheck.mockResolvedValue(
        draftView({
          education: { ...EDUCATION, qualification_key: "PHD" },
          document: DOCUMENT,
          education_check: check({ status: "MISMATCH", message: "You selected Ph.D., but the uploaded document appears to be an M.Tech academic record." }),
        }),
      );
      click(/Save and continue/);
      expect(await screen.findByText(/You selected Ph.D./)).toBeInTheDocument();
      expect(api.recheck).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("heading", { name: /Resume/ })).not.toBeInTheDocument();
    });
  });

  describe("steps 5–6 — resume, review and submit", () => {
    it("accepts PDF resumes only", async () => {
      const api = mockApi();
      await reachResume(api);
      attachResume("resume.docx");
      expect(screen.getByText("Please upload your resume as a PDF file.")).toBeInTheDocument();
      click(/^Continue/);
      expect(screen.queryByRole("button", { name: "Submit application" })).not.toBeInTheDocument();
    });

    it("reviews every step, edits go back with values kept, and submits once", async () => {
      const api = mockApi();
      let finish: (value: PublicApplicationResult) => void = () => undefined;
      api.submit.mockImplementation(() => new Promise((done) => (finish = done)));
      await reachResume(api);
      const file = attachResume();
      click(/^Continue/);
      await screen.findByRole("button", { name: "Submit application" });

      const basic = screen.getByRole("region", { name: "Basic information" });
      expect(basic).toHaveTextContent("Eli Candidate");
      expect(basic).toHaveTextContent("Verified");
      expect(screen.getByRole("region", { name: "Professional background" })).toHaveTextContent("Pune");
      const education = screen.getByRole("region", { name: "Education" });
      expect(within(education).getByText("Highest qualification").nextElementSibling).toHaveTextContent("M.Tech");
      expect(within(education).getByText("Specialization category").nextElementSibling).toHaveTextContent(
        "Mechanical & Manufacturing",
      );
      expect(within(education).getByText("Specialization").nextElementSibling).toHaveTextContent(
        "Mechanical Engineering",
      );
      expect(education).toHaveTextContent("Document checked");
      expect(screen.getByRole("region", { name: "Resume" })).toHaveTextContent("resume.pdf");

      fireEvent.click(within(screen.getByRole("region", { name: "Professional background" })).getByRole("button", { name: /Edit/ }));
      expect(await screen.findByLabelText(/Place of birth/)).toHaveValue("Pune");
      click(/^Continue/); // background saved again
      await screen.findByLabelText(/Highest qualification/);
      click(/^Continue/);
      await screen.findByRole("heading", { name: /Resume/ });
      click(/^Continue/);

      const submit = await screen.findByRole("button", { name: "Submit application" });
      fireEvent.click(submit);
      fireEvent.click(submit);
      await waitFor(() => expect(api.submit).toHaveBeenCalledTimes(1));
      expect(api.submit).toHaveBeenCalledWith(JOB_BASE, AUTH, file, null, expect.any(Function));
      await act(async () => finish(result()));
      expect(await screen.findByRole("heading", { name: "Application Submitted" })).toBeInTheDocument();
    });

    it("never shows a screening result, whatever the backend sends", async () => {
      const api = mockApi();
      api.submit.mockResolvedValue({
        ...result(),
        ...({ decision: "NOT_MATCH", overall_score: 12 } as object),
      } as PublicApplicationResult);
      await reachResume(api);
      attachResume();
      click(/^Continue/);
      click("Submit application");
      await screen.findByRole("heading", { name: "Application Submitted" });
      expect(screen.queryByText(/NOT_MATCH|12/)).not.toBeInTheDocument();
    });

    it("sends the candidate back to Education when the JD's eligibility changed", async () => {
      const api = mockApi();
      api.submit.mockRejectedValue(
        new ApiError(
          "This role's eligibility requirements have changed since you entered your education.",
          409,
          "education_recheck_required",
        ),
      );
      await reachResume(api);
      attachResume();
      click(/^Continue/);
      click("Submit application");
      expect(await screen.findByText(/eligibility requirements have changed/)).toBeInTheDocument();
      expect(screen.getByLabelText(/Highest qualification/)).toBeInTheDocument();
    });

    it("asks to verify again when the verification expired, keeping progress", async () => {
      const api = mockApi();
      api.saveBackground.mockRejectedValueOnce(
        new ApiError("Your email verification has expired.", 422, "email_not_verified"),
      );
      renderForm();
      await completeDetails();
      await completeVerify();
      fill(/Date of birth/, "1998-06-01");
      fill(/Place of birth/, "Pune");
      fill(/Languages known/, "English");
      fireEvent.keyDown(screen.getByLabelText(/Languages known/), { key: "Enter" });
      fill(/I am a/, "FRESHER");
      fill(/Current location/, "Pune");
      fill(/Preferred location/, "Bengaluru");
      click(/^Continue/);
      expect(await screen.findByText(/Your email verification has expired/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Send verification code/ })).toBeInTheDocument();
    });
  });

  describe("other entry points", () => {
    it("uses the campus drive's own endpoints for a drive link", async () => {
      const api = mockApi();
      const campusCode = vi.spyOn(careersApi, "requestCampusEmailCode").mockResolvedValue({
        expires_in_seconds: 600,
        resend_available_in_seconds: 60,
      });
      vi.spyOn(careersApi, "verifyCampusEmailCode").mockResolvedValue({
        verification_token: "verified-token",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      });
      renderForm({ campusToken: "drive-tok" });
      await completeDetails();
      expect(campusCode).toHaveBeenCalledWith("drive-tok", "eli@example.com");
      await completeVerify();
      expect(api.startDraft).toHaveBeenCalledWith(
        "/api/v1/public/campus-drive/drive-tok",
        AUTH,
        "Eli Candidate",
        "+919876543210",
      );
    });

    it("keeps a program's one-request form: no Education step", async () => {
      vi.spyOn(careersApi, "checkProgramApplicantStatus").mockResolvedValue({ already_applied: false });
      vi.spyOn(careersApi, "requestProgramEmailCode").mockResolvedValue({
        expires_in_seconds: 600,
        resend_available_in_seconds: 60,
      });
      vi.spyOn(careersApi, "verifyProgramEmailCode").mockResolvedValue({
        verification_token: "verified-token",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      });
      const startDraft = vi.spyOn(careersApi, "startApplicationDraft");
      renderForm({
        program: {
          id: "prog-1",
          ready: true,
          declaration: {} as ProgramDeclaration,
        },
      });
      const items = within(screen.getByRole("navigation", { name: "Application progress" })).getAllByRole(
        "listitem",
      );
      expect(items).toHaveLength(5);
      await completeDetails();
      await completeVerify();
      expect(startDraft).not.toHaveBeenCalled();
      expect(screen.getByLabelText(/Highest qualification/)).toBeInTheDocument();
    });
  });
});
