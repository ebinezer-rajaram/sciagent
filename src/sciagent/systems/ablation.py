"""SPEC §11 item 13: the V3/V4 memory ablation, and R2.

R2 (SPEC §2) asks whether structured memory beats raw history **at equal
information**, and §5 line 282 names the pair "LLM with raw history versus LLM
with hypothesis graph". §9 line 399 scopes the comparison to S8, S11 and S12.

The whole ablation is one argument
----------------------------------

In this architecture the only thing a model ever sees is
:func:`~sciagent.systems.llm.encoding.render_brief`, so the memory
representation is a parameter of that function and of nothing else. Both arms
therefore entertain the same library, select experiments the same way through
:func:`sciagent.experiments.boed.plan`, and report a posterior the framework
computed. They are :class:`~sciagent.systems.hybrid.Hybrid` twice, under two
names, over two layers that differ in one enum value.

That is deliberate and it is the point of :func:`memory_ablation` existing at
all. "The arms differ only in memory" could have been a property to audit across
two classes; here it is a fact about how the pair is constructed, because every
argument but :class:`~sciagent.systems.llm.encoding.Memory` is shared.

Equal information, and what it rules out
----------------------------------------

The arms *swap* representations rather than nesting them. V3 sees the per-step
readings and the entertained structures unannotated; V4 sees those same
structures annotated with their posterior mass, and no readings. Neither brief's
content is a superset of the other's.

The alternative -- V4 as V3 plus the graph -- was rejected. It would measure
"a graph on top of a history" rather than "a graph instead of a history", and R2
says *at equal information*. ``docs/DECISIONS.md`` records the choice.

Where the delta is defined
--------------------------

SPEC F6 makes extension conditional on Stage A detection, and
:class:`~sciagent.systems.hybrid.Hybrid` honours that: it asks for a proposal
only when the posterior predictive check reports the entertained set inadequate.
On a scenario where the check passes, neither arm's brief is ever rendered and
the two run identical trajectories. That is not a defect in the ablation. It is
the honest report that R2 has no delta to measure there, and
``tests/test_ablation.py`` measures which scenarios it covers rather than
assuming.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sciagent.core.edits import Defect, EditGrammar
from sciagent.systems.hybrid import Hybrid
from sciagent.systems.llm.encoding import Memory
from sciagent.systems.llm.provider import ProposalLayer, Provider
from sciagent.systems.llm.transcripts import TranscriptStore

__all__ = ["ABLATION_SYSTEM_PROMPT", "memory_ablation"]


#: What both arms are told their job is.
#:
#: Deliberately **not**
#: :data:`~sciagent.systems.llm.provider.DEFAULT_SYSTEM_PROMPT`. That one names
#: the two things V7's brief carries -- "the posterior over the hypotheses
#: entertained so far" and "the observation that motivated the choice" -- and
#: each arm is missing one of them. Under the default prompt V3 would be told to
#: reason from a posterior it cannot see and V4 asked to cite an observation it
#: cannot see, which handicaps the arms *asymmetrically* and confounds exactly
#: the delta R2 is asking about. `ScriptedProvider` ignores the brief, so the
#: confound is invisible offline and would have surfaced only once a real model
#: read it.
#:
#: This prompt says the same thing about the division of labour while referring
#: to the brief generically, so it is true of both arms and of neither's absence.
#: V7 keeps the default untouched: the system prompt enters the transcript
#: address, so editing it would invalidate item 12's recorded corpus, and V7 is
#: not in §9's ablation cell anyway.
ABLATION_SYSTEM_PROMPT = """\
You are proposing structure for a scientific investigation.

An executable programme generates the data. Something has been changed in it,
and the change is one of the structures listed in the brief. Conventional
methods have already done the parts that are theirs: they maintain the belief
over the hypotheses entertained so far, and they run the check that says whether
those hypotheses explain the data.

Your job is to choose which structure to entertain next, given what the brief
shows you about the run so far and what the existing hypotheses fail to explain.
Choose the structure whose mechanism would produce the discrepancy the brief
shows -- not the one that is most complex, and not the one that is most familiar.

You choose a structure and, for each of its parameters, an index into that
parameter's grid. You do not choose experiments, and you never state a
probability, a plausibility, a score or any other number: those are computed
from your proposal by the framework, and there is no field in which you could
write one.

Give a short rationale naming what in the brief motivated the choice.\
"""


def memory_ablation(
    library: Mapping[str, Defect],
    provider: Callable[[], Provider],
    grammar: EditGrammar,
    store: TranscriptStore,
    *,
    max_proposals: int = 2,
    system_prompt: str = "",
) -> tuple[Hybrid, Hybrid]:
    """Return SPEC §5's ``(V3, V4)`` pair over one shared configuration.

    Guarantees the two systems differ in exactly one respect: the
    :class:`~sciagent.systems.llm.encoding.Memory` their proposal layer renders
    briefs under. Library, grammar, transcript store, proposal budget and system
    prompt are the same objects in both, so nothing else *can* differ.

    Each arm gets its own :class:`~sciagent.systems.llm.provider.ProposalLayer`,
    because a layer's call counter advances per proposal and enters the
    transcript address; sharing one would make each arm's second call address as
    though it were the other's. The ``store`` is shared and that is safe: the
    brief differs between the arms, so their addresses differ and neither can
    resolve a call the other recorded.

    ``provider`` is a **factory**, called once per arm, and that is the one
    signature decision here worth stating. A
    :class:`~sciagent.systems.llm.scripted.ScriptedProvider` consumes its script,
    so two arms drawing from one instance would see *different payloads* -- V4
    answering the second scripted proposal while V3 answered the first. The
    ablation would then be measuring what the backend said back rather than how
    the run was represented, and the confound would be invisible in the result.
    A factory makes "both arms see the same backend state" a fact about
    construction. For a stateless backend, ``lambda: backend`` is the whole of
    what it costs.

    ``system_prompt`` defaults to :data:`ABLATION_SYSTEM_PROMPT` and not to the
    layer's default, for the reason that constant records: the default names
    both of the things the arms differ in, and would hand each arm an
    instruction it cannot follow.

    **Build a fresh pair per scenario.** The guarantees above are made at
    construction and do not survive reuse: a layer's call counter and a scripted
    backend's script position both carry over, so a pair run on S8 and then on
    S12 addresses its S12 calls differently from a pair built for S12 -- and
    under :data:`~sciagent.systems.llm.transcripts.REPLAY` that is a
    :class:`~sciagent.core.errors.TranscriptMissError` rather than a wrong
    number. Constructing the pair is cheap; reusing it is the trap.
    """
    # `.strip()` rather than a bare `or`: a whitespace-only prompt is truthy, so
    # the plain fallback handed both arms a blank instruction and called it the
    # caller's choice. `Hybrid` already strips the name it is given for the same
    # reason; this makes the two agree.
    prompt = system_prompt.strip() or ABLATION_SYSTEM_PROMPT
    return (
        Hybrid(
            library,
            ProposalLayer(
                provider(),
                grammar,
                store,
                system_prompt=prompt,
                memory=Memory.RAW,
            ),
            max_proposals=max_proposals,
            name="V3",
        ),
        Hybrid(
            library,
            ProposalLayer(
                provider(),
                grammar,
                store,
                system_prompt=prompt,
                memory=Memory.GRAPH,
            ),
            max_proposals=max_proposals,
            name="V4",
        ),
    )
