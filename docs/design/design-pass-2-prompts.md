# Design pass 2: the user's prompt templates (verbatim)

Source: pasted by the user on 2026-10-16 (about 02:00) as the raw design brief for "design pass 2". These are TEMPLATES with `[placeholders]`; the filled-in, ordered, agent-sized version is `docs/design/design-pass-2-pipeline.md`. This file is the untouched original so nothing is lost in the reorganisation. Rule from the user: the design pass runs only after Phase 19 (19b and 19c) is fully done.

---

## Prompt 1: UX architecture document

You are a Senior UX Designer and Information Architect with 10+ years of 
experience designing complex digital products. I need you to generate a 
complete UX architecture document for my product.

---

PRODUCT CONTEXT
- Product name: [NAME]
- Product type: [Web app / Mobile app / Both]
- Industry: [e.g. Fintech / Health / E-commerce / SaaS]
- Target users: [Describe your primary user, who they are, their goals, 
  tech comfort level]
- Core problem it solves: [1–2 sentences]
- Key user actions (the 3–5 things users must be able to do): [List them]
- Any known competitors or inspiration: [Optional]

---

WHAT I NEED YOU TO GENERATE

1. INFORMATION ARCHITECTURE (IA)
- Full sitemap of every screen/page in the app
- Grouped by section (e.g. Onboarding, Dashboard, Settings)
- Hierarchy shown clearly (parent → child pages)
- Navigation structure (bottom nav / sidebar / top nav, recommend which 
  and why)

2. USER FLOWS
Generate step-by-step user flows for these core journeys:
- Onboarding & account creation
- Core action flow (the main thing the user comes to do)
- Settings & profile management
- Error recovery flow
For each flow: show every screen the user touches, the decision points, 
and what happens on each path (success, error, edge case).

3. FULL SCREEN-BY-SCREEN BREAKDOWN
For EVERY screen in the app, provide:
- Screen name
- Purpose (what the user is trying to do here)
- Key UI elements on the screen
- Primary action (main CTA)
- Secondary actions
- Navigation options available

4. MICROCOPY FOR EVERY SCREEN
For each screen, write the actual copy for:
- Page/screen title
- Subheading or supporting text
- CTA button labels
- Input field placeholder text and labels
- Tooltip or helper text (where relevant)

5. ERROR STATES
For every screen or action that can fail, write:
- The error scenario (what went wrong)
- Error message headline (short, clear)
- Error body copy (explain what happened + what to do)
- CTA label to recover (e.g. "Try Again", "Go Back", "Contact Support")
Cover at minimum: form validation errors, network/connection errors, 
permission denied, session expired, payment failure (if applicable).

6. EMPTY STATES
For every screen that can appear empty (no data yet), write:
- Empty state illustration description (what to show visually)
- Headline
- Supporting copy
- CTA to help user take their first action
Cover: first-time user empty states AND zero-result search/filter states.

7. LOADING & TRANSITION STATES
For key screens, describe:
- What loading state looks like (skeleton screen, spinner, etc.)
- Loading copy if any (e.g. "Getting your dashboard ready…")
- Transition message after a completed action 
  (e.g. success toast, confirmation screen)

8. STRUCTURAL RECOMMENDATIONS
Based on the product type and user goals:
- Recommend the best navigation pattern and why
- Flag any screens that might cause confusion or drop-off
- Suggest any flows that could be simplified or merged
- Identify where progressive disclosure should be applied
- Call out any accessibility considerations per screen

---

OUTPUT FORMAT
Structure your response like a UX specification document with clear 
headings for each section. Use tables where it helps clarity 
(especially for microcopy and error states). Be exhaustive, I want 
this to be thorough enough to hand directly to a developer or use as 
a Figma annotation reference.

---

## Prompt 2: ASCII wireframe

Create an ASCII art mockup/wireframe for [interface/component name].

Include:

1. Layout Structure
   - Use ASCII characters to represent UI elements
   - Boxes: ? ? ? ? ? ? (or + - | for simpler)
   - Text labels: Clear, concise labels
   - Spacing: Consistent indentation and alignment

2. Component Elements
   - Headers: Use = or - for section dividers
   - Buttons: [Button Text]
   - Input fields: [___________]
   - Navigation: > Home | Products | About
   - Content areas: Use | for columns, - for rows

3. Visual Hierarchy
   - Use spacing to show grouping
   - Use different characters for emphasis
   - Show relationships between elements
   - Indicate interactive vs. static elements

4. Responsive Considerations (if applicable)
   - Show mobile vs. desktop layouts
   - Indicate breakpoints
   - Show how elements reflow

5. Annotations (optional)
   - Add notes explaining design decisions
   - Label key interactions
   - Mark important elements

Format as clean, readable ASCII art that clearly communicates the interface structure.

---

## Prompt 3: Length-expansion stress test

Run a length-expansion stress test for this UI.

Context:
- Product/feature: [name]
- Key screens/components: [list]
- Target expansion factor: [30% / 50% / 70%]
- Target locales: [list]

Inputs:
[Paste UI copy, screenshots/links, and component constraints]

Provide:

1. High-Risk Strings
   - Strings most likely to expand significantly
   - Strings with variables/placeholders that can expand unpredictably

2. Breakpoints & Failure Modes
   For each screen/component, identify:
   - Where truncation will occur
   - Where wrapping will create layout shifts
   - Where buttons/inputs/nav items will overflow
   - Where table columns/cards will break

3. Recommended Fixes
   - Copy alternatives (shorter phrasing, remove redundancy)
   - Layout fixes (wrap rules, flexible widths, max-widths, responsive stacking)
   - Component fixes (icon-only variants, adaptive labels, overflow menus)
   - When truncation is acceptable vs not (and tooltip/expand patterns)

4. Test Cases
   - A checklist of ?strings to test? + ?screens to verify?
   - Suggested pseudo-localized examples (optional)

Format as a screen-by-screen punch list with recommended fixes.

---

## Prompt 4: Design taste before pixels

You set design taste before pixels. No generic AI aesthetic. Read the room, commit to a direction, then specify the system.

Context:
What we are designing: [Landing / portfolio / redesign / product surface / other]
Audience: [Who]
Vibe words (if any): [minimal, editorial, playful, Linear-like, premium, trust-first, etc.]
References: [URLs, products, screenshots — or "none"]
Existing brand assets: [Logo, colors, type — or "none"]
Hard constraints: [A11y, regulated, dark-only, etc. — or "none"]
Current state: [Paste brief, wireframe, or UI summary]

## Process

### 1. Design read (one line)
"Reading this as: <page kind> for <audience>, with a <vibe> language, leaning toward <aesthetic family>."

If the brief is ambiguous between two real directions, ask exactly ONE clarifying question and stop. Otherwise do not ask.

### 2. Three dials (1–10) with justification
- DESIGN_VARIANCE (1 = perfect symmetry … 10 = artsy chaos)
- MOTION_INTENSITY (1 = static … 10 = cinematic)
- VISUAL_DENSITY (1 = gallery airy … 10 = cockpit packed)

Defaults for marketing/landing-ish work unless the read overrides: 8 / 6 / 4.
Trust-first / public-sector: pull variance and motion down.

### 3. Anti-default lock
Explicitly ban the LLM defaults that do not fit this read (examples: purple gradients, Inter+slate, three equal feature cards, glass everywhere, cream parchment premium).

### 4. Taste system
Deliver:
- Aesthetic name (2–5 words) + one-sentence why it fits
- Signature element (ONE memorable move)
- Palette roles with hex or OKLCH
- Type pairing and hierarchy rules
- Layout idea for the primary viewport (short + tiny ASCII if helpful)
- Motion intent gated by MOTION_INTENSITY (what moves vs stays still)
- Content/voice cues that match the visual taste
- Non-goals (what this design will refuse)

### 5. First-build checklist
Ordered steps for the next implementation pass (not a full redesign essay).

Be opinionated. Taste is a commitment, not a moodboard of five conflicting styles.

---

## Prompt 5: Color and typography system (with a visual hierarchy prompt appended)

Act as a Lead Visual Designer specializing in design systems and visual foundations with deep expertise in color theory, typographic hierarchy, and accessibility standards.

I need to build a comprehensive color and typography system for the following product:

Product Context:
Product Name: [name]
Product Type: [e.g., SaaS dashboard, e-commerce app, mobile banking, consumer social app]
Brand Personality: [3-5 adjectives]
Starting Color (if any): [paste hex or describe the hero brand color - e.g., "a deep teal, approximately #0D9488"]
Starting Font (if any): [name any locked typeface - or say "none, recommend from scratch"]
Key UI Surfaces: [e.g., light mode only / dark mode only / both / high-density data tables / marketing pages]
Accessibility Level Required: [WCAG AA / WCAG AAA]
Platform: [Web / iOS / Android / All]

SECTION A: COLOR SYSTEM

Please generate a complete color system that includes:

Primitive Color Palette (Raw Scale)
For the brand's hero color, generate a full 10-step scale (50-900) with:
- Hex value for each step
- Approximate HSL values
- Suggested use case for each step (e.g., "500 = primary interactive, 700 = hover state, 100 = subtle background")

Semantic Color Tokens
Map the primitive scale to semantic tokens:
- Interactive: color.interactive.default / hover / active / disabled
- Background: color.bg.page / surface / overlay / inverse
- Text: color.text.primary / secondary / disabled / inverse / link
- Border: color.border.default / strong / subtle
- Status: color.status.success / warning / error / info (each with default + background variants)

Contrast Compliance Table
Create a table showing:
| Text Color Token | Background Token | Contrast Ratio | WCAG AA Pass? | WCAG AAA Pass? |
(List the 8 most common text/bg pairings)

Dark Mode Adaptation
If dark mode is required: map each semantic token to its dark mode equivalent
Flag any colors that need a completely different value vs. a simple inversion

SECTION B: TYPOGRAPHY SYSTEM

Type Scale
Generate a modular type scale with:
Scale name | Font size | Line height | Letter spacing | Font weight | Usage
(Include: display, h1, h2, h3, h4, body-lg, body-md, body-sm, label, caption, overline)

Font Pairing Recommendation
- Primary typeface (headings): name + rationale + fallback stack
- Secondary typeface (body): name + rationale + fallback stack
- Monospace (code/data, if needed): name + rationale
- Licensing: flag free vs. paid for each recommendation

Typographic Hierarchy Rules
- When to use each scale level
- Weight usage guidelines (when to use regular vs. medium vs. bold)
- Line length guidelines (optimal character count per line for body text)
- When to use caps, italics, or underline - and when never to

Design Token Naming Convention
Provide the full token naming schema in a format ready for Style Dictionary or Figma Tokens plugin

Design the visual hierarchy for [page/layout]. Include:

1. Content Priority
   - Primary content (most important)
   - Secondary content
   - Tertiary content
   - Call-to-action placement

2. Visual Weight
   - Size relationships
   - Color contrast
   - Typography scale
   - White space usage

3. Layout Structure
   - Grid system
   - Content zones
   - Alignment principles
   - Proximity grouping

4. Typography Hierarchy
   - Heading sizes (H1-H6)
   - Body text sizes
   - Emphasis techniques
   - Line length and spacing

5. Color and Contrast
   - Primary color usage
   - Accent color placement
   - Background colors
   - Text contrast ratios

6. Visual Flow
   - Eye movement patterns
   - Scanning paths
   - Focal points
   - Content grouping

7. Responsive Considerations
   - Mobile hierarchy
   - Tablet hierarchy
   - Desktop hierarchy
   - Breakpoint adjustments

Format as a visual hierarchy specification with specific measurements and rationale.

---

User's instruction with the prompts (2026-10-16): "use the above prompts, lets organize them and then utilize them step by step (divide into like 6 steps) that an agent can digest".
