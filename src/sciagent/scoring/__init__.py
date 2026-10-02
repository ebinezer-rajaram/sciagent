"""The v2 scorer (SPEC §4.3, §4.4, §6.4, §7.1).

Framework-side only: nothing an agent can reach imports it, and every number
it writes is computed from data no system saw (CLAUDE.md invariant 2).

- ``heldout``: score 1, the held-out predictive gap and the gap closed.
- ``structure``: score 2, exact recovery and distance to the truth.
- ``battery``: score 3, interventional similarity on a fixed battery.
- ``efficiency``: score 4, best-so-far gap against fits used.
- ``behaviour``: the §4.4 behaviour measures, from plain records.
- ``gonogo``: the §7.1 pilot read and the §6.4 positive controls.
- ``stats``: paired standard errors and bootstrap CIs.
- ``seeds``: the scorer's random streams, disjoint from the investigation's.
"""
