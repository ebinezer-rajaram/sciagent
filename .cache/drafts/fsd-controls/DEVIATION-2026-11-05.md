# Protocol deviation, 2026-11-05: `ctlA1-cal-r0` drawn outside the gate

Any later use of ctlA1 rung 0 carries this note (INBOX 119eff3; LOG 2026-11-05, "Check (b): an unapproved real stream was drawn during testing").

- **Stream:** `ctlA1-cal-r0`, a real control stream, rung 0. It is outside the approval for check (b), which covers `ctlA1-cal-r2`, `ctlA1-crn-r2` and `ctlA1-boot-r2` only (INBOX b0e794a).
- **When:** 2026-11-05, 12:07Z to 12:11:11Z, 4:09 CPU, killed after a tool timeout.
- **How:** while testing its self-test, a lane helper ran a mutant of `.cache/drafts/fsd-seedlead/checkb/checkb.py` with the guard removed, and the self-test then called `calibration_d0("ctlA1")` for real. Seed objects for other real ids were built too, with no generator drawn on them.
- **How much:** 600 to 1,000 runs by the helper's estimate. The exact count is unknown.
- **The values:** never written and never seen. They stayed in memory and were lost at the kill; `m1.out` is 0 bytes. This is the helper's account, as nothing records the run.
- **What was checked:** no file in this directory is newer than the start of 2026-11-05. The before and after hashes of this directory (`fsd-seedlead/checkb/fsd-controls-before.txt`, `-after.txt`) were taken at 12:18Z and 12:59Z, **after the draw**, so they do not cover it.
- **Consequence:** no rerun on rung 0. D0, the test runs and the scores of 2026-10-29 are unchanged, and A1's scored failure stands. Helpers never remove or bypass the guard, even in a mutant or test (AUTOPILOT, Never).
