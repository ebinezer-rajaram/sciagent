"""The consensus edit, fixed in source before any segment is ingested.

What the transfer test claims
-----------------------------

The QTM track asks whether a research system detects model inadequacy on data
where the answer is already known, and proposes the right extension. That is
only a test if the answer was fixed *first*. An edit chosen after seeing how a
system performed on the catalogue would be a much weaker claim wearing the same
words -- and nothing downstream could tell the two apart from the numbers alone.

So the edit is a module constant, and its digest is computed at import.

Why this edit
-------------

Seismology's consensus model for Southern California seismicity is ETAS:
magnitude-gated triggering, in which an event's size raises the rate of the
events that follow it. Fitted to this catalogue specifically -- Moutote et al.
(2021), van den Ende & Ampuero (2020). Expressed in the slice's own grammar that
is exactly one edit, ``AddDependency(size → arrival)``, which the library
already contains as SPEC §4.5's S11 mechanism.

That coincidence is the whole reason QTM was chosen over the alternatives in
``docs/BACKLOG.md``: the answer seismology settled is a structure the framework
can already express, so D1 has a surrogate on found data and Stage B has
something to be right or wrong about.

:data:`CONSENSUS_EDIT` is therefore ``SIZE_EXCITATION`` itself rather than a
copy. A copy could drift from the mechanism the closed-world scenarios use, and
the two would then be different edits with the same name -- which is the failure
gate A25's third clause exists to prevent.
"""

from __future__ import annotations

import hashlib

from environments.pointproc.mechanisms import SIZE_EXCITATION
from sciagent.core.edits import Edit
from sciagent.experiments.dsl import defect_key

__all__ = ["CONSENSUS_EDIT", "PREREGISTRATION_DIGEST", "preregistration_digest"]

#: The preregistered answer for the found-data track: ETAS magnitude-gated
#: triggering, which in this grammar is ``AddDependency(size → arrival)``.
CONSENSUS_EDIT: Edit = SIZE_EXCITATION


def preregistration_digest(edit: Edit) -> str:
    """Return the SHA-256 of ``edit``'s canonical key.

    Guarantees a value that is a pure function of the edit's structure and
    parameters, so a change to either -- including a re-calibration that moved a
    parameter without changing the edit's shape -- produces a different digest
    and therefore a different data address.

    Built on :func:`~sciagent.experiments.dsl.defect_key`, which is the
    canonicalisation an experiment address already renders a defect by; a
    second, private serialisation here could disagree with it and would then let
    a segment be addressed under an edit the registry considers different.
    """
    return hashlib.sha256(defect_key(frozenset({edit})).encode("utf-8")).hexdigest()


#: Fixed at import, which is what makes the third clause of gate A25 structural
#: rather than a convention: ``snapshot.data_version`` mixes this into every
#: segment's address, so a segment cannot be addressed -- and therefore cannot
#: be recorded against -- unless the preregistration already exists.
PREREGISTRATION_DIGEST = preregistration_digest(CONSENSUS_EDIT)
