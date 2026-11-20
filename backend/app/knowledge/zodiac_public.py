"""Public, visitor-safe knowledge for Zodi, the assistant on the Zodiac Patent
careers site (ZPHIREX).

Two sources only, and every statement traces to one of them:

* Zodiac Patent's work — the official website (zodiacpatent.com: home,
  /patent-drafting/, /quality-control/, reviewed 2026-11-18).
* How opportunities and applying work — what this product actually does (the
  public homepage's four opportunity cards, the careers page and its views,
  the program pages, the application form, the
  assessment consent screen, docs/).

It is written for a language model to ground on, so it states limits plainly:
where nothing is published (salaries, benefits, durations, scholarship
amounts, eligibility beyond a listing, contact channels), the entry says so
rather than leaving a gap for the model to fill.

Keep it public-safe: no internal architecture, administration, tenant or
personal data. Update it in the same change that alters the behaviour it
describes (CLAUDE.md § 7).
"""

from app.knowledge.base import KnowledgeEntry


def _kw(words: str) -> frozenset[str]:
    return frozenset(words.split())


#: Where candidates browse every current opening (the Open Roles page).
CAREERS_PATH = "/org/zodiac-patent"

#: The careers page opened on one focused view — where each homepage
#: opportunity card leads (frontend/src/features/careers/careersView.ts).
JOBS_PATH = f"{CAREERS_PATH}?view=jobs"
INTERNSHIPS_PATH = f"{CAREERS_PATH}?view=internships"
CAMPUS_PATH = f"{CAREERS_PATH}?view=campus"
PROGRAMS_PATH = f"{CAREERS_PATH}?view=programs"

ZODIAC_PUBLIC_KNOWLEDGE: tuple[KnowledgeEntry, ...] = (
    KnowledgeEntry(
        id="about-zodiac-patent",
        title="About Zodiac Patent",
        content=(
            "Zodiac Patent works in U.S. patent services. Since 2004 it has worked with U.S. "
            "patent law firms on patent application drafting, patent quality control and "
            "patent litigation support. The work involves understanding technical inventions "
            "and helping prepare, review and analyse patent documentation. ZPHIREX (this site) "
            "is the career portal for Zodiac Patent, where candidates explore opportunities "
            "and apply. The official website is zodiacpatent.com. Further company details - "
            "office locations, team size, leadership, culture, awards or clients - are not "
            "published here, so they are not available."
        ),
        keywords=_kw(
            "zodiac patent company organization organisation about who business founded "
            "history since 2004 zphirex firm services background"
        ),
        url=None,
    ),
    KnowledgeEntry(
        id="work-areas",
        title="The work Zodiac Patent does",
        content=(
            "Patent application drafting: preparing U.S. patent applications from an "
            "inventor's disclosure, working with patent attorneys and inventors. Patent "
            "quality control: reviewing drafts to find and correct specific issues - "
            "antecedent basis errors, word support errors, dependency errors and others - or "
            "suggest acceptable alternatives. Patent drawings: drawings created from scratch "
            "following USPTO guidelines, such as circuit diagrams, block diagrams and "
            "flowcharts, and improving drawings that are provided. Patent litigation support "
            "and research: infringement search and analysis and validity/invalidity search "
            "and analysis, focused on prior art that closely resembles the patent in "
            "question. Technology spaces named by the company include artificial "
            "intelligence, machine learning, software, cybersecurity, automotive and "
            "semiconductor. Which of these areas a particular role involves is described in "
            "that role's listing."
        ),
        keywords=_kw(
            "services drafting draft quality control qc review drawings drawing "
            "illustration diagrams flowchart litigation infringement validity invalidity "
            "prior art search research analysis uspto technology ai software "
            "semiconductor cybersecurity automotive engineering kind field domain"
        ),
        url=None,
    ),
    KnowledgeEntry(
        id="drafting-process",
        title="How a patent application is drafted",
        content=(
            "Zodiac Patent's published drafting process: 1) the invention disclosure "
            "documents are received; 2) an interview is held with counsel and the "
            "inventors; 3) proposed claims and figures are shared with counsel (draft plan); "
            "4) the first draft is shared for feedback from counsel and inventors; 5) "
            "feedback is incorporated in revised drafts; 6) the finalized patent application "
            "draft is shared with counsel."
        ),
        keywords=_kw("drafting process steps disclosure claims figures draft revision invention"),
        url=None,
    ),
    KnowledgeEntry(
        id="opportunities",
        title="Opportunities at Zodiac Patent",
        content=(
            "The homepage offers four ways in: Open Jobs, Internships, Campus Hiring and "
            "Training Programs (training, trainee and scholarship programs, including Zodiac "
            "Training Academy programs). Each opens the matching view of the careers page "
            f"({CAREERS_PATH}), which lists what is open now; the careers page also has an "
            "'All' view. Only what is listed there is open now."
        ),
        keywords=_kw(
            "opportunity opportunities options pathways paths available offer offered kinds "
            "types categories careers jobs openings"
        ),
        url="/",
    ),
    KnowledgeEntry(
        id="full-time-jobs",
        title="Full-time jobs",
        content=(
            "Full-time and part-time roles are listed under Open Jobs (homepage) - the "
            "careers page's Jobs view. Each listing shows "
            "what the team has published for it - for example department, location, work "
            "mode, experience range, and pay only if it was published. If a listing does not "
            "state something, that information is not available."
        ),
        keywords=_kw(
            "full time fulltime permanent job jobs role roles position experienced career"
        ),
        url=JOBS_PATH,
    ),
    KnowledgeEntry(
        id="internships",
        title="Internships",
        content=(
            "Internships are offered for students and aspiring professionals and appear as "
            "openings of type Internship (the Internships card on the homepage). An internship "
            "listing may state its duration, whether it is paid, and whether conversion to "
            "full-time is possible - but only when the team has published that for the "
            "listing. Nothing about duration, stipend or conversion is promised in general."
        ),
        keywords=_kw("internship internships intern interns stipend student students summer"),
        url=INTERNSHIPS_PATH,
    ),
    KnowledgeEntry(
        id="training-academy",
        title="Zodiac Training Academy",
        content=(
            "Zodiac Training Academy offers training and learning opportunities for "
            "candidates who want to develop professional knowledge and skills. Training is "
            "offered through programs listed under Training Programs (homepage); each "
            "program has its own page with its description, eligibility, the training "
            "durations it offers and any fee or scholarship information the team has "
            "finalized. Course names, syllabus, certificates and fees are not published "
            "beyond what a program page shows."
        ),
        keywords=_kw(
            "academy training train course courses learn learning program programs "
            "certificate certification syllabus fee fees zodiac"
        ),
        url=PROGRAMS_PATH,
    ),
    KnowledgeEntry(
        id="scholarships",
        title="Scholarships",
        content=(
            "Scholarship opportunities are offered through programs listed under Training "
            "Programs (homepage). A program's page shows scholarship amounts, eligibility "
            "rules and timing only when the team has finalized them; otherwise it shows the "
            "team's disclosure wording. There is no general scholarship amount, eligibility "
            "rule or deadline to quote - each program sets its own."
        ),
        keywords=_kw(
            "scholarship scholarships funding funded financial aid amount money support grant"
        ),
        url=PROGRAMS_PATH,
    ),
    KnowledgeEntry(
        id="trainee-programs",
        title="Trainee programs",
        content=(
            "Trainee programs are structured professional learning pathways, listed under "
            "Training Programs (homepage). Each program page states its own details "
            "(description, eligibility, training durations offered, closing date). Duration, "
            "pay or employment after the program are not promised beyond what a program page "
            "states."
        ),
        keywords=_kw("trainee trainees traineeship fresher freshers graduate pathway structured"),
        url=PROGRAMS_PATH,
    ),
    KnowledgeEntry(
        id="program-applications",
        title="Applying to a program",
        content=(
            "Programs are applied to on their own page (/programs/...). The application uses "
            "the same form as a job application, plus an education declaration - education "
            "level, year of study where relevant, specialization, institution, expected "
            "completion date and CGPA or percentage where asked - and acceptance of the "
            "program's terms. Applications are accepted only while a program is open; its "
            "page shows the closing date if there is one."
        ),
        keywords=_kw(
            "program programs apply application declaration education cgpa percentage "
            "institution college terms eligible eligibility deadline closing"
        ),
        url=PROGRAMS_PATH,
    ),
    KnowledgeEntry(
        id="campus-recruitment",
        title="Campus recruitment",
        content=(
            "Campus recruitment is one of the pathways, for college students, final-year "
            "students and recent graduates. When Zodiac Patent runs a campus drive with a "
            "college, the placement office shares a direct application link - no account "
            "needed. Drive links are private to each drive, so there is no public list of "
            "drives or participating colleges. Only open drives accept applications; if a "
            "drive has closed, the link says so. Some drives include an assessment. Students "
            "and graduates can also apply to listed openings and programs directly."
        ),
        keywords=_kw(
            "campus drive drives college colleges university placement student students "
            "final year graduate graduates recruitment fresher freshers"
        ),
        url=CAMPUS_PATH,
    ),
    KnowledgeEntry(
        id="how-to-apply",
        title="How to apply",
        content=(
            f"To apply for a job: choose Open Jobs or Internships on the homepage (or open "
            f"{CAREERS_PATH}), pick a role and use 'Apply for this role'. You confirm your "
            "email address with a one-time code sent to it, complete the application form "
            "and upload your resume. No account or login is needed. The page confirms when "
            "the application has been received. Applications are made per role; your profile "
            "may also be considered for other suitable roles."
        ),
        keywords=_kw(
            "apply applying applied application applications submit register account signup "
            "login portal code verification otp"
        ),
        url=CAREERS_PATH,
    ),
    KnowledgeEntry(
        id="application-requirements",
        title="What the application form asks for",
        content=(
            "Required: full name; email (confirmed with a one-time code); phone; date and "
            "place of birth; languages known; whether you are a Fresher or Experienced; "
            "current and preferred location; qualification; and a resume (PDF, DOC or DOCX, "
            "up to 10 MB). Experienced applicants also give years of experience, current "
            "title and company, and notice period in days (or mark themselves an immediate "
            "joiner). Optional: LinkedIn and GitHub links, 10th-standard marks percentage and "
            "the 10th marks card. Use the same email each time - it keeps one profile per "
            "person."
        ),
        keywords=_kw(
            "information fields form upload pdf docx size documents required "
            "mandatory notice period linkedin github qualification marks details"
        ),
        url=CAREERS_PATH,
    ),
    KnowledgeEntry(
        id="after-you-apply",
        title="What happens after you apply",
        content=(
            "After applying, the page confirms the application was received. The recruitment "
            "team reviews it against the role; it may move on through screening (AI-assisted, "
            "always reviewed by a person), an assessment for some roles, an interview and a "
            "decision. Updates such as an assessment invitation or interview details are sent "
            "by email, so check the inbox (and spam folder) of the address used. There is no "
            "candidate login or online status tracker, and no timeline is published."
        ),
        keywords=_kw(
            "after happens next status track follow response reply hear wait confirmation "
            "received notified outcome result selected selection process hiring "
            "steps stages timeline"
        ),
        url=None,
    ),
    KnowledgeEntry(
        id="assessments",
        title="How assessments work",
        content=(
            "Some roles include a short skills assessment. Candidates receive a personal "
            "invitation link (no login needed) for one attempt, with an expiry date. "
            "Questions are multiple-choice, and the duration and instructions are shown "
            "before starting. Before starting, the candidate must agree to a notice about "
            "browser-based monitoring: timestamped events such as switching tabs or windows, "
            "exiting fullscreen, camera/microphone becoming unavailable, and internet "
            "interruptions. Audio and video are not recorded or stored. After submitting, the "
            "candidate sees a confirmation but no score; the team follows up by email."
        ),
        keywords=_kw(
            "assessment assessments test tests exam quiz mcq invitation invite monitoring "
            "proctoring camera microphone webcam duration score attempt retest"
        ),
        url=None,
    ),
    KnowledgeEntry(
        id="ai-in-hiring",
        title="How AI is used in hiring",
        content=(
            "AI helps the recruitment team review applications against a role's "
            "requirements; it never decides. A person always makes the hiring decision. If an "
            "assessment uses monitoring, the candidate is told before starting."
        ),
        keywords=_kw(
            "ai artificial intelligence automated automatic screening bias human fair "
            "algorithm machine decide"
        ),
        url=None,
    ),
    KnowledgeEntry(
        id="not-published",
        title="Information that is not published",
        content=(
            "Salaries and pay (unless a listing states them), benefits, working hours, "
            "work-from-home policy, office locations, team size, scholarship amounts, "
            "program durations, fees and eligibility (unless a program page states them), "
            "placement or employment guarantees, and selection timelines are not published "
            "here. Say plainly that this information is not available, and point to the "
            "relevant listing or program page, or to the recruitment team's contact if one is "
            "provided."
        ),
        keywords=_kw(
            "salary salaries pay package ctc compensation benefits perks leave hours timing "
            "shift remote wfh home hybrid office location locations size employees guarantee "
            "guaranteed placement bond policy culture"
        ),
        url=None,
    ),
    KnowledgeEntry(
        id="contact",
        title="Contacting the recruitment team",
        content=(
            "If the recruitment team has published a careers contact email, it is given in "
            "the <contact> reference block, and it is the only contact to share. If there is "
            "no <contact> block, no contact details are published - none may be guessed. The "
            "team also contacts applicants by email once there is an update."
        ),
        keywords=_kw("contact reach email phone support address office enquiry inquiry"),
        url=CAREERS_PATH,
    ),
    KnowledgeEntry(
        id="about-zodi",
        title="About Zodi, the assistant",
        content=(
            "Zodi is the AI assistant on the Zodiac Patent careers site. It can explain the "
            "opportunities and how to apply, describe Zodiac Patent's work, point to current "
            "public openings and programs, and help with general job-search questions. It "
            "cannot look up an individual application or its status, cannot apply on "
            "someone's behalf, and does not make hiring decisions. Its answers are "
            "AI-generated and can contain mistakes; please avoid sharing sensitive personal "
            "information in the chat."
        ),
        keywords=_kw("zodi assistant chatbot bot chat privacy you yourself"),
        url=None,
    ),
)
