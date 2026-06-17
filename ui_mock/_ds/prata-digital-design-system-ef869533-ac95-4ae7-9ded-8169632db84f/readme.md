# Prata Digital — Design System

A design system for **Prata Digital** (also branded **Prata Tech** / **Banco Prata**), a
Brazilian fintech that provides consigned-credit and rewards products through a network of
correspondents and promoters (*correspondentes bancários / promotoras*).

This system was reverse-engineered from the production **Admin** front-end — the internal
platform used by promoters, typists (*digitadores*) and managers to simulate, originate and
track credit proposals. It captures the real tokens, components and screens of that product.

> **Everything in this system is in Brazilian Portuguese (pt-BR).** Copy, labels and sample
> data follow Brazilian conventions (R$, CPF, FGTS, Pix, etc.).

---

## What Prata Digital does

Prata is a credit-and-rewards platform. Its admin surface manages several credit products:

- **FGTS** — *Saque-Aniversário FGTS* anticipation (consult balance, originate proposals).
- **CLT / Crédito do Trabalhador** — payroll-deductible worker credit (margin consult,
  proposals, auction/*leilão*, registered companies, contracts).
- **Cartão de Crédito / CGV** — credit-card simulation and proposals.
- **INSS** — consigned credit for retirees/pensioners.
- **Livelo points & “Prata coins” (P$)** — a rewards layer; promoters accrue points
  (P$) that can be transferred to Livelo. Co-branded *Prata + Livelo* marketing.
- **Gestão** — management: promoters (*promotoras*), users, Corban pre-registration,
  goals, commissions, do-not-disturb lists.

Roles in the product: **super, admin, manager, coordinator, commercial, financial,
typist (digitador), promoter (corban)**. The platform is **white-label** — primary,
secondary and light colours plus logo are themed per tenant via a backend theme API
(default tenant values are encoded in the tokens here).

## Sources

These were the inputs used to build this system. The reader is not assumed to have access;
they are recorded here for provenance.

- **Codebase:** `admin/` — the Prata Digital Admin SPA.
  - Stack: **Vue 2.6** + **Bulma 0.9** (SCSS) + **FontAwesome 5** + a custom Vue SVG icon set.
  - Key style sources read:
    - `src/assets/scss/src/_statementVariables.scss` — the real colour palette (Prata green,
      stone, gray, semantic ramps, tag/notification colour pairs, `.is-card`, `.tag`).
    - `src/assets/scss/src/_buttonsVariables.scss` — button variants.
    - `src/assets/scss/src/_textVariables.scss` — type scale & weights.
    - `src/assets/scss/src/_utils.scss` — 4px spacing grid.
    - `src/data/theme.json` — default tenant theme (primary `#00e4b4`,
      secondary `#1a1e39`, light `#f8f8f8`).
  - Components read: `src/components/Base/*` (Button, Input, Select, Switch, Checkbox,
    Tabs, Table, Card), `src/components/TheNavigation.vue`, `src/views/Login.vue`,
    `src/views/Dashboard.vue`, `LiveloPoints.vue`.
  - Icons: `src/assets/icons/*.vue` (single-path Vue SVG components) → extracted to
    `assets/icons/*.svg`. Mascot `RocketGirl.vue` → `assets/illustrations/rocket-girl.svg`.
  - Logos: `public/img/logo-prata.png`, `logo-mobile-prata.png`, `logo-simulator.png`.

---

## Content fundamentals

How Prata writes copy in-product.

- **Language:** Brazilian Portuguese, always. Never mix English UI strings.
- **Address the user with “você”**, second person, polite but direct. Examples from the
  product: *“Para acessar, entre com os seus dados.”*, *“Fazer login”*.
- **Casing:** Sentence case for body and helper text. Buttons and primary actions use an
  imperative verb in sentence case (*“Fazer login”, “Simular crédito”, “Exportar”,
  “Recarregar”*). Section/menu labels are short nouns (*“Consultas”, “Propostas”,
  “Promotoras”, “Dashboard”*).
- **Tone:** operational and trustworthy — this is a money tool. Confident, concise,
  zero hype. Helper tooltips explain *how* (numbered steps), e.g. *“Siga os seguintes
  passos para alterar o filtro dos gráficos: 1. Selecione o período 2. Clique no botão de
  recarregar.”*
- **Domain vocabulary (keep verbatim):** *proposta, simulação, saldo, margem, parcela,
  promotora, corban, digitador, repasse, comissão, antecipação, Saque-Aniversário,
  averbação*. Product names stay as-is: **FGTS, CLT, INSS, CGV, Pix, Livelo**.
- **Money & numbers:** Brazilian format — `R$ 1.234,56` (dot thousands, comma decimal).
  Points shown as `1.234P$`. Documents: CPF `000.000.000-00`, CNPJ `00.000.000/0000-00`.
- **Emoji:** not used in-product. Don’t introduce them.
- **Status language:** proposals move through named states surfaced as coloured tags
  (e.g. *Aprovada, Pendente, Em análise, Reprovada, Pago*). Use the tag colour system,
  not raw colour words.

---

## Visual foundations

The product’s look: **clean, dense, operational fintech** — white cards on a near-white
canvas, warm-neutral text, with a single electric **Prata mint** as the brand spark and a
deep **navy** as the anchor.

- **Colour vibe.** Mostly neutral (stone/gray) with restrained colour. The brand mint
  (`#00E4B4`) and its darker action green (`#00A482`) carry primary actions, active states
  and positive figures. Navy (`#1A1C32`/`#1A1E39`) is the ink/anchor — sidebar background,
  wordmark, headings. Indigo (`#5666D5`) is the link/info accent. Semantic colour appears
  almost exclusively inside **tags, notifications and figures** — never as large fills.
- **Type.** **Inter** is the workhorse for all UI and body (14px/20px base). **Unbounded**
  is the geometric display face for big brand moments and headings. **Montserrat** is a
  legacy fallback still present on older surfaces. Weight ladder: 400 regular, 500 medium
  (buttons/labels/tabs), 600–700 for emphasis and active tabs, 800 for hero stats.
- **Spacing.** Strict **4px grid** (`--space-1…16`). Card padding is `24px 16px`. Comfortable
  but information-dense; tables and forms are tight.
- **Corner radii.** Soft, not pill-y: inputs & buttons **6px**, cards **8px**, stat cards /
  tags / pills **10px**, login panel & big modals **16px**. Nav items **4px**.
- **Cards.** White background, `1px` `--stone-100` border, `8px` radius, very soft shadow
  (`0 1px 2px rgba(0,0,0,.05)`). Stat cards round to 10px and lift on hover (mint text +
  larger shadow). No heavy borders, no coloured left-accent stripes.
- **Shadows.** Minimal and low-contrast. Default chrome is borders, not shadows; shadows
  appear on hover, dropdowns and modals only. No glow, no neumorphism.
- **Backgrounds.** Flat. App canvas is `#F8F8F8`/white. **No gradients** in the product UI
  (the only gradient-ish moments are partner marketing imagery). No textures/patterns.
- **Borders.** `1px` solid is the default separator. Input borders `#D1D5DB`; card/table
  borders `--stone-100`/`--stone-200`.
- **Tags / pills.** Pastel background + dark text pairs (success green, info blue, warning
  amber, danger red, hold violet, neutral slate). `10px` radius, `12px/500`, `2px 10px`
  padding. This is the single most recognisable Prata UI atom.
- **Buttons.** `12px 16px`, `6px` radius, Inter `14/500`. Primary = action green
  (`is-success`); outlined = stone border + stone-700 text; ghost = stone-500 text;
  link = underlined indigo. Hover darkens; the login CTA overlays a 10% black scrim on hover.
- **Animation.** Subtle and functional. Tabs slide horizontally (≈350ms,
  `cubic-bezier(.4,0,.2,1)`); cards expand/lift on hover (~350ms ease); transitions fade +
  small translate. No bounces, no infinite loops. Easing tokens: `--ease-standard`
  and `--ease-sine`.
- **Hover / press.** Hover = darken (or a translucent black scrim) and/or a soft shadow
  lift; active nav uses navy fill + light text. Press states rely on colour change, not
  scale (except tags with actions, which scale to 1.1).
- **Transparency / blur.** Used sparingly — translucent overlays behind modals; RGBA tints
  of the primary (`--color-primary-rgb`) for selected rows / highlights. No glassmorphism.
- **Imagery.** Partner/marketing PNGs (Pix, Livelo co-brand, transfers) are flat,
  full-colour product illustrations. The brand mascot is **RocketGirl** — a flat-style
  illustration of a woman with a rocket, in mint + navy + skin tones.

---

## Iconography

- **Two icon systems coexist** in the product:
  1. A **custom in-house SVG icon set** — single-purpose Vue SVG components, mostly
     **single-path**, 14–20px, drawn on small viewboxes. Some are **filled with
     `currentColor`** (recolour by setting `color`/`fill`), others ship **hard-coded brand
     colours** (e.g. green `#00A482`, indigo `#4754BB`, gray `#6B7280`, coin gold `#EAB308`).
     These are extracted into **`assets/icons/*.svg`** (45 icons) — copy them into artifacts;
     do **not** redraw them.
  2. **FontAwesome 5 (Solid + Brands)** — used inline via `fas fa-*` classes throughout
     (`fa-eye`, `fa-redo`, `fa-home`, `fa-gavel`, `fa-user-lock`, `fa-ban`, `fa-check-circle`…).
     For HTML artifacts, load FontAwesome 5 from CDN to reproduce these.
- **Style:** geometric, rounded joins, medium weight, no outline/duotone mixing within a
  view. Sizes cluster at 14/16/20px. In buttons, icons sit beside the label with `~3–8px`
  gap and inherit/stroke the text colour.
- **Emoji / unicode icons:** not used as iconography. Avoid.
- **Brand glyphs:** `PrataCoin.svg` (the P$ coin, gold `#EAB308`) is the rewards mark;
  `rocket-girl.svg` is the mascot illustration.
- **Substitutions:** none needed for the custom set (extracted directly). If you need an icon
  the set doesn’t have, match FontAwesome 5 Solid (the product’s second system) before
  reaching for any other library, and flag it.

---

## Index — what’s in this system

**Root**
- `styles.css` — global entry point (import this one file). `@import`s only.
- `readme.md` — this guide.
- `SKILL.md` — Agent-Skills wrapper so the system can be used in Claude Code.

**`tokens/`** — CSS custom properties, imported by `styles.css`
- `fonts.css` — Inter / Montserrat / Unbounded webfonts (Google Fonts).
- `colors.css` — full palette + semantic aliases (`--text-*`, `--surface-*`, `--border-*`,
  feedback pairs, theme `--color-primary/secondary/light`).
- `typography.css` — families, weights, size/line-height scale.
- `spacing.css` — 4px spacing, radii, shadows, layout chrome, motion.
- `base.css` — element resets + body/heading defaults.

**`assets/`**
- `logo-prata.png`, `logo-mobile-prata.png`, `logo-simulator.png`, `prata-logo.png`,
  `favicon.png` — brand logos (mint **PD** monogram + navy wordmark).
- `icons/*.svg` — 45 extracted product icons.
- `illustrations/rocket-girl.svg` — brand mascot.
- `image/*` — partner/marketing imagery (Pix, Prata+Livelo, transfers).

**`guidelines/`** — foundation specimen cards (Design System tab): colours, type, spacing,
radii, shadows, tags, iconography, logo.

**`components/`** — reusable React primitives (see each `.prompt.md`):
Button, Input, Select, Checkbox, Switch, Tag, StatusTag, Card, StatCard, Tabs, Table, Modal.

**`ui_kits/admin/`** — high-fidelity click-through recreation of the Admin product
(Login → Dashboard → Proposals → FGTS consult → Livelo rewards).

---

## Caveats

- **Webfonts** are loaded from **Google Fonts** (matching the codebase’s `@import`), not
  self-hosted `@font-face` binaries — so the compiler reports “Fonts: (none)”. If you want
  the fonts shipped as binaries to consumers, provide the `.woff2` files and they’ll be
  wired into `tokens/fonts.css`.
- The product is **white-label**; the encoded theme is the default Prata tenant. Override
  `--color-primary`, `--color-secondary`, `--color-light` for other tenants.
