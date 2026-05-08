# Ticket: Grades Ticket Evaluation Flicker/Disappear

## Title
Grades: Ticket Evaluation flashes then disappears on load

## Reported URL
`https://web-production-f280f.up.railway.app/grades`

## Summary
On the Grades page, clicking **Ticket Evaluation** can briefly show graded ticket content and then hide/disappear. Behavior is intermittent but reproducible during iframe load + regrouping.

## Reproduction
1. Open `/grades`.
2. Click **Ticket Evaluation**.
3. Observe content flashes, then can disappear or fail to remain interactable.

## Expected
Ticket evaluation remains visible and stable after tab switch, and grouped ticket buckets expand/collapse reliably.

## Actual
Ticket content can render briefly and then vanish during post-load DOM transforms.

## Technical Context
- `switchTab` is defined in `ui_runner/templates/indexGrades.html` and exposed via `window.switchTab`.
- This does **not** appear to be a missing `switchTab` binding issue.
- Risk area is iframe post-load regrouping in `collapseSportsInTicketFrame(frame)`.

## Suspected Root Cause
During regrouping, insertion may be attempted relative to an anchor captured before node detach/reparenting. If that anchor is stale, regrouping can fail mid-flow and leave content missing.

## Fix Applied Locally
In `ui_runner/templates/indexGrades.html`:
- Updated `collapseSportsInTicketFrame(frame)` to rebuild groups into a `DocumentFragment`.
- Insert fragment after intro node only if intro is still attached.
- Otherwise append fragment directly to wrap.

This removes stale-anchor insertion risk and makes regrouping atomic.

## Acceptance Criteria
- Ticket tab no longer flashes/disappears.
- Bucket expand/collapse remains stable across repeated tab switches.
- No regressions in Slate and Props tabs.

## Related Notes
- Spacing fix (`.sport-section` margin reduction / `:last-of-type` handling) is separate and already validated live.
