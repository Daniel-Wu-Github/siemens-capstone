# Frontend design system

Status: accepted by the user 2026-10-29. Read before ANY UI edit. Authority: user instructions, then approved mocks (`docs/mock/design/`), then this file, then older docs.

## 1. Stack
Single `index.html` with embedded CSS, JavaScript and SVG. No framework, no remote fonts, icons or scripts. Charts are hand-written SVG. The mock's `tokens.css` and `app.css` get inlined into `index.html` when built.

## 2. Theme: plain operations console
Light, flat, dense. Content is tables, one status strip per page and one detail column. Structure comes from 1 px rules, not boxes with shadows. Colour is used only for the accent and for alert tiers, and every tier colour also carries a text label.

## 3. Tokens (all component CSS uses only these)
| Group | Tokens |
| --- | --- |
| Space | 4, 8, 12, 16, 24, 32, 48 px (`--s1`..`--s7`) |
| Type | system font stack; 12 / 13 / 14 / 16 / 20 / 28 px; line height 1.3 for data, 1.45 for text; tabular numbers on every table and number |
| Neutrals | bg `#f4f5f6`, surface `#fff`, sunken `#eceef0`, ink `#16202a`, ink-2 `#3b4752`, muted `#5b6773`, line `#d5d9de`, line-strong `#9aa4ae` |
| Accent | petrol `#00757a` (existing app colour), wash `#e4f1f1` |
| Tiers | Alert `#b3261e`, Predicted `#9a5200`, Watch `#2b5c8a`, OK `#2e6b3a`, each with a pale wash |
| Shape | radius 2 px everywhere, no shadows except one overlay level for dialogs and drawers |
| Motion | 120 ms colour change only; none otherwise |
| Focus | 2 px accent ring with a white gap |

Contrast: muted text on white is 5.9:1, accent on white 5.3:1, tier colours on their washes at least 4.5:1 (to be confirmed by the audit in section 6).

## 4. Layout
- Sidebar 208 px: three primary pages (Stock alerts, Predicted alerts, Replenishment insights), then Setup (Data sources, Settings), and nothing else: legacy pages were removed from the app (code in `legacy_for_deletion/`). Setup is Data sources (imports, reference files, quality) and Settings (forecast settings). Active page has a 3 px accent bar.
- Page header: title (28 px), one line of context and data-as-of time, actions on the right, a strong rule under it.
- Status strip: one bordered row of four counts. No icons, no gradients, no per-tile shadows.
- Body: dense table (36 px rows) at flexible width plus a 360 px detail column. Under 1100 px the detail column stacks below. No phone layout is provided (not required).

## 5. Graphics (functional only)
1. **Hours-to-minimum bar** (table cell): 0 to 72 h, filled by tier colour, black tick at lead + safety.
2. **Projection line** (detail panel): stock falling to the minimum line, shaded lead + safety window.
3. **Refill dots** (table) and **refill bars** (detail): height of each refill as a share of Max, dashed line at Max.
No donuts, no gauges, no decorative shapes or icons.

## 6. Component inventory
Nav item, Button (default, primary), Status tag, Status strip, Table (sticky header, right-aligned tabular numbers, selected row), Toolbar (search plus select), Panel, Key-value list, Step list (formula steps), Hours bar, Projection chart, Refill chart, Notice banner, Note text. A new component needs a row here first.

## 7. Rules
Tokens only, no raw hex or px in component CSS. Every number goes through one formatter. Names never truncate mid-word. Targets at least 24 px, contrast 4.5:1. Never convey a tier by colour alone. Anti-patterns to avoid here: identical shadowed cards, big number plus tiny label tiles, all-caps eyebrows, decorative icons, motion on hover.

## 8. Workflow
Plan, build on tokens, render at 1440x900 and 1280x800 with real part names, measure clipping, overlap and contrast, fix (max 3 loops), fresh-reviewer critique, then lock screenshots. Mock sources: `docs/mock/design/`.
