# Frontend design system

Visual language, motion rules, the home-page hero, and how they are kept fast and
accessible. This describes what is implemented; it is not a roadmap.

## Identity: HireZ

The product is **HireZ**. Its identity is a **text-only wordmark**
(`shared/components/Wordmark.tsx`): "Hire" in the ink colour and the "Z" in
brand indigo (`--color-wordmark-z`), display face, weight 750, tight
tracking (-0.05em), upright — no italic, gradient, symbol or image. There is no image
logo and no placeholder box anywhere in the UI; where the full word doesn't
fit (the collapsed sidebar rail) the compact form is the wordmark's indigo
"Z" on its own (`<Wordmark variant="mark" />`, weight 800). The accessible
name is always "HireZ". The browser tab reads "HireZ" on the home page and "<Page> · HireZ"
inside the app.

- **Indigo is the brand.** Neutral surfaces (off-white / zinc) and
  charcoal / off-white ink carry the page; indigo (`#4F46E5`, hover
  `#4338CA`, soft surface `#EEF2FF`) marks primary actions, links, focus,
  the wordmark's Z, the active nav item and key icons. **Teal** (`#0D9488`)
  is the one supporting accent: the selected-nav bar, section kickers and
  small supporting details. Secondary buttons stay neutral and bordered, so
  there is one clear primary action.
- **Status has its own colours.** Semantic tokens (success / warning /
  danger / info / neutral) carry meaning only, and the recruitment pipeline
  has a dedicated stage palette (below).
- **No decoration for its own sake.** No 3D, no ambient network artwork, no
  frosted/glass bars, no gradient washes or glows; depth is hairlines and
  restrained shadows. Bars and headers are solid surfaces.
- **Brand research.** No official colour specification for a parent brand
  could be verified (the company site exposes only image logos), so the
  palette is an independent choice. Every value lives in `tokens.css`;
  changing the brand means changing those values only.
- **Company vs product.** HireZ is the product name. SIGVITAS remains the
  company name where it is the actual organization: its careers page
  (`/org/sigvitas`), the footer copyright, the candidate-facing pages (which
  show the *hiring organization's* name, not the product's) and the Sigvi
  assistant. API titles, the CLI and environment variables were not renamed.

## Tokens (`frontend/src/styles/tokens.css`)

The single source of truth; pre-existing token names are kept so every screen
inherits the palette. Light / dark values:

| Token | Light | Dark | Use |
|---|---|---|---|
| `--color-primary-solid` (hover) | #4f46e5 (#4338ca) | #5850ec (#4f46e5) | primary buttons, solid indicators |
| `--color-on-primary` | #ffffff | #ffffff | text on primary fills |
| `--color-primary` (hover) | #4f46e5 (#4338ca) | #a5b4fc (#c7d2fe) | links, active nav text, indigo icons |
| `--color-brand` / `-brand-soft` / `-brand-soft-border` | #4f46e5 / #eef2ff / #c7d2fe | #818cf8 / #1f1e3d / #34336a | brand graphics, soft indigo tiles |
| `--color-wordmark-z` | #4f46e5 | #818cf8 | the Z in "HireZ" |
| `--color-accent` / `-text` | #0d9488 / #0f766e | #2dd4bf / #5eead4 | teal: selected-nav bar, kickers, supporting details |
| `--color-focus` | #4f46e5 | #818cf8 | focus ring (global `:focus-visible`) |
| `--glow-brand` / `--glow-accent` | indigo 22% / teal 10% | indigo 22% / teal 9% | the hero glow behind the product preview |
| `--shadow-brand` | indigo-tinted | indigo-tinted | primary-button hover lift |
| `--color-bg` / `-bg-subtle` | #f6f7f8 / #eef0f2 | #111214 / #151619 | canvas, neutral bands |
| `--color-surface` / `-surface-raised` | #ffffff / #ffffff | #1a1b1f / #202126 | cards, panels, shell |
| `--color-border` / `-border-strong` | #e3e5e8 / #cfd2d7 | #2a2b30 / #37393f | hairlines |
| `--shadow-card` / `-raised` / `-popover` | layered, 2–22% charcoal | layered, 40–75% black | resting cards / lifted panels / floating UI |
| `--canvas-grid` / `--canvas-light` | 5% ink / 85% white | 4% white / 4% white | the faint grid and soft light behind sign-in and the hero |
| `--weight-display` | 650 | 650 | page and section headings |
| `--color-input-border` | #7d838c | #6c717b | form-control outlines (3:1) |
| `--color-text` / `-text-muted` | #15181d / #565c66 | #ebecee / #a4a8b0 | text |
| `--color-{danger,success,active,inactive,warn,info,interview}-{bg,text}` | | | badges and alerts |
| `--kind-*` | | | categorical (org-chart departments, calendar kinds) — not status |

### Recruitment pipeline stages

One mapping (`shared/lib/statusTone.ts`): `pipelineStage(status)` maps the
API status to a stage, `pipelineColor(status)` gives the stage's token, and
`statusTone(status)` gives the badge tone. Unknown statuses fall back to
neutral. Used by the overview and reports charts, the AI pipeline panel,
the recent-activity timeline, `PipelineTrack` (current node and mini bar),
status badges and the home page.

| Stage | Statuses | Token (light / dark) |
|---|---|---|
| Applied | APPLIED | `--pipeline-applied` #7b8698 / #8d98ab (neutral) |
| Screening | UNDER_REVIEW, SCREENING | `--pipeline-screening` #b7791f / #e0a83a (amber) |
| Assessment | ASSESSMENT_INVITED / STARTED / COMPLETED | `--pipeline-assessment` #2b6cc4 / #6ea0f0 (blue) |
| Interview | SHORTLISTED, INTERVIEW | `--pipeline-interview` #6d4fd8 / #a58df7 (violet) |
| Selected | SELECTED | `--pipeline-selected` #2f8f5b / #55c48a (green) |
| Hired | HIRED | `--pipeline-hired` #15703f / #3fb877 (deep green) |
| Rejected | REJECTED | `--pipeline-rejected` #c13030 / #ef7d7d (red) |
| On hold | AI_SCREENED_OUT (advisory) | `--pipeline-hold` #a5762a / #c9a24a (muted amber) |

Stage colour is never the only signal: stage names, counts and percentages
stay visible next to every bar, dot and badge.

### Type, shape, depth

One family (the platform UI face; no webfont), a fixed scale (`--text-*`)
plus a fluid public scale (`--text-display`, `--text-headline`,
`--text-lead`). Page and section headings use `--weight-display` (650) with
tight tracking (-0.03em / -0.02em) and balanced wrapping. Radii 6 / 8 / 10 /
14 px. Depth is layered and low-opacity: a hairline border plus
`--shadow-card` at rest, `--shadow-raised` for lifted panels (sign-in card,
hero preview), `--shadow-popover` for floating UI; dark mode adds a 4% top
highlight instead of heavier shadows. The shell (sidebar, top bar) and cards
sit on the surface colour above the canvas, so the dashboard reads in layers
in both themes.

### Sign-in (`features/auth/LoginPage.tsx`, `styles/auth.css`)

A centered card (max 25rem) on the canvas: monochrome wordmark, "Sign in",
one line on who signs in, the two fields and a full-width primary button,
with a note that staff accounts come from the organization admin. Behind it,
`--canvas-grid` lines fade out radially from a `--canvas-light` pool with
two faint `--glow-brand` / `--glow-accent` pools; the card stays solid (no
glass) with a soft brand shadow, a fine indigo-to-teal hairline on its top
edge and a short rise-in. Inputs darken their border on hover, show an
indigo focus ring, and turn red only for the browser's own validation after
the user has interacted (`:user-invalid`). While signing in, the button
shows a spinner and the form is `aria-busy`; an error alert rises in.
Back-to-home and the theme toggle sit in a light top bar. Authentication,
validation, errors and redirects are unchanged; no credentials appear in the
UI.

### Sidebar scrolling (`styles/shell.css`)

The page frame never scrolls: `.app-shell` is the viewport height and only
`.content` scrolls. Inside the sidebar, only the link list (`.sidebar-nav`)
scrolls — the header row and footer stay fixed — with the shared
auto-hiding scrollbar (below), `overscroll-behavior: contain` (the page doesn't scroll
when the list hits its end) and a short fade at the list's top and bottom
edges. The `<aside>` itself is `overflow: hidden`. The phone drawer leaves
room for the safe-area inset.

The header is one compact row (`.sidebar-brand`, 36px): the collapse/expand
button on the far left, the HireZ wordmark beside it, both vertically
centred; the nav starts 12px below. The toggle is a borderless 36px icon
button with the same corner radius and hover fill as a nav link. On the
collapsed 72px rail the same 36px row shows the toggle and the compact
"Z" mark side by side, centred as a pair (the rail never widens, and the
row height doesn't change, so the nav doesn't move). Swapping between the
full wordmark and the mark fades the incoming form in (opacity/scale,
`--dur-base`). Phones have no toggle and never collapse: the drawer header
is the full wordmark with the close button on the right.

### Scrollbars (`index.css`)

On devices with a fine pointer that can hover, every scroll area keeps a
constant 8px transparent gutter (so nothing shifts) and shows a slim,
rounded thumb only while the pointer is over that area — including over
the bar and while dragging — or while keyboard focus is inside it. The
thumb is `--scrollbar-thumb`, a translucent wash of `--color-text-muted`
(soft slate in light mode, lighter gray in dark), darkening to
`--scrollbar-thumb-active` under the pointer. Chromium and Safari use the
`::-webkit-scrollbar` parts; Firefox gets `scrollbar-width: thin` and
`scrollbar-color` (set only where `::-webkit-scrollbar` is unsupported,
because Chromium ignores the webkit parts on elements that set them), with
no rounded-corner or thumb-hover control. Nothing transitions — scrollbar
parts don't animate reliably across engines. Touch screens keep the
platform's native overlay scrollbars. Scrolling is never disabled; the one
deliberately hidden bar is the swipeable pipeline track.

### Startup screen (`index.html`, `styles/loading.css`, `shared/lib/brandLoading.ts`)

A cold load never shows a blank page: `index.html` contains a full-screen
`#brand-loading` overlay on `--color-bg`: a bold, stationary indigo "Z"
(display face, weight 800, `--color-wordmark-z`) centred in a thin circular
track (3px, a 14% wash of the wordmark indigo), sized together by one
variable (`clamp(6.5rem, 24vmin, 8.75rem)`). A highlight travels around the
track — a conic sweep from transparent through indigo to a rounded teal
(`--color-accent`) head, cut to the track's width by a radial mask — on a
1.1s linear `transform: rotate` loop. The ring and the Z are separate
layers: only the ring rotates. A tiny inline script applies a saved theme choice before first
paint (same key as `ThemeContext`), so the overlay matches the theme.

Lifecycle: it stays until real route content has committed (`AppReady`
inside the router's Suspense boundary) and nothing holds it
(`HoldBrandLoading`): a protected route restoring the session, the sign-in
page while the session is checked or a signed-in redirect is pending, and
the code-loading fallback. It then fades out (280ms), leaves the
accessibility tree at once and is removed; while shown, `#root` is `inert`.
Route changes never bring it back, and there is no artificial delay — a
fast start is just a short fade. Fail-safe: it leaves after 6s whatever
happens, revealing the app's own loading text or error state. Reduced
motion: the static Z and a static arc, removed without a fade. Forced
colours: a plain system-coloured Z and track, no arc.

### Key figures (`shared/components/MetricCard.tsx`)

Value first, label second, context third. The icon (the project's own
`Icon` set) sits at full strength on a small tinted tile in the top-right
corner, coloured by the card's meaning (stage colours for selected / hired /
rejected / assessment, teal for the lead figure, muted gray for neutral
ones), with a matching 2px top edge — recognizable at a glance in both
themes, small enough not to compete with the figure. Used by the overview
and reports. Values are always real API figures.

### Contrast (measured)

Every text and graphic pair the system relies on was computed from
`tokens.css` for both themes (WCAG 2.2: 4.5:1 text, 3:1 large text and
non-text UI) — 146 pairs, all pass (white on the indigo button 6.3:1 light
/ 5.6:1 dark; indigo on its soft tiles 5.6:1 / 8.0:1; both ends of the
headline gradient pass as large text). For example: primary button text 15.8:1
(light) / 14.5:1 (dark); metric icons on their tiles 3.2–5.7:1 (light) and
5.1–7.2:1 (dark); the focus ring 3.9–4.2:1 / 6.7–7.3:1; every pipeline stage
at least 3.35:1 against cards; org-chart connectors and search-dimmed text
pass in both themes. The small wordmark is 19px bold so it qualifies as
large text.

## Motion rules

Every animation must communicate state, hierarchy, direction, feedback or
continuity. Only `transform`/`opacity` animate; movement is ≤ ~14px.

| Primitive | Where | Behaviour |
|---|---|---|
| `PageTransition` | recruiter/admin shell | 200ms fade + 6px rise on route change; entrance only, never waits on an outgoing page |
| `Reveal` / `useInView` | landing, careers | scroll-in with direction and stagger; latches. Fail-open: visible without IntersectionObserver, if creating the observer throws, and — for an element too tall to ever show `threshold` of itself — as soon as any of it is on screen |
| Home hero | public home | headline, copy and actions rise in, then the product preview (320ms later), then its two small cards (scale/opacity). Afterwards only those two cards drift a few px on slow 7s/9s cycles, and the layers follow the pointer by a few px (fine pointer, ≥1000px wide). Entrance, drift and parallax use `scale`/`opacity`, `transform` and `translate` respectively, so they never fight |
| Capability cards, journey | public home | cards stagger in; on hover a faint indigo wash, a 2px lift and a filled icon tile. The "how it works" line draws itself left to right, then the four steps follow it in |
| Header | public home | turns lightly frosted once content scrolls beneath it — the one use of blur |
| `AnimatedNumber` | dashboard figures | counts up when first seen and on change; **renders the true value at once** for reduced motion / no rAF / no IO |
| Skeletons | every list/page | shapes mirror the real layout (stat grid, list, table, org chart) |
| FLIP | org chart | after a reorder, cards glide to their new place (same department only) |
| `PipelineTrack` | application detail, candidate detail | the eight hiring stages with the real status marked; the connector fills and the new stage arrives when the status changes. Display only — moving an application is still "Move to…". Scrolls sideways on phones, centred on the current stage |
| Status feedback | jobs, application detail | a toast from the real mutation result ("“Data Analyst” is now on hold.", "Moved to Interview.") plus a one-off row flash / badge pop on the job that changed |
| Activity stream | activities | entries grouped under Today / Yesterday / dated headings on a vertical line with a node per event kind; Delete appears on hover/focus (always visible on touch); an entry being deleted recedes |
| Composer steps | email composer | *Compose → Review & send* follows the composer's real step |

**Reduced motion** is global: `tokens.css` collapses all durations and
animation counts under `prefers-reduced-motion`, and `Reveal`/`AnimatedNumber`/
FLIP each opt out explicitly; the home page's drift and parallax only exist
under `prefers-reduced-motion: no-preference` and above 560px. Verified in a
real browser (headless Edge): two infinite animations on the home page with
motion allowed, zero with reduced motion, and every reveal visible.

Not done, deliberately: dialogs have an entrance but **no exit animation**.
`Modal` closes synchronously (`onClose` is called immediately and its tests pin
that); delaying it to play an exit would change a tested contract across a
dozen dialogs for a cosmetic gain. Parent-driven closes would also still
snap, so a partial version would look inconsistent.

## The public home page

`features/public/PublicHomePage.tsx` + `styles/home.css`. Short and
outward-facing: header (wordmark, four section links, theme, staff sign-in),
hero, five core capabilities as a card grid (three across, then two wider)
beside a short lead, a four-step "how it works" as step cards, a short campus-recruitment section, a neutral closing band (staff
sign-in, open roles) and a minimal footer with the SIGVITAS copyright.
Internal operating rules — approvals, separation of duties, audit
mechanics, tenancy, grading and status rules — are deliberately not on the
public page; they live in the signed-in product and the docs. No
testimonials, logos, statistics or integrations.

The hero visual (`HeroProductPreview.tsx`) is a layered preview of the
product itself, in HTML/CSS: an application panel (stage badge, the six real
pipeline stages as a compact track with the current stage in its pipeline
colour, the next step), with an assessment card (indigo edge and icon tile)
and an interview-feedback card (teal) resting on its corners. Behind it, a
large, soft indigo radial glow with a smaller teal tint (`--glow-*`) sits at
z-index -1 — always behind the cards, clipped by the hero. The headline's
second line uses a restrained indigo-to-teal text gradient (solid text in
forced-colours mode). Capability icons sit on soft indigo tiles; the "how it
works" track starts solid indigo and fades to a hairline. Names in the
preview are placeholder lines; there are no scores, counts or rates. It is
one image to assistive tech (role="img" with a description). Under 560px the
resting cards follow the panel instead of overlapping. The earlier Three.js
scene, network artwork and simple SVG pipeline were removed (with the
`three` dependency).

## Sigvi (the public AI assistant)

`features/sigvi/` + `styles/sigvi.css`. Sigvi is part of the site, not a
separate product: it uses the tokens for every colour, shadow, radius, type
size and duration, so it follows light/dark with the page. One accent (the
primary blue: visitor messages, Send, Apply, links; suggestion chips are
neutral and turn blue on hover),
neutral surfaces separated by hairlines, `--shadow-popover` for the panel and
`--shadow-raised` for the launcher. No glows, gradients, star fields or
orbiting decoration. The one piece of character is the small inline-SVG robot
head, used as an icon (launcher, header, beside replies) and always
decorative. Motion is limited to short fades; nothing runs at rest, and
reduced motion switches every Sigvi animation off.

Rendered through a portal into `<body>` (same reason as dialogs). Non-modal
floating panel on desktop; full-screen sheet ≤ 640px that tracks the visible
viewport (16px input to prevent iOS zoom, safe-area padding, page scroll locked
beneath, keyboard-aware). It appears only on the home, careers and role pages —
never on assessment/campus-drive pages or the staff app. Details:
`docs/sigvi.md` § 8.

## Dialogs

`Modal` and `ConfirmDialog` (every dialog in the app goes through one of them)
render into `document.body` through a portal, and `.dialog-overlay` is
`position: fixed; inset: 0`. The portal is not optional: the page-transition
wrapper keeps a `transform` after its entrance, and a transformed ancestor
becomes the containing block for `position: fixed` — an inline overlay was
sized and scrolled with the page's *content*, so a dialog appeared far above or
below the visible area depending on how long the page was and where it was
scrolled. A dialog taller than the viewport scrolls itself (`.dialog-card`
is capped at the viewport height and has `overflow-y: auto`); wheel scrolling
never reaches the page behind. Positioning uses no measurement or JavaScript.

## Route-level code splitting

The landing page and sign-in ship in the main bundle; every other screen is a
lazy chunk (`app/routes.tsx`). The main bundle went from ~514 kB to ~330 kB
(~143 → ~103 kB gzip), so a candidate opening the landing page never downloads
the recruiter app.

## Accessibility

Skip link, focus-visible ring, `aria-current` navigation, a phone drawer that
is `inert` while closed, closes on Escape and route change, and moves focus to
its close button. Focus uses a solid `--color-focus` ring; the active nav item
is marked by background, weight and an accent bar (not colour alone); the home
illustration has a title and description. Org-chart reordering keeps
its keyboard alternative (Arrow Up/Down on the handle). Status is never colour
alone (badge dots, shape-coded legend swatches). The assessment shows real
progress derived from the candidate's answers and does **not** invent a timer —
the backend enforces none.

## Verifying visually

There is no visual-regression harness in CI. Changes were checked in Edge —
headless with software WebGL for the scenarios (desktop / tablet / phone, both
themes, reduced motion, WebGL forcibly removed, mocked-data states), and in a
**headed window on a real integrated GPU** for the actual look and frame rate
(the hero held ~56–57 fps there). Populated recruiter screens were viewed with
QA-only mocked API responses that are not part of the repository. Numbers from
one integrated GPU are indicative, not a device matrix.
