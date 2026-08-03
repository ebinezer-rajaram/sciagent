"""Posterior updating, predictive checking and entropy (SPEC §3.5, §6.2).

Staged per SPEC F12: :class:`~sciagent.inference.empirical.EmpiricalTableEngine`
first, a likelihood-free engine later, each independently validated against
acceptance tests A6-A11 before any agent result depends on it.
"""

from __future__ import annotations
