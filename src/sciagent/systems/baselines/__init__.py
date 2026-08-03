"""The conventional systems of SPEC §5.

Four of the six live here: V1 (BOED-only), B1 (PPC-only), B4 (retrieval) and
B5 (beam search). V7 and the V3/V4 ablation arrive with backlog items 12 and 13,
after this apparatus has been validated without an LLM in it.

Each is deliberately a fair opponent rather than a straw man. SPEC §12 says in
as many words that beating B4 or B5 is not an exit criterion -- it is the
research question -- so a baseline that lost because it was built carelessly
would destroy the result rather than flatter it.
"""

from __future__ import annotations
