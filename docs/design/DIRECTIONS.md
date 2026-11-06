# Finance Agent: design directions

Status: research. Nothing here is implemented. Mocks are static HTML with synthetic data.

Mocks, shared data and palettes live in the scratchpad (not in the repo):
`/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/`
(`pocket.html`, `night.html`, `statement.html`, `data.js`, `palettes.js`, `common.js`, `validate.mjs`).

## 1. What we take from the references

| Source | Keep | Leave |
| --- | --- | --- |
| Revolut (main) | True black and white canvases. Pill buttons. 20px soft cards. A white pill as the primary on black. Big display type only on marketing surfaces. Saturated product colours kept inside the product, never on buttons. Ink `#191c1f` warmer than black. | The name and logo. Cobalt-violet `#494fdf` and anything in the violet or indigo band (hue 235-290). Aeonik Pro. Metal-card imagery. Its 136px hero lockups copied as they are. |
| Mercury | Calm product pages at light weights. Soft secondary pills. | The indigo action colour and the night-violet ground. |
| Wise | Heavy display figures, a tinted neutral ground, rounded white cards. | The lime brand accent (it reads as Wise). |
| Coinbase | Display type at regular weight, depth from stacked cards, no decorative shadows. | Coinbase Blue `#0052ff`. |

**Not impersonating Revolut.** No wordmark lookalike and no "R". Our mark is three bars of decreasing length (money being sorted). No accent within ΔE 25 of `#494fdf`. Fonts are all OFL. The demo copy never names a bank. Synthetic accounts are generic ("Everyday current", "Easy saver").

## 2. Shared for all three

- **Screen question (Overview):** "Where did this month's money go, and does everything add up?" Finding line: "You kept £936.90. Eating out ran £41.30 above September." Main job, one click away: review the account that does not reconcile.
- **Data:** in pence, totals computed from rows, with `console.assert` checks (in £3,412.50, out £2,475.60, net +£936.90, net worth £11,779.79, one gap of £12.40 on the credit card). See `data.js`.
- **Categories (10 + Other):** Housing, Groceries, Bills and utilities, Eating out, Shopping, Transport, Travel, Health and fitness, Entertainment, Subscriptions; Other is always neutral grey. A category keeps one hue on every screen.
- **Money:** GBP via `Intl.NumberFormat('en-GB')`, a true minus `−`, a `+` on income, tabular figures, pence a step smaller (Pocket) or grey (Night).
- **Polarity:** income is green with `+`. Spending is in ink, never red. Red or amber only marks something that needs action (a reconciliation gap, a category over its usual).
- **Privacy mode:** `H` (no modifier, ignored while typing in a field) toggles `data-private` on `<html>`. Every `.money` gets `filter: blur(7-8px)`. The sidebar has a "Hide amounts" button with `aria-pressed` that does the same. Persist the choice in `localStorage`. Chart tooltips and `aria-label`s must read "hidden" while it is on (to do in the app: the mocks still expose amounts to screen readers). Screenshot: `shots/pocket-private.png`.
- **Stack mapping:** each token below maps to a shadcn CSS variable (`--background` = ground, `--card` = surface, `--foreground` = text, `--muted-foreground` = muted, `--primary`, `--border`, `--ring`). Extend shadcn's `--chart-1..5` to `--cat-<id>` for the 11 categories so Recharts `<Cell fill="var(--cat-groceries)">` works in both themes. Dark mode uses the `class` strategy and `prefers-color-scheme` as the default. Self-host fonts with `@fontsource-variable/*`.
- **Motion budget, app:** 120-220ms, `ease-out` (`[0.2, 0.8, 0.2, 1]`), exits about 70% of the entry time. No entrance choreography on cards. Motion only answers an action: a popover opens from its chip, a recategorised row's chip changes colour, the donut segments re-flow (`motion/react` `layout` and `AnimatePresence`), a number cross-fades when the month changes. Under `useReducedMotion()`: no transforms, opacity changes of 100ms or less, the blur toggle snaps.
- **Motion, landing only:** large display type and scroll-driven storytelling. All of it is skipped under reduced motion, where each act renders in its final state as an ordinary section.
- **Ambient background (M7, landing and demo only):** a slow-drifting field of large blurred colour blobs on a 40-60s loop, low opacity, animating only transform and opacity, paused when the tab is hidden, off under reduced motion. App screens keep the static glow and grain from #15 (no motion; glow hue outside 235-290).
- **Glass (app chrome, #15):** a frosted look (translucent fill, `backdrop-filter: blur(20px) saturate(1.6)`, 1px light edge with an inner top highlight) on chrome only: the sticky header pill, action pills, the month switcher track and the phone bottom tab bar (`.glass` in `index.css`). Content cards (figures, charts, transaction lists) stay solid for legibility. `prefers-reduced-transparency: reduce` falls back to the solid card colour. Text on glass keeps WCAG AA in both themes, checked over the brightest part of the glow. Glow and glass tints stay outside hue 235-290 (accent blue about 210 and teal about 172 only). No 3D charts; static, no motion beyond the app budget.
- **Landing story (scroll-craft beats):** 1 Recognition (a month of raw statement lines) → 2 Turn (lines get sorted into categories) → 3 Substance (categories become the donut, the totals and the gap check) → 4 Commitment ("Open demo" → `/app`). The numbers on the landing page come from the same `data.js` and are labelled sample data. No invented user counts.
- **Charts:** the composition donut is the one multi-hue chart. Every other chart (monthly trend, merchant ranking) is one series in the direction's data hue, with the current month marked.
- **Known rule conflict:** the taste inspector fails every mock on `rainbow-chart` (8-9 hues visible) because the brief asks for a ~10-category donut. This is a deliberate override. Mitigations: a thin ring, 2-3px seams, largest category first, a legend table beside it with the value and share (colour is never the only cue), and a tooltip or focus label per segment. If that reads as noisy in the real app, a fallback is to colour the top 5 and pool the rest in grey "Other categories". Expect only pairwise ΔE 2-4 under simulated deuteranopia; 10 hues cannot all be told apart by colour alone, which is why the labels carry the meaning.

All palettes were checked by `validate.mjs`: every category mark is at least 3:1 on its card (WCAG 1.4.11). Text tokens were chosen at 4.5:1 or better.

---

## 3. Direction A: Pocket

**Idea.** The closest to the Revolut feel: soft white cards on a cool off-white ground, large radii, pill controls and saturated product colour. The categories are the face. Each category is a pale tile of its own hue with the dark of that hue as ink. The same chip appears in the legend, on every transaction row and on the drill-down header, so colour turns into identity rather than decoration. Net worth and the month's net are set big in Manrope 600, with the pence small and grey.

**Twist vs Revolut.** Revolut puts colour in round icons and keeps it inside marketing mockups. Pocket makes the category chip the main unit of the interface: it is the label, the filter and the inline editor. Cobalt is a true blue (`#0b66c3`, hue about 211, ΔE 36 from `#494fdf`) and appears only on links and the brand mark.

**Font.** Manrope (OFL), weights 400/500/600/700. Its figures are geometric and close in spirit to Aeonik without copying it.

| Role | Size / weight |
| --- | --- |
| Page title (h1) | 28 / 600, -0.01em |
| Net worth (answer figure) | 34 / 600, -0.02em |
| Month figures | 26 / 600 (net 32) |
| Card title | 18 / 600 |
| Body, rows | 16 / 400-500 |
| Labels, meta | 14 / 400 |
| Chips | 13 / 600 |
| Landing display | clamp(56px, 9vw, 132px) / 600, -0.035em, line height 0.95 |

**Tokens**

| Token | Light | Dark |
| --- | --- | --- |
| ground | `#f3f5f7` | `#0d0e10` |
| surface (card) | `#ffffff` | `#16181b` |
| raised | `#f2f6fb` | `#1e2124` |
| active (nav, segmented) | `#e7eaee` | `#23262a` |
| rule | `#f0f2f4` | `#1f2225` |
| control border | `#d6d9dd` | `#3a3e43` |
| text | `#1b1d1f` | `#f4f5f6` |
| muted | `#5c6064` | `#a9aeb4` |
| accent (links, mark) | `#0b66c3` | `#7da2f7` |
| primary button | `#1b1d1f` on white text | `#f4f5f6` with `#111214` text |
| positive | `#2f7a45` | `#5ccf86` |
| warning bg / ink | `#fff4e0` / `#8a4b00` | `#3a2a12` / `#f5c27a` |
| chip tint / ink | category 14% on surface / category mixed 42% with black | category 20% on surface / category mixed 35% with white |

**Categorical palette** (in category order; Other `#8a8f94` / `#7d838a`)
- Light: `#0b66c3 #0e7a82 #b45309 #c03a5e #2f7a45 #8a6a00 #a03c96 #0a6fa8 #7a5636 #5f6b0f`. Minimum 5.0:1 on white.
- Dark: `#7da2f7 #3fc1c4 #f0a04b #f27b9b #5ccf86 #dcb83c #d886d0 #4fb4ec #cfa57c #b5c94a`. Minimum 6.9:1 on `#16181b`.

**Radii and spacing.** Cards 20px, inner blocks 10px, chips and buttons 999px. 4px base. Gap and card padding 24px, canvas gutter 36px, sidebar 240px, row pitch 28px (legend) and about 60px (two-line transaction rows).

**Motion.** App: the chip is the editor. Click a chip, a popover of the 11 chips scales in from it (160ms). Pick one and the row's chip cross-fades to the new hue, while the donut segment and the legend row re-flow with `layout` (200ms). Landing (live-surface grammar, real markup computing from `data.js`): a pinned stage, with raw statement lines on the left. As you scroll, each line's chip lights up and the line drops into its category tile on the right (the sort). The tiles then fold into the donut and the three month figures count up from the real totals. The close is an actual input: recategorise one sample transaction and watch the donut change, then "Open demo".

**Signature move.** The sort: a transaction becoming its category chip, shown once on the landing page and repeated in miniature every time you recategorise in the app.

**Mock and screenshots**
- `/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/shots/pocket-light.png`
- `/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/shots/pocket-dark.png`
- `/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/shots/pocket-private.png` (privacy mode)
- Inspector, app rules: 1 FAIL (rainbow-chart, the override above) and 2 WARN. The cards in the lower row end at different heights, and the "Where money came from" card has a 79px blank band. Both are fixable once the drill-down links and an "Upcoming" block exist.
- Fixed in the look-and-fix round: the layout spilled past 900px (a ring gap and 8 rows), there was an orphaned "Oct", pence were set at 10px, and the month card sat below the accounts on the phone.

---

## 4. Direction B: Night Ledger

**Idea.** Revolut's black storytelling canvas turned into the product itself. Dark is the default and is designed first. Elevation comes from lightness alone (`#000` ground, `#111` card), with no card borders. The active nav item is shown only by brighter text. The primary action is a white pill. Every figure is set in Geist Mono, so the screen reads as a ledger. The accounts card makes reconciliation the hero: each account says "Matches ledger" with a tick, or shows an amber block with the missing amount and a "Find it" action.

**Twist vs Revolut.** Revolut uses black for marketing and white for the app. Here the app lives on black and money is shown as ledger columns. Trust is earned visibly: the screen tells you which balances it has checked against the bank.

**Font.** Geist (text) + Geist Mono (all figures, a style decision; the taste linter warns on mono and this is the defended exception). Both OFL.

| Role | Size / weight |
| --- | --- |
| Page title | 24 / 600 |
| "Kept this month" (answer figure) | 34 / 500 mono |
| Month figures | 28 / 500 mono |
| Net worth | 30 / 500 mono |
| Card title | 15 / 500 |
| Body, rows | 14 / 400 |
| Labels, deltas | 12 |
| Landing display | clamp(56px, 10vw, 148px) Geist 500, -0.04em, line height 0.92 |

**Tokens**

| Token | Light | Dark (primary) |
| --- | --- | --- |
| ground | `#f4f4f5` | `#000000` |
| surface | `#ffffff` | `#111111` |
| raised | `#f0f0f1` | `#1c1c1c` |
| rule | `#ececee` | `#1f1f1f` |
| control | `#d4d4d8` | `#3d3d3d` |
| text | `#0a0a0a` | `#ffffff` |
| muted / faint | `#5d5d63` / `#6b6b70` | `#b4b4b4` / `#949494` |
| primary pill | `#0a0a0a`, white text | `#ffffff`, black text |
| positive | `#1f7a4d` | `#3fd09a` |
| gap ink / bg | `#9a4a00` / `#fff3e3` | `#ffb35c` / `#2a1d0c` |

**Categorical palette** (Other `#8a8f94` / `#6f747a`)
- Light: `#0b6e66 #2d5fb8 #b2471c #93407f #4f7a14 #9a6200 #0e6f99 #b8324c #7b5a3c #5e6a73`. Minimum 5.1:1.
- Dark: `#2fd6bd #6fa2ff #ff8a5c #e98ad3 #a4d65e #f5c04a #4cc6f2 #ff7088 #d2a77e #a8b4bd`. Minimum 7.1:1 on `#111`.

**Radii and spacing.** Cards 16px, pills 999px, gap blocks 10px. 12-column canvas, 16px gap, 40px gutters, sidebar 232px. Table rows 34px.

**Motion.** App: almost still. Month tabs cross-fade the figures (mono digits hold their width, so nothing jumps). Resolving a gap turns the amber block into the grey "Matches ledger" tick line (height and opacity, 200ms). Landing (typographic-poster grammar on true black): one huge line at about 10vw, "Every pound, accounted for.", then a pinned ledger whose rows type in, settle into category columns and finally tick off against the bank, one account at a time. The white "Open demo" pill is the brightest pixel on the last screen.

**Signature move.** The tick-off: balances are checked against the bank in view, and the one that does not match stays amber until you find it.

**Mock and screenshots**
- `/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/shots/night-dark.png`
- `/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/shots/night-light.png`
- Inspector: 1 FAIL (rainbow-chart, the override) and 2 WARN. The tabular warning is a false alarm because mono digits are already fixed width. There is a 44px band under the donut.
- Fixed in the look-and-fix round: a hatched gap bar at its true 3% ratio read as a rendering glitch and was removed. Deltas lacked the £ sign.
- Risk: the long mono figures can feel technical rather than "nice". The light mode is the weaker of the two.

---

## 5. Direction C: Statement

**Idea.** The bank statement, redrawn. Achromatic chrome (ink, greys, 1px borders, tight 8/6 radii) on a statement-paper blue-grey ground. The month card carries the pound strip: money in is the full width of one bar, each category takes its share in order, and the hatched end is what you kept. "Of every £1 that came in, 73p went out and 27p stayed." Accounts are a real reconciliation table with Bank and Ledger gap columns, so £0.00 is grey and −£12.40 is red.

**Twist vs Revolut.** Revolut is a glossy product brochure. Statement is the paper version: no tints and no pills, and the only colour is on the money itself. It is the most readable and the least Revolut-like, which makes it the safest for a public demo.

**Font.** Schibsted Grotesk (OFL), a grotesk with newspaper roots, weights 400/500/700. Its tabular figures are wide, so headline figures use proportional numerals and tables keep tabular ones.

| Role | Size / weight |
| --- | --- |
| Page title | 24 / 700 |
| Kept (answer figure) | 30 / 700 |
| In / out | 22 / 500 |
| Card title | 16 / 500 |
| Body, cells | 14 / 400 |
| Headers, meta | 12 |
| Landing display | clamp(52px, 8vw, 120px) / 700, -0.03em |

**Tokens**

| Token | Light | Dark |
| --- | --- | --- |
| ground | `#f2f4f7` | `#0e1013` |
| surface | `#ffffff` | `#15171a` |
| raised | `#eef0f3` | `#1c1f23` |
| border | `#e3e6ea` | `#24282d` |
| rule | `#eef0f2` | `#1f2327` |
| control | `#cfd3d9` | `#3b4047` |
| text | `#0d1117` | `#f1f3f5` |
| muted | `#5a616b` | `#a3aab3` |
| primary | `#0d1117`, white text | `#f1f3f5`, `#0e1013` text |
| data hue (single-series charts, kept hatch) | `#1d4f91` | `#7fa8e6` |
| positive / negative | `#2e7d32` / `#c4242a` | `#6cc58a` / `#ef6b6b` |

**Categorical palette** (print inks; Other `#8f939a` / `#767b82`)
- Light: `#1d4f91 #8c3b2e #2f6b4f #7a5a14 #6a3d75 #1f6f7a #b25a00 #4b5f17 #8a3a5c #3d5a80`. Minimum 4.8:1.
- Dark: `#7fa8e6 #e08c7a #77c19b #d2b268 #c49ad0 #6cc3cf #ffa04f #a9c070 #e48fb2 #97b2d6`. Minimum 7.0:1.

**Radii and spacing.** Cards 8px, controls 6px, the strip 4px at its ends. 12-column canvas, 12px gap, 32px gutters, sidebar 248px with a 1px rule. Table rows 28px (categories) and 44px (accounts).

**Motion.** App: the strip re-divides with `layout` when you change month or category (220ms). Otherwise the screen is still. Landing (split-stage grammar): the left half is the raw statement and the right half is the picture. Scrolling moves lines across the divider into the pound strip, segment by segment. At the end the divider travels to the left edge, the strip becomes the full Overview, and "Open demo" sits in that column.

**Signature move.** The pound strip: one bar that answers "where did my income go?" in a single read. It works for the landing story and as the Overview's lead.

**Mock and screenshots**
- `/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/shots/statement-light.png`
- `/private/tmp/claude-501/-Users-eshansharif-Documents-projects-finance-dashboard/d2153b50-9bd2-47c3-9585-fef0b42bf410/scratchpad/design/shots/statement-dark.png`
- Inspector: 1 FAIL (rainbow-chart, the override) and 0 WARN.
- Fixed in the look-and-fix round: the "Find it" button wrapped, headline figures were too wide, and inline share bars repeated the donut a third time.

---

## 6. Other screens, by direction

| Screen | Pocket | Night Ledger | Statement |
| --- | --- | --- | --- |
| Category drill-down | Header is the category chip at large size with month spend. Monthly trend as 12 square columns in the category's own hue (current month full, prior months at 35%). Top merchants as a ranked list where the row is the bar. Transactions below. | Mono figures. Trend as a 2px line in the category hue with the 6-month average as a dashed reference. Merchants as a table with count, total and change against last month. | Trend as columns in ink with the current month in the data hue. Merchants as a table with the share of category spend. The pound strip filtered to this category's share of income. |
| Transactions + inline category change | 60px two-line rows (merchant over chip, amount over date). The chip is a `<button>` that opens a shadcn `Popover` + `Command` list of the 11 chips, searchable and keyboard-first. Undo toast for 5s. | 40px single-line table rows. Pressing `C` on a focused row opens the category picker. Uncategorised rows show an amber dot. | A dense table (date, merchant, category select, amount). Category is a native-looking `Select` styled as text until hover or focus. A bulk "Set category" for selected rows. |
| Landing `/` | Live surface: the sort | Typographic poster: the tick-off | Split stage: the strip |

All three: filters as one row of chips, "View all" instead of pagination on the Overview, honest zero and empty states (`£0.00` in grey, "No transactions in this category in October"), and per-block loading bars at the real size.

---

## 7. Recommendation

**Build Pocket (A) as the system, and borrow two moves from the others:**

1. **Statement's pound strip (C)** as the lead of Pocket's month card, under the in/out/net figures, drawn in Pocket's chip hues with the kept part hatched in cobalt. It is the clearest single picture of "money comes in, gets sorted, becomes insight". It is also the natural bridge from the landing story: the landing page's final frame is this strip, and "Open demo" lands on the same strip in `/app`.
2. **Night Ledger's tick-off language (B)** for reconciliation: "Matches ledger" with a grey tick on every reconciled account, amber only on the one that needs review.

Why Pocket: it is the one the user will read as "a lot like Revolut" (soft 20px cards, pills, saturated product colour, big confident figures), but its identity comes from something Revolut does not do: the category chip as the unit of the interface. It is clearly not Revolut, with no violet, no Aeonik and its own mark. Its dark mode came out as good as its light mode in the screenshots. For the landing page, use Night Ledger's true-black display treatment for the first act only (one huge Manrope line on `#000`, the Revolut storytelling cue). The sort and the strip then carry the scroll on Pocket's light ground. The colour change is a composition decision made at a section edge, not a fade.

Before building, open questions for the user: Manrope or Geist (the sheet dealt Manrope, and the brief suggested Geist), and whether a 10-hue donut is acceptable or the top-5-plus-grey fallback is preferred.

## Decision (agreed)

- Base: **Pocket**, plus Statement's **pound strip** as the month card headline (it also closes the landing story), plus Night Ledger's "Matches ledger / Find it" reconciliation language. Dark mode uses Night Ledger's true black.
- Font: **Manrope**, tabular figures.
- Donut: colour the **top 5 Categories**, grey the rest; the table lists every Category.
- Landing page: one true-black screen of large type, then the light sort-and-strip scroll story ending in "Open demo".
