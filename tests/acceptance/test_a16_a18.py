"""Acceptance tests A16-A18 (SPEC §6.4): hypothesis and schema validation.

One test per criterion, named for it. These are the contract for backlog item 5.

A17's static half went live at backlog item 9, and this note replaces the one
that stood while it could not.

It asks for static confirmation that no agent-accessible path writes
``plausibility``. ``AGENT_TOOL_SURFACE`` was declared at item 4 naming
``sciagent.systems``, and no module satisfied it until item 9 built the baselines
of SPEC §5, so the analysis over ``src`` was trivially clean and the static half
rested on the two fixtures instead. It no longer does:
``test_a17_the_surface_declaration_matches_real_modules`` fails if the surface
ever stops describing the tree again, so the criterion below cannot go quietly
vacuous a second time.

What item 9 exposed is that the criterion is unsatisfiable as literally stated. A
research system must be able to introduce a hypothesis, and introducing one
necessarily runs the plausibility derivation -- that is the framework writing the
number, which is what SPEC's second invariant *requires*. So every correct
systems layer has an agent-reachable path into ``_derive_plausibility``, and a
gate forbidding the path outright forbids the design. ``PLAUSIBILITY_DERIVATION``
names the three functions licensed to make that write, the analyser exempts their
own references and nothing else, and two tests here hold that boundary in place:
one checks every licensed function still needs its licence, the other checks the
licence does not extend to what a licensed function calls.

Worth recording separately: before the boundary existed, the surface's own module
passed only because ``Investigation.propose`` shadowed
``HypothesisGraph.propose`` under the analyser's simple-name resolution. Clean
for the wrong reason is worse than red.

The runtime half has stood on its own from day one -- the graph derives every
plausibility from the prefix code and refuses a forged one -- and is tested
separately below.
"""

from __future__ import annotations

import ast
import math
from dataclasses import replace
from inspect import signature
from itertools import islice
from pathlib import Path

import pytest
from callgraph import MODULE_SCOPE, analyse
from hypothesis import given
from hypothesis import strategies as st

from environments.pointproc import CONFOUNDED_MECHANISMS, edit_grammar, mechanism_defect
from environments.pointproc.catalogue import metric_registry
from environments.pointproc.components import MIXTURE_OF_EXPONENTIAL_2, SIZE
from environments.pointproc.grammar import EXPONENTIAL_MIXTURE_GRIDS
from sciagent.core.conditions import (
    COMPARE_OPS,
    And,
    Between,
    Compare,
    Condition,
    Not,
    Or,
    evaluate,
    intervals,
    satisfiable_over,
    witness,
)
from sciagent.core.edits import (
    ChangeDistributionFamily,
    Defect,
    Edit,
    canonical,
)
from sciagent.core.errors import (
    DuplicateHypothesisError,
    PlausibilityWriteError,
    UnfalsifiableHypothesisError,
)
from sciagent.core.types import (
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    MetricName,
    MetricRef,
    Prediction,
    PredictionId,
    Probability,
    RejectionCode,
)
from sciagent.hypothesis.graph import (
    PLAUSIBILITY_DERIVATION,
    PLAUSIBILITY_SYMBOLS,
    HypothesisGraph,
    HypothesisNode,
)
from sciagent.hypothesis.validator import (
    defect_signature,
    find_duplicate,
    validate_prediction,
)
from sciagent.registry.metrics import MetricRegistry
from sciagent.registry.partitions import AGENT_TOOL_SURFACE

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = Path(__file__).resolve().parents[2] / "src"

GRAPH = SOURCE / "sciagent" / "hypothesis" / "graph.py"

TEMPLATE = ExperimentTemplateId("observe_baseline")

METRICS: MetricRegistry = metric_registry()
GRAMMAR = edit_grammar()


def plausibility_definition_lines() -> frozenset[int]:
    """Return the lines of ``graph.py`` that *define* a plausibility symbol.

    A module names what it defines, so the module declaring both
    ``PLAUSIBILITY_SYMBOLS`` and the ``plausibility`` field necessarily
    references them at module scope. Neither is a write, and neither can appear
    in ``PLAUSIBILITY_DERIVATION``, which is a list of *functions*.

    Read from ``graph.py``'s own AST rather than written down as line numbers, so
    the carve-out moves when the declaration does and cannot quietly come to
    cover a third site.

    Exactly two shapes qualify, and the narrowness is the point. A bare
    annotation with no value (``plausibility: Probability``) *cannot* be a write:
    it binds nothing. The ``PLAUSIBILITY_SYMBOLS`` assignment is named
    explicitly, by that name, at module level. Matching "any assignment binding
    ``plausibility``" instead -- the first version of this -- would have exempted
    a module-scope ``plausibility = ...``, which is a write, so the carve-out
    would have whitelisted the one construct it exists to catch.
    """
    tree = ast.parse(GRAPH.read_text(encoding="utf-8"), filename=str(GRAPH))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets: list[ast.expr] = (
            [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
        )
        bound = {target.id for target in targets if isinstance(target, ast.Name)}
        declares_field = (
            isinstance(node, ast.AnnAssign)
            and node.value is None
            and "plausibility" in bound
        )
        if declares_field or "PLAUSIBILITY_SYMBOLS" in bound:
            end = node.end_lineno if node.end_lineno is not None else node.lineno
            lines.update(range(node.lineno, end + 1))
    assert lines, f"{GRAPH.name} declares neither PLAUSIBILITY_SYMBOLS nor the field"
    return frozenset(lines)


def prediction(
    metric: str,
    *,
    refutation: Condition,
    condition: Condition | None = None,
    hypothesis: str = "h",
    name: str = "p",
) -> Prediction:
    """Build a prediction against a registered diagnostic."""
    spec = METRICS.spec(metric)
    return Prediction(
        id=PredictionId(f"{hypothesis}/{name}"),
        hypothesis_id=HypothesisId(hypothesis),
        diagnostic=spec.ref,
        condition=condition if condition is not None else Not(refutation),
        under=TEMPLATE,
        refutation=refutation,
    )


def populated() -> HypothesisGraph:
    """A graph holding one hypothesis per confounded mechanism.

    Four distinct defects of differing code length, which is what the plausibility
    tests need: a prior derived from a prefix code is only observably derived if
    the hypotheses it ranges over do not all cost the same.
    """
    graph = HypothesisGraph.empty(GRAMMAR, METRICS)
    for name in sorted(CONFOUNDED_MECHANISMS):
        graph = graph.propose(
            HypothesisId(name),
            program_edit=mechanism_defect(name),
            predictions=(
                prediction(
                    "fano_factor_w2", refutation=Compare("<", 1.5), hypothesis=name
                ),
            ),
            rationale=f"{name} produces overdispersed counts",
        )
    return graph


# ==========================================================================
# A16  Falsifiability
# ==========================================================================

#: Metrics whose declared range is [-1, 1].
BOUNDED: tuple[str, ...] = ("count_autocorrelation_w2", "sign_autocorrelation")

#: Metrics whose declared range is [0, inf).
NON_NEGATIVE: tuple[str, ...] = (
    "mean_rate",
    "fano_factor_w2",
    "inter_arrival_dispersion",
    "size_dispersion",
)

#: Metrics whose declared range is [1, inf).
FLOORED: tuple[str, ...] = ("spectral_peak_prominence", "mean_high_run_length")

#: Refutation shapes that fall wholly outside [-1, 1].
OUTSIDE_BOUNDED: tuple[Condition, ...] = (
    Compare(">", 1.0),
    Compare(">=", 1.5),
    Compare("<", -1.0),
    Compare("<=", -1.5),
    Between(1.2, 3.0),
    Between(-9.0, -2.0),
    Or((Compare(">", 2.0), Compare("<", -2.0))),
    Not(Between(-1.0, 1.0)),
)

#: Refutation shapes that fall wholly below 0.
BELOW_ZERO: tuple[Condition, ...] = (
    Compare("<", 0.0),
    Compare("<=", -0.5),
    Between(-5.0, -1.0),
    Not(Compare(">=", 0.0)),
    And((Compare("<", 0.0), Compare(">", -100.0))),
)

#: Refutation shapes that fall wholly below 1.
BELOW_ONE: tuple[Condition, ...] = (
    Compare("<", 1.0),
    Between(0.0, 0.5),
    Compare("<=", 0.99),
)

#: Refutations unsatisfiable on their own terms, whatever the diagnostic's range.
SELF_CONTRADICTORY: tuple[Condition, ...] = (
    And((Compare(">", 5.0), Compare("<", 5.0))),
    And((Compare(">=", 1.0), Compare("<", 1.0))),
    And((Compare("==", 1.0), Compare("==", 2.0))),
    And((Compare("==", 3.0), Compare("!=", 3.0))),
    Not(Compare(">=", -math.inf)),
    And((Compare(">", 2.0), Compare("<=", 2.0))),
    Between(2.0, 2.0, low_closed=False),
    And((Between(0.0, 1.0), Between(2.0, 3.0))),
)

#: Endpoints the generated conditions compare against. Deliberately coarse and
#: shared with :data:`PROBES`, so that generated conditions land exactly on the
#: values probed: the interesting disagreements between an interval denotation
#: and a direct evaluation are all at closed-versus-open endpoints, and a
#: strategy over arbitrary floats would almost never produce one.
THRESHOLDS: tuple[float, ...] = (
    -math.inf,
    -2.0,
    -1.0,
    -0.5,
    0.0,
    0.5,
    1.0,
    2.0,
    math.inf,
)

#: Values the two implementations are compared at. The neighbours of each
#: threshold are included so that strict and non-strict comparisons are
#: distinguished.
#:
#: Finite by construction. The condition algebra models the finite reals -- an
#: infinite endpoint is always open -- so the two implementations genuinely
#: disagree at ``±inf`` and the disagreement is pinned by its own test below
#: rather than generated into every property.
PROBES: tuple[float, ...] = (
    *(t for t in THRESHOLDS if math.isfinite(t)),
    *(math.nextafter(t, -math.inf) for t in THRESHOLDS if math.isfinite(t)),
    *(math.nextafter(t, math.inf) for t in THRESHOLDS if math.isfinite(t)),
    -3.0,
    0.25,
    1.5,
    3.0,
)

CONDITIONS: st.SearchStrategy[Condition] = st.recursive(
    st.one_of(
        st.builds(Compare, st.sampled_from(COMPARE_OPS), st.sampled_from(THRESHOLDS)),
        st.builds(
            Between,
            st.sampled_from(THRESHOLDS),
            st.sampled_from(THRESHOLDS),
            st.booleans(),
            st.booleans(),
        ),
    ),
    lambda children: st.one_of(
        st.builds(And, st.lists(children, max_size=3).map(tuple)),
        st.builds(Or, st.lists(children, max_size=3).map(tuple)),
        st.builds(Not, children),
    ),
    max_leaves=6,
)

#: The 50 hand-constructed unsatisfiable cases A16 asks for, as
#: ``(metric, refutation)`` pairs.
UNSATISFIABLE: tuple[tuple[str, Condition], ...] = (
    *((metric, cond) for metric in BOUNDED for cond in OUTSIDE_BOUNDED),
    *((metric, cond) for metric in NON_NEGATIVE for cond in BELOW_ZERO),
    *((metric, cond) for metric in FLOORED for cond in BELOW_ONE),
    *(("mean_rate", cond) for cond in SELF_CONTRADICTORY),
)

#: Controls: refutations that *are* satisfiable, so the criterion cannot be met
#: by a validator that rejects everything.
SATISFIABLE: tuple[tuple[str, Condition], ...] = (
    ("count_autocorrelation_w2", Compare(">", 0.2)),
    ("count_autocorrelation_w2", Compare("<=", -1.0)),
    ("sign_autocorrelation", Between(-0.5, 0.5)),
    ("mean_rate", Compare("<=", 0.0)),
    ("fano_factor_w2", Compare(">", 3.0)),
    ("fano_factor_w2", Or((Compare("<", -1.0), Compare(">", 8.0)))),
    ("inter_arrival_dispersion", Not(Between(0.5, 2.0))),
    ("spectral_peak_prominence", Compare("<=", 1.0)),
    ("mean_high_run_length", Between(1.0, 1.0)),
    ("size_skewness", Compare("<", -2.0)),
)


class TestA16Falsifiability:
    """Hypotheses whose refutation is unsatisfiable over the range are rejected."""

    def test_a16_the_case_set_meets_the_declared_size(self) -> None:
        """SPEC §6.4 asks for 50 cases; a smaller set would not discharge it."""
        assert len(UNSATISFIABLE) >= 50, (
            f"A16 requires 50 unsatisfiable cases, this set has {len(UNSATISFIABLE)}"
        )
        assert len(set(UNSATISFIABLE)) == len(UNSATISFIABLE), "cases are not distinct"

    @pytest.mark.parametrize(("metric", "refutation"), UNSATISFIABLE)
    def test_a16_unsatisfiable_refutations_are_rejected(
        self, metric: str, refutation: Condition
    ) -> None:
        """Each of the 50 constructed cases is rejected, with the right code."""
        report = validate_prediction(prediction(metric, refutation=refutation), METRICS)
        assert not report.ok
        assert RejectionCode.UNSATISFIABLE_REFUTATION in report.codes, (
            f"{metric} / {refutation} was rejected, but for {report.codes}"
        )

    @pytest.mark.parametrize(("metric", "refutation"), SATISFIABLE)
    def test_a16_satisfiable_refutations_are_accepted(
        self, metric: str, refutation: Condition
    ) -> None:
        """The control: a validator that rejected everything would fail here."""
        report = validate_prediction(prediction(metric, refutation=refutation), METRICS)
        assert report.ok, report.messages

    def test_a16_satisfiability_respects_the_declared_range(self) -> None:
        """The check is over the diagnostic's range, not over the reals.

        ``fano < 0`` is perfectly satisfiable as arithmetic. It is unsatisfiable
        as a refutation because the Fano factor is declared non-negative, and
        that distinction is the whole content of A16.
        """
        below_zero = Compare("<", 0.0)
        assert satisfiable_over(below_zero, -1.0, 1.0)
        assert not satisfiable_over(below_zero, 0.0, math.inf)

    @given(condition=CONDITIONS, value=st.sampled_from(PROBES))
    def test_a16_the_interval_denotation_agrees_with_direct_evaluation(
        self, condition: Condition, value: float
    ) -> None:
        """The property the whole criterion rests on.

        A16's verdict is decided by interval arithmetic, and the 50 cases above
        only check the answers on 50 conditions someone thought of. This checks
        the machinery itself, against a second implementation that recurses on
        the condition and compares numbers directly. If the two agree at every
        probed value for every generated condition, the denotation is right and
        the verdicts follow from it.
        """
        denoted = any(piece.contains(value) for piece in intervals(condition))
        assert denoted == evaluate(condition, value)

    @given(condition=CONDITIONS)
    def test_a16_a_satisfiable_condition_yields_a_valid_witness(
        self, condition: Condition
    ) -> None:
        """A "satisfiable" verdict names the outcome that justifies it."""
        found = witness(condition, 0.0, 10.0)
        if found is not None:
            assert 0.0 <= found <= 10.0
            assert evaluate(condition, found), (
                f"witness {found!r} does not satisfy {condition!r}"
            )

    @given(condition=CONDITIONS, value=st.sampled_from(PROBES))
    def test_a16_an_unsatisfiable_verdict_has_no_counterexample(
        self, condition: Condition, value: float
    ) -> None:
        """The dangerous direction: a false rejection kills a good hypothesis."""
        if satisfiable_over(condition, 0.0, 10.0) or not 0.0 <= value <= 10.0:
            return
        assert not evaluate(condition, value), (
            f"{condition!r} was called unsatisfiable over 0..10 but {value!r} "
            f"satisfies it"
        )

    @given(condition=CONDITIONS)
    def test_a16_negation_is_involutive(self, condition: Condition) -> None:
        """Normalisation does not lose or invent points under double negation."""
        assert intervals(Not(Not(condition))) == intervals(condition)

    def test_a16_infinite_values_are_outside_the_domain(self) -> None:
        """The one place the two implementations part company, pinned deliberately.

        The interval representation keeps infinite endpoints open, so ``±inf`` is
        in no denotation; the direct evaluator answers about IEEE values, where
        ``-inf >= -inf`` holds. A diagnostic returning an infinity would fall in
        the gap, and could be called unable to refute a hypothesis it does in
        fact refute.

        It is sound for the slice because the estimators raise on the inputs that
        would produce an infinity rather than returning one, so no such value
        reaches a condition. This test exists so that an environment which does
        produce one fails here instead of inheriting the assumption in silence.
        """
        whole_line = And(())
        for value in (math.inf, -math.inf):
            assert evaluate(whole_line, value)
            assert not any(piece.contains(value) for piece in intervals(whole_line))
        assert not satisfiable_over(Compare("==", math.inf), 0.0, math.inf)

    def test_a16_graph_refuses_an_unfalsifiable_hypothesis(self) -> None:
        """The gate is enforced at the point of entry, not merely reportable."""
        graph = HypothesisGraph.empty(GRAMMAR, METRICS)
        with pytest.raises(UnfalsifiableHypothesisError):
            graph.propose(
                HypothesisId("h"),
                program_edit=mechanism_defect("hawkes"),
                predictions=(
                    prediction("fano_factor_w2", refutation=Compare("<", 0.0)),
                ),
            )

    def test_a16_graph_refuses_a_hypothesis_with_no_predictions(self) -> None:
        """SPEC §3.3 requires at least one prediction; none is unfalsifiable."""
        graph = HypothesisGraph.empty(GRAMMAR, METRICS)
        with pytest.raises(UnfalsifiableHypothesisError) as excinfo:
            graph.propose(
                HypothesisId("h"),
                program_edit=mechanism_defect("hawkes"),
                predictions=(),
            )
        assert RejectionCode.NO_PREDICTIONS.value in str(excinfo.value)

    def test_a16_tautological_refutation_is_its_own_code(self) -> None:
        """A refutation covering the whole range is reported separately.

        It is not the criterion A16 states, so it carries its own code rather
        than inflating the count of unsatisfiable cases. It never appears alone:
        a refutation that covers everything leaves the prediction's condition
        either unsatisfiable or overlapping it, so a second code always follows.
        What matters is that the tautology is named, rather than the report
        showing only the downstream consequence.
        """
        report = validate_prediction(
            prediction("fano_factor_w2", refutation=Compare(">=", 0.0)), METRICS
        )
        assert RejectionCode.TAUTOLOGICAL_REFUTATION in report.codes
        assert RejectionCode.UNSATISFIABLE_REFUTATION not in report.codes

    def test_a16_overlapping_condition_and_refutation_are_rejected(self) -> None:
        """One observation may not both confirm and refute."""
        report = validate_prediction(
            prediction(
                "fano_factor_w2",
                condition=Compare(">", 2.0),
                refutation=Compare(">", 3.0),
            ),
            METRICS,
        )
        assert RejectionCode.OVERLAPPING_REFUTATION in report.codes

    def test_a16_unknown_metric_is_rejected(self) -> None:
        """A prediction against an unregistered diagnostic has no range to check."""
        unknown = replace(
            prediction("fano_factor_w2", refutation=Compare(">", 3.0)),
            diagnostic=MetricRef(name=MetricName("no_such_metric"), version="1.0.0"),
        )
        report = validate_prediction(unknown, METRICS)
        assert report.codes == (RejectionCode.UNKNOWN_METRIC,)


# ==========================================================================
# A17  Plausibility immutability
# ==========================================================================


class TestA17PlausibilityImmutability:
    """No agent-accessible path writes ``plausibility``."""

    def test_a17_symbol_declaration_is_present(self) -> None:
        """The declaration exists, so the analysis below cannot go vacuous."""
        assert PLAUSIBILITY_SYMBOLS, "no plausibility symbols are declared"
        assert "plausibility" in PLAUSIBILITY_SYMBOLS
        assert AGENT_TOOL_SURFACE, "the agent tool surface declaration is empty"

    def test_a17_the_surface_declaration_matches_real_modules(self) -> None:
        """The static analysis is over something. It was not, until item 9.

        ``AGENT_TOOL_SURFACE`` was declared at item 4 naming ``sciagent.systems``,
        which no module satisfied until the baselines of SPEC §5 were built. Until
        then the analysis over ``src`` was trivially clean, and this class's other
        assertions were what carried A17's static half. This test fails the day
        the surface stops describing the tree again.
        """
        analysis = analyse(
            SOURCE,
            surface=AGENT_TOOL_SURFACE,
            sealed_symbols=PLAUSIBILITY_SYMBOLS,
            licensed=PLAUSIBILITY_DERIVATION,
        )
        assert analysis.matched_patterns, (
            f"no module matches the agent tool surface {AGENT_TOOL_SURFACE!r}, so "
            f"the criterion below examines nothing"
        )
        functions = [
            entry for entry in analysis.entry_points if not entry.endswith(MODULE_SCOPE)
        ]
        assert functions, (
            "the surface matched modules but no functions. Compared against real "
            "definitions rather than against entry_points, which since A32 is "
            "non-empty for any matched module and could not fail."
        )

    def test_a17_no_agent_path_writes_plausibility(self) -> None:
        """The criterion itself, over the shipped source tree.

        ``licensed`` exempts the framework's own derivation, and only its own
        references -- see ``PLAUSIBILITY_DERIVATION`` for why the criterion is
        unsatisfiable without a named boundary and why it is these three
        functions.
        """
        analysis = analyse(
            SOURCE,
            surface=AGENT_TOOL_SURFACE,
            sealed_symbols=PLAUSIBILITY_SYMBOLS,
            licensed=PLAUSIBILITY_DERIVATION,
        )
        assert analysis.clean, "\n".join(str(path) for path in analysis.paths)

    def test_a17_every_licensed_function_still_needs_its_licence(self) -> None:
        """A boundary that outgrows its reason is a blanket exemption.

        Each named function must exist and must actually reference a plausibility
        symbol. An entry that no longer does is dead, and dead entries are how a
        narrow exemption turns into a wide one without anybody deciding to widen
        it.

        Module scope is compared separately, not exempted. Since A32 the analyser
        reads import-time code, and ``graph.py`` is where both
        ``PLAUSIBILITY_SYMBOLS`` and the ``plausibility`` field are *defined* --
        a module names what it defines. Those references are held to a stricter
        rule than the functions are: they must fall on the definition lines
        themselves. A module-level statement in ``graph.py`` that touched
        plausibility anywhere else still fails this test, which a blanket
        exclusion of ``<module>`` would not have caught.
        """
        flagged = analyse(
            SOURCE,
            surface=("sciagent.hypothesis.graph",),
            sealed_symbols=PLAUSIBILITY_SYMBOLS,
        )
        touching = {
            f"{path.reference.module}.{path.reference.function}"
            for path in flagged.paths
            if path.reference.function != MODULE_SCOPE
        }
        assert set(PLAUSIBILITY_DERIVATION) == touching, (
            f"declared boundary {sorted(PLAUSIBILITY_DERIVATION)!r} does not match "
            f"the functions that actually touch a plausibility symbol "
            f"{sorted(touching)!r}"
        )

        defining = [
            path.reference
            for path in flagged.paths
            if path.reference.function == MODULE_SCOPE
        ]
        assert defining, (
            "no module-scope reference was found, so the rule below is dead and "
            "the analyser has stopped reading import-time code"
        )
        # The line numbers below come from graph.py, so only graph.py's own
        # references may be judged against them. Today the surface above is that
        # one module and <module> is never a callee, so nothing else can appear
        # here -- but that is a coupling between two lines rather than a stated
        # rule, and widening the surface tuple would silently exempt a sibling
        # module's module-scope write whenever its line number collided.
        foreign = sorted(
            f"{reference.module}.{MODULE_SCOPE} line {reference.lineno}"
            for reference in defining
            if reference.module != "sciagent.hypothesis.graph"
        )
        assert not foreign, (
            f"a module other than {GRAPH.name} reached module scope here; its "
            f"references are being judged against {GRAPH.name}'s line numbers, "
            f"which means nothing: {foreign}"
        )
        definitions = plausibility_definition_lines()
        stray = sorted(
            f"line {reference.lineno} references {reference.symbol}"
            for reference in defining
            if reference.lineno not in definitions
        )
        assert not stray, (
            f"{GRAPH.name} touches a plausibility symbol at module scope outside "
            f"its own definitions: {stray}"
        )

    def test_a17_the_licence_does_not_extend_one_hop_further(self) -> None:
        """Exempting a function must not exempt what it calls.

        The whole value of a named boundary is that it is a boundary. If the
        analyser stopped traversing at a licensed function instead of merely
        ignoring its own references, a write could be hidden by putting it in a
        helper, and this test is what forbids that.
        """
        analysis = analyse(
            FIXTURES,
            surface=("plausibility_writer",),
            sealed_symbols=PLAUSIBILITY_SYMBOLS,
            licensed=("plausibility_writer.agent_scores",),
        )
        assert not analysis.clean, (
            "licensing the entry point hid a write in the function it calls; the "
            "exemption is being applied transitively"
        )

    def test_a17_analyser_detects_the_negative_control(self) -> None:
        """The planted write is found, transitively, through one hop."""
        analysis = analyse(
            FIXTURES,
            surface=("plausibility_writer",),
            sealed_symbols=PLAUSIBILITY_SYMBOLS,
        )
        assert not analysis.clean, "the analyser missed the planted write"
        assert any(
            path.entry.endswith("agent_scores") and len(path.chain) > 1
            for path in analysis.paths
        ), f"write found, but not transitively: {[str(p) for p in analysis.paths]}"

    def test_a17_analyser_clears_the_positive_control(self) -> None:
        """A surface module that touches no plausibility is reported clean."""
        analysis = analyse(
            FIXTURES, surface=("clean_tool",), sealed_symbols=PLAUSIBILITY_SYMBOLS
        )
        assert analysis.clean, "\n".join(str(path) for path in analysis.paths)
        functions = [
            entry for entry in analysis.entry_points if not entry.endswith(MODULE_SCOPE)
        ]
        assert functions, "the positive control matched no function"

    def test_a17_propose_accepts_no_plausibility_argument(self) -> None:
        """There is no write path because there is no parameter to write through."""
        parameters = signature(HypothesisGraph.propose).parameters
        assert not any("plausib" in name for name in parameters), sorted(parameters)

    def test_a17_plausibility_is_the_normalised_prefix_code_prior(self) -> None:
        """The number is derived from ``code_length``, not supplied by anyone."""
        graph = populated()
        weights = {
            node_id: math.exp2(-GRAMMAR.code_length(node.program_edit))
            for node_id, node in graph.nodes.items()
            if node.program_edit is not None
        }
        total = math.fsum(weights[key] for key in sorted(weights))
        for node_id, node in graph.nodes.items():
            assert node.plausibility == weights[node_id] / total

    def test_a17_plausibility_is_a_normalised_distribution(self) -> None:
        graph = populated()
        mass = math.fsum(graph.nodes[key].plausibility for key in sorted(graph.nodes))
        assert mass == pytest.approx(1.0)
        assert all(node.plausibility > 0.0 for node in graph.nodes.values())

    def test_a17_forged_plausibility_is_refused_at_runtime(self) -> None:
        """Static analysis is not the only guard.

        Reaching past the constructor to plant a number -- which is what an
        agent-authored tool would have to do -- is refused when the graph next
        checks itself, because every stored value is re-derived and compared.
        """
        graph = populated()
        target = sorted(graph.nodes)[0]
        forged = dict(graph.nodes)
        forged[target] = replace(forged[target], plausibility=Probability(0.99))
        with pytest.raises(PlausibilityWriteError):
            replace(graph, nodes=FrozenDict[HypothesisId, HypothesisNode](forged))

    def test_a17_a_simpler_defect_is_never_less_plausible(self) -> None:
        """The prior is parsimony, so it must order by code length.

        A prior that failed this would be arithmetically normalised and
        scientifically meaningless.
        """
        graph = populated()
        edits = graph.edits()
        assert set(edits) == set(graph.nodes), "a node has no compiled edit to cost"
        ordered = sorted(edits, key=lambda key: GRAMMAR.code_length(edits[key]))
        plausibilities = [graph.nodes[key].plausibility for key in ordered]
        assert plausibilities == sorted(plausibilities, reverse=True)

    def test_a17_status_transitions_do_not_disturb_the_prior(self) -> None:
        """Rejecting a hypothesis is an evidential act, not a prior revision."""
        graph = populated()
        target = sorted(graph.nodes)[0]
        before = {key: graph.nodes[key].plausibility for key in graph.nodes}
        after = graph.reject(target, RejectionCode.DUPLICATE)
        assert after.nodes[target].status == "rejected"
        assert {key: after.nodes[key].plausibility for key in after.nodes} == before


# ==========================================================================
# A18  Duplicate detection
# ==========================================================================


def edit_pool(limit: int) -> tuple[Edit, ...]:
    """Return a deterministic slice of the grammar's enumerable edit space.

    ``max_per_structure`` is set well above ``limit // structures`` so the pool
    is drawn from every structure the grammar licenses rather than exhausting
    the first few, and so ``limit`` is always reached.
    """
    pool = tuple(islice(GRAMMAR.enumerate_edits(max_per_structure=32), limit))
    assert len(pool) == limit, f"the grammar yielded {len(pool)} edits, not {limit}"
    return pool


def constructed_pairs() -> tuple[tuple[Defect, Defect, bool], ...]:
    """Return 200 ``(left, right, duplicate)`` triples.

    Half are duplicates presented differently -- permuted construction order,
    permuted parameter insertion order, a set built from a list with a repeat.
    Half differ, most of them by a single grid point, which is the case a
    signature computed over rounded floats would get wrong.
    """
    pool = edit_pool(100)
    pairs: list[tuple[Defect, Defect, bool]] = []
    for index, edit in enumerate(pool):
        other = pool[(index + 1) % len(pool)]
        second = pool[(index + 37) % len(pool)]
        shuffled = replace(
            edit,
            parameters=FrozenDict[str, float](
                dict(reversed(list(edit.parameters.items())))
            ),
        )
        if index % 2 == 0:
            pairs.append((frozenset({edit}), frozenset({shuffled}), True))
        else:
            pairs.append(
                (
                    frozenset(
                        [edit, *([second] if second.target != edit.target else [])]
                    ),
                    frozenset(
                        [*([second] if second.target != edit.target else []), edit]
                    ),
                    True,
                )
            )
        pairs.append((frozenset({edit}), frozenset({other}), edit == other))
    return tuple(pairs[:200])


PAIRS = constructed_pairs()


class TestA18DuplicateDetection:
    """Structurally identical edit sets are detected as duplicates."""

    def test_a18_the_pair_set_meets_the_declared_size(self) -> None:
        assert len(PAIRS) == 200
        assert sum(1 for _, _, duplicate in PAIRS if duplicate) >= 90
        assert sum(1 for _, _, duplicate in PAIRS if not duplicate) >= 90

    def test_a18_two_hundred_constructed_pairs(self) -> None:
        """Every pair is classified correctly. No exceptions, no near-misses."""
        wrong = [
            (left, right, expected)
            for left, right, expected in PAIRS
            if (defect_signature(left) == defect_signature(right)) is not expected
        ]
        assert not wrong, f"{len(wrong)} pair(s) misclassified, first: {wrong[0]}"

    def test_a18_signature_ignores_construction_order(self) -> None:
        """Permutation invariance, stated directly rather than only via pairs."""
        for left, right, expected in PAIRS:
            if expected:
                assert canonical(left) == canonical(right)

    def test_a18_grid_neighbours_are_distinct(self) -> None:
        """Two edits one grid point apart are not duplicates.

        The nearest pair in the log-spaced grids differ in the fourth decimal, so
        a signature over rounded values would collapse them.
        """
        pool = edit_pool(40)
        signatures = {defect_signature(frozenset({edit})) for edit in pool}
        assert len(signatures) == len(pool)

    def test_a18_graph_refuses_a_duplicate_proposal(self) -> None:
        graph = populated()
        existing = sorted(graph.nodes)[0]
        duplicate = graph.nodes[existing].program_edit
        assert duplicate is not None
        with pytest.raises(DuplicateHypothesisError) as excinfo:
            graph.propose(
                HypothesisId("fresh"),
                program_edit=frozenset(reversed(list(duplicate))),
                predictions=(
                    prediction(
                        "fano_factor_w2",
                        refutation=Compare(">", 8.0),
                        hypothesis="fresh",
                    ),
                ),
            )
        assert existing in str(excinfo.value)

    def test_a18_a_rejected_hypothesis_still_blocks_its_duplicate(self) -> None:
        """Re-proposing a refuted hypothesis is exactly the zombie SPEC §12.8 bans."""
        graph = populated()
        existing = sorted(graph.nodes)[0]
        graph = graph.reject(existing, RejectionCode.UNSATISFIABLE_REFUTATION)
        edit = graph.nodes[existing].program_edit
        assert edit is not None
        assert find_duplicate(graph, edit) == existing

    def test_a18_a_distinct_defect_is_not_a_duplicate(self) -> None:
        """The control. All four mechanisms edit ``arrival``; this edits ``size``."""
        fresh: Defect = frozenset(
            {
                ChangeDistributionFamily(
                    target=SIZE,
                    family=MIXTURE_OF_EXPONENTIAL_2,
                    parameters=FrozenDict[str, float](
                        {
                            grid.name: grid.values[0]
                            for grid in EXPONENTIAL_MIXTURE_GRIDS
                        }
                    ),
                )
            }
        )
        GRAMMAR.validate_defect(fresh)
        assert find_duplicate(populated(), fresh) is None
