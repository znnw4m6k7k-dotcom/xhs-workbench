# Content workflow board design QA

final result: passed

## Visual target and evidence

- Selected source: `C:/Users/Administrator/Documents/ChatGPT/工作台/redesign/selected-reference.png` (the user's explicit attachment overrides the earlier numeric selection).
- Implementation: `http://127.0.0.1:17865/`, Work board, current account 女装主号, light theme.
- Source: 1487 × 1058 pixels. Desktop browser viewport: 1487 × 1058 CSS pixels, density 1. The browser reserves a 15px vertical scrollbar; its full-page capture is 1472 × 1112. `board-desktop-final.png` is an unscaled 1472 × 1058 top viewport crop. Comparison canvas leaves the scrollbar-width difference blank, with no scaling or distortion.
- Full comparison: `C:/Users/Administrator/Documents/ChatGPT/工作台/redesign/comparison-final.png`.
- Readable focused comparison of review card, type and account switch: `C:/Users/Administrator/Documents/ChatGPT/工作台/redesign/comparison-focus.png`.
- Responsive evidence: `board-narrow-final.png` (809 × 900 CSS viewport; two columns); `board-mobile-final.png` (390 × 844 CSS viewport; one column), in the same evidence directory.

## Comparison history

1. Initial desktop capture: title, sidebar, column headings and cards were smaller than the selected reference. P2: adjusted desktop sidebar to 247px, title to 36px, column headings to 18px, card title to 16px and card body to 14px, and aligned column surfaces and recent activity strip.
2. Initial narrow capture: legacy mobile rules changed the sidebar into a large top bar. P2: explicitly restored a vertical 74px / 58px icon navigation rail; condensed empty states; verified the revised two-column and one-column captures.
3. Recent activity initially exposed technical summaries such as drafts=1. P2: translated those fields into ordinary progress text and added real product thumbnails. Rechecked final full-view and focused evidence.
4. Final comparison: no actionable P0/P1/P2 findings remain within the selected board and responsive scope.

## Required fidelity surfaces

- Typography: system Chinese sans stack; readable 14px card copy and 16px title at desktop, strong page/column/card hierarchy. Fonts remain local and do not require a network font service.
- Layout: four columns at desktop, two below 1200px, one below 541px; sidebar and actions remain reachable. DOM checks found no horizontal page overflow at desktop, 809px or 390px.
- Colors: white application canvas, cool gray navigation and column surfaces, restrained red selection and actions; gray/blue/amber/green column markers match the reference hierarchy.
- Images: uses existing garment photography and generated images belonging to this product. No missing image loads in the board. Bootstrap Icons v1.11.3 are embedded as unmodified library assets, without CDN runtime dependency.
- Copy: exact four workflow headings and primary actions; actual garment and draft content, genuine account and task state. Search includes garment name, category, colors, draft title and body.

## Intentional content differences

- The reference is populated with illustrative garments and a model portrait. The current account has one real sweater draft. Its actual garment image is shown in review; other columns show empty states. No sample garments, accounts, avatars, publication times, image counts or progress percentages were inserted into live data.
- Existing selection/editing/generation/review/publishing pages remain functional destinations. Advanced authoring tools are available in a secondary navigation disclosure. This implementation changes the board and shared shell; it does not claim to reproduce every downstream editor as a new mock screen.
- Published drafts leave the board. Scheduled drafts stay in the last column with their actual scheduled time. Active jobs and failures retain actionable status links.

## Validation

- Browser: search non-match 0 cards, match 1 card; review-stage filter 1 visible column/1 card; clearing returns four columns; category and account controls use live data.
- Review CTA opens the existing draft editor for the selected draft. Back-to-board works.
- New product CTA opens the existing upload/details dialog; canceled without creating data.
- Library, calendar, history navigation verified against resulting headings.
- Error/warning console logs: none returned for the tested page session. Board images: zero failed loads.
- JavaScript syntax check passed.
- 11 isolated workflow classification checks passed: incomplete item, ready for images, draft, finalized draft, scheduled draft, published removal, active job, failure, cross-account exclusion, orphan draft, image-based association.
- No generation, finalization, scheduling or publishing was triggered during verification. No existing garment, draft or account data was changed.

## Remaining test limits / polish

- Only one real account is configured, so switching between multiple live accounts was not exercised in-browser. Cross-account task exclusion was checked with isolated fixtures.
- Full publishing and AI generation behavior was preserved and was not re-executed as part of this UI change.
- P3: the selected reference has more image-rich card types than the present account can display. Check those naturally as new garments enter the workflow.

## Checklist

- [x] Explicit image reference used
- [x] Existing real data and backend actions retained
- [x] Desktop and responsive evidence reviewed
- [x] Main controls tested
- [x] No actionable P0/P1/P2 design findings
- [x] Original HTML backup and reviewable patch saved in the evidence directory
