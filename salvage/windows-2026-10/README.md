# Windows PC salvage, 2026-10

Gitignored working files from `.cache/` on the Windows PC, kept so they are not
lost with it. Reference material only: nothing here is imported or run by the
package, and no numbers here are reported (dm-devbox is the reference platform).

- `hunt/A`, `hunt/B`: the defect-hunt checks, including `p3_gap.py`,
  `store_check.py` and `report_determinism.py` that QUEUE.md lists as lost.
  `hunt/B/fit4src` (a source copy at b5e2b87) and the hunt outputs are omitted.
- `minipilot-scripts`: the Windows mini-pilot supervisors and gate checks
  (HANDOFF-2026-10-09).
- `p4`: the P4 plan (`PLAN.md`), LOG drafts, early diffs and the policy cost
  estimate. The P4 code itself is on the `p4-deltas` branch.

The stores (`.cache/pilot`, `.cache/minipilot*`, hunt outputs, ~100 MB) are
not here.
