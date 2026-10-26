# Frontend design system

Visual language, motion rules, the 3D hero, and how they are kept fast and
accessible. This describes what is implemented; it is not a roadmap.

## Identity: "Signal"

The product is a *talent graph* — people, jobs and skills as nodes, matches as
edges. That one motif is reused everywhere: the logo mark, the sliding nav
indicator, timelines, the hiring-process spine, the ambient network artwork,
the org-chart lines and the 3D hero.

- **One brand hue** (signal blue), **one warm accent** (amber, "the human in
  the loop" — the hired stage, a matched candidate, a human-decision step),
  and cool ink neutrals. Semantic colours (success / warning / danger / info /
  neutral) carry meaning only.
- **The public site is Sigvitas-first.** The landing page, hero and careers
  pages carry the Sigvitas name and its mark; "AI Recruitment Platform" is the
  name of the recruiter product and is not the public brand. The network /
  3D motif is there to say *intelligence behind the recruiting*, not to rename
  the site. The logo mark echoes the shipped favicon (brand tile, circuit
  nodes, magnifier, person).
- **Dark is designed, not inverted**: a neutral graphite surface scale
  (`--color-bg` #0f1115 < `--color-surface` #171a20 < `--color-surface-raised`
  #1d2129) rather than blue-tinted near-black; hairline borders do the
  separating and shadows stay flat; the brand blue is kept for actions,
  links and focus; status colours are desaturated so badges read on dark
  without glowing.
- **The recruiter workspace is quiet.** The network/talent-graph artwork and
  soft brand glows belong to the public site. Inside `/recruiter` there is no
  page-wide glow, no frosted top bar, no gradient header card or gradient
  empty states, and key-figure cards don't lift on hover (they aren't
  clickable); colour is reserved for status and actions.

## Tokens (`frontend/src/styles/tokens.css`)

The single source of truth. Pre-existing names (`--color-bg`, `--color-surface`,
`--color-primary`, …) are kept, so every page inherits the palette. Additive
tokens: type scale (`--text-*`, three weights only), radii, elevation
(`--shadow-card/raised/popover`), motion (`--dur-*`, `--ease-*`), and the
talent-graph category palette (`--kind-*`), which the 3D scene, the SVG poster
and the legend all read — so they can never drift apart.

Typography uses one family everywhere: the platform UI face (`--font-sans` /
`--font-display`: Segoe UI Variable, SF Pro, Roboto). No webfont is loaded.
The public site (landing, careers, Sigvi) adds a small fluid scale:
`--text-display` (hero, 36–52px), `--text-headline` (section titles, 26–34px)
and `--text-lead` (intro paragraphs, 17–19px), with matching
`--leading-*` / `--tracking-*` tokens. Display and headline sizes use the
medium weight (500), and headlines are a single ink colour. Brand blue is
reserved for actions and links, not for headline words or eyebrows, which are
muted small caps (`--tracking-caps`).

`--color-primary` is for text/links; `--color-primary-solid` is the fill behind
white text (buttons). They are separate so each clears WCAG AA in each theme.

Form controls use `--color-input-border` and `--color-input-bg`: the outline
is 3:1 against its surface in both themes (WCAG 1.4.11), which the general
`--color-border-strong` hairline is not. Radii are 6 / 8 / 10 / 14 px
(`--radius-sm/md/lg/xl`).

**Contrast is audited, not eyeballed**: every text and graphic token pair in
both themes was checked against WCAG (4.5:1 text, 3:1 graphics) directly from
`tokens.css`; all 56 pairs pass. (The audit found and fixed two: the light
"matched" amber on the hero stage, and the dark primary button's hover fill.)
The graphite dark palette was re-checked the same way: body text 15.6:1 on
the page and 13.3:1 on raised surfaces, muted text 6.4–6.9:1, links 8.1:1,
white on the primary button 5.1:1, every status text on its badge 6.8–9.3:1.

## Motion rules

Every animation must communicate state, hierarchy, direction, feedback or
continuity. Only `transform`/`opacity` animate; movement is ≤ ~14px.

| Primitive | Where | Behaviour |
|---|---|---|
| `PageTransition` | recruiter/admin shell | 200ms fade + 6px rise on route change; entrance only, never waits on an outgoing page |
| `Reveal` | landing, careers | scroll-in with direction and stagger; latches; visible immediately without IntersectionObserver |
| `AnimatedNumber` | dashboard figures | counts up when first seen and on change; **renders the true value at once** for reduced motion / no rAF / no IO |
| `Magnetic` | hero + closing CTA | a few px of pull toward a fine pointer; off for touch and reduced motion |
| Skeletons | every list/page | shapes mirror the real layout (stat grid, list, table, org chart) |
| FLIP | org chart | after a reorder, cards glide to their new place (same department only) |
| `PipelineTrack` | application detail, candidate detail | the eight hiring stages with the real status marked; the connector fills and the new stage arrives when the status changes. Display only — moving an application is still "Move to…". Scrolls sideways on phones, centred on the current stage |
| Status feedback | jobs, application detail | a toast from the real mutation result ("“Data Analyst” is now on hold.", "Moved to Interview.") plus a one-off row flash / badge pop on the job that changed |
| Activity stream | activities | entries grouped under Today / Yesterday / dated headings on a vertical line with a node per event kind; Delete appears on hover/focus (always visible on touch); an entry being deleted recedes |
| Composer steps | email composer | *Compose → Review & send* follows the composer's real step |

**Reduced motion** is global: `tokens.css` collapses all durations and
animation counts under `prefers-reduced-motion`, and `Reveal`/`AnimatedNumber`/
`Magnetic`/FLIP/the 3D scene each opt out explicitly. Verified in a real
browser: zero running animations page-wide.

Not done, deliberately: dialogs have an entrance but **no exit animation**.
`Modal` closes synchronously (`onClose` is called immediately and its tests pin
that); delaying it to play an exit would change a tested contract across a
dozen dialogs for a cosmetic gain. Parent-driven closes would also still
snap, so a partial version would look inconsistent.

## The 3D hero (`frontend/src/features/public/hero/`)

Three layers, so almost all of it is testable without a GPU:

1. **`networkLayout.ts`** — pure and deterministic (seeded): orbits, nodes per
   category (candidates, jobs, skills, assessments, organizations, AI
   analysis), ring links, spokes to the core, candidate↔job "match" pairs and
   the particle field. Two densities (`full`, `reduced`).
2. **`network3d.ts`** — the imperative Three.js scene (used directly; no React
   renderer). One `InstancedMesh` per category, link/ring geometry static in
   each orbit's local space (only Group transforms animate), a handful of live
   match beams, one `Points` field. Reacts to pointer (parallax + per-node
   proximity), scroll (drifts back and fades), theme (re-reads the palette
   with no reload) and the legend (highlights a category). Pointer/scroll are
   smoothed with exponential damping.
3. **`HeroScene.tsx`** — lifecycle: the SVG **poster** (`NetworkPoster.tsx`,
   the same layout drawn flat) is the first paint; Three.js is a separate chunk
   fetched after first paint when idle; the canvas fades in when ready.

**Fallbacks** — the poster is a complete experience, not an error state: it is
what shows for reduced motion, for browsers without WebGL, if the chunk fails
to load, and if the GPU context is lost.

**Composition** — the scene is a quiet companion to the headline, not a second
hero: one hue family (the `--kind-*` palette, the amber match beam as the only
warm accent), low opacities, calm materials, ~33 nodes on desktop. It sits
right of the copy at ≥ 1100px and never behind text; below that it stacks under
the copy in its own block. The legend and proof line are plain text, not chips.

**Performance** — the 3D chunk is *not* requested at first paint; the render
loop runs only while the hero is on screen and the tab is visible; pixel ratio
is capped (lower on phones) and shed if frames run slow; everything is disposed
on unmount. Phones/tablets use the `reduced` density, no per-node proximity,
and a stacked layout (copy first, scene in its own block — never behind text).

The **only new dependency** is `three` (+ `@types/three`, dev). Nothing in the
project provided 3D/WebGL or animation; react-three-fiber and drei were not
added.

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
its close button. The 3D canvas/poster are `aria-hidden`; the legend is a real
button group (`aria-pressed`, hover **and** focus). Org-chart reordering keeps
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
