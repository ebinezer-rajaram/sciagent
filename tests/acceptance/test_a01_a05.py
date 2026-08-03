"""Acceptance tests A1-A5 (SPEC §6.1): generative programme and grammar.

One test per criterion, named for it. These are the contract for backlog items
2 and 3; nothing downstream may proceed while any of them fails.
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from typing import TypeVar

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from environments.pointproc import (
    CONFOUNDED_MECHANISMS,
    SIZE_EXCITATION,
    agent_grammar,
    arrival_burst,
    edit_grammar,
    mechanism_defect,
    reference_program,
)
from environments.pointproc.components import ARRIVAL, OBS, SIGN, SIZE
from environments.pointproc.grammar import GRID_SIZE
from sciagent.core.edits import (
    AddDependency,
    AddLatentVariable,
    ChangeDistributionFamily,
    Defect,
    DependencyOption,
    Edit,
    EditGrammar,
    FamilyOption,
    LatentOption,
    ParameterGrid,
    ReparameteriseComponent,
    canonical,
)
from sciagent.core.errors import CyclicDependencyError, OffGridParameterError
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    ComponentId,
    FamilyId,
    FrozenDict,
    GrammarVersion,
    Seed,
)

CHILD = Path(__file__).parent / "determinism_child.py"

ALL_DEFECTS: dict[str, Defect] = {
    "reference": frozenset(),
    "size_excitation": frozenset({SIZE_EXCITATION}),
    **{name: mechanism_defect(name) for name in CONFOUNDED_MECHANISMS},
}


def edited_program(defect: Defect) -> GenerativeProgram:
    return edit_grammar().apply(reference_program(), defect)


# ==========================================================================
# A1  Determinism
# ==========================================================================


class TestA1Determinism:
    """``execute(seed, n)`` is byte-identical across 100 repeats and processes."""

    @pytest.mark.parametrize("name", sorted(ALL_DEFECTS))
    def test_a1_byte_identical_across_100_repeats(self, name: str) -> None:
        program = edited_program(ALL_DEFECTS[name])
        expected = program.execute(Seed(4242), 256).to_bytes()
        for repeat in range(100):
            actual = (
                edited_program(ALL_DEFECTS[name]).execute(Seed(4242), 256).to_bytes()
            )
            assert actual == expected, f"{name} diverged on repeat {repeat}"

    def test_a1_byte_identical_across_processes(self) -> None:
        """The arm that catches hash randomisation and iteration-order leaks.

        Three child processes run under different ``PYTHONHASHSEED`` values,
        including ``random``. A programme that derived a stream from :func:`hash`
        or that walked a ``set`` would agree with itself in-process and disagree
        here.
        """
        outputs = []
        for hash_seed in ("0", "1", "random"):
            environment = dict(os.environ, PYTHONHASHSEED=hash_seed)
            completed = subprocess.run(
                [sys.executable, str(CHILD)],
                capture_output=True,
                text=True,
                check=True,
                env=environment,
            )
            outputs.append((hash_seed, completed.stdout))
        reference_output = outputs[0][1]
        assert reference_output.strip(), "child process produced no digests"
        for hash_seed, output in outputs[1:]:
            assert output == reference_output, (
                f"PYTHONHASHSEED={hash_seed} produced different digests:\n"
                f"{reference_output}\nvs\n{output}"
            )

    def test_a1_in_process_matches_subprocess(self) -> None:
        """The in-process result equals the subprocess result, digest for digest."""
        sys.path.insert(0, str(CHILD.parent))
        try:
            import determinism_child
        finally:
            sys.path.pop(0)
        completed = subprocess.run(
            [sys.executable, str(CHILD)], capture_output=True, text=True, check=True
        )
        expected = "".join(
            f"{name} {digest}\n" for name, digest in determinism_child.digests().items()
        )
        assert completed.stdout == expected

    def test_a1_clamped_execution_is_byte_identical_across_100_repeats(self) -> None:
        """The in-process arm for clamped execution (backlog item 7).

        Hawkes is the case that matters: it simulates by Ogata thinning, so its
        draw count depends on its own history, and a forced burst is the input
        that perturbs that history most.
        """
        clamps = {ARRIVAL: dict(arrival_burst(8, 0.01))}
        program = edited_program(ALL_DEFECTS["hawkes"])
        expected = program.execute(Seed(4242), 256, clamps=clamps).to_bytes()
        for repeat in range(100):
            actual = (
                edited_program(ALL_DEFECTS["hawkes"])
                .execute(Seed(4242), 256, clamps=clamps)
                .to_bytes()
            )
            assert actual == expected, f"clamped hawkes diverged on repeat {repeat}"

    def test_a1_clamps_are_not_silently_ignored(self) -> None:
        """Guards against a degenerate pass of the arms above.

        Every determinism check compares a run against itself, so a clamp that
        was quietly dropped would satisfy all of them. The three execution modes
        the child process reports must therefore produce three *different*
        digests for every programme.
        """
        sys.path.insert(0, str(CHILD.parent))
        try:
            import determinism_child
        finally:
            sys.path.pop(0)
        digests = determinism_child.digests()
        for name in ALL_DEFECTS:
            modes = {
                suffix: digests[f"{name}{suffix}"]
                for suffix in determinism_child.CLAMP_MODES
            }
            assert len(set(modes.values())) == len(modes), (
                f"{name}: clamped and unclamped runs agree, so a clamp is being "
                f"ignored: {modes!r}"
            )

    def test_a1_distinct_seeds_give_distinct_logs(self) -> None:
        """Guards against a degenerate pass: seeds must actually matter."""
        program = reference_program()
        digests = {
            hashlib.sha256(program.execute(Seed(seed), 128).to_bytes()).hexdigest()
            for seed in range(16)
        }
        assert len(digests) == 16

    def test_a1_seed_derivation_is_positional_independent(self) -> None:
        """Defecting one component must not perturb another component's stream.

        Seeds are derived by name, not by draw order, so ``arrival`` produces the
        same times whether or not ``size`` carries a defect. A ``SeedSequence``
        spawned in visit order would fail this.
        """
        grammar = edit_grammar()
        size_option = grammar.families[SIZE][0]
        size_edit = ChangeDistributionFamily(
            SIZE, size_option.family, _midpoint_parameters(size_option.grids)
        )
        base = reference_program()
        defected = edited_program(frozenset({size_edit}))

        assert np.array_equal(
            base.execute(Seed(9), 64).values[ARRIVAL],
            defected.execute(Seed(9), 64).values[ARRIVAL],
        )
        assert not np.array_equal(
            base.execute(Seed(9), 64).values[SIZE],
            defected.execute(Seed(9), 64).values[SIZE],
        )


# ==========================================================================
# A2  Edit soundness
# ==========================================================================


class TestA2EditSoundness:
    """Every grammar-valid edit compiles to an executable programme."""

    #: Parameter combinations attempted per structural cell. Structural cells are
    #: always covered exhaustively; within a cell the sample is deterministic and
    #: retains every corner of the grid box. The full space is 1.2 million edits,
    #: which cannot be executed in a test, so the coverage actually achieved is
    #: asserted below rather than left implicit.
    MAX_PER_STRUCTURE = 81
    N_EVENTS = 64

    @pytest.mark.parametrize("grammar_name", ["edit_grammar", "agent_grammar"])
    def test_a2_every_enumerated_edit_compiles_and_executes(
        self, grammar_name: str
    ) -> None:
        grammar = {"edit_grammar": edit_grammar, "agent_grammar": agent_grammar}[
            grammar_name
        ]()
        program = reference_program()
        checked = 0
        for edit in grammar.enumerate_edits(self.MAX_PER_STRUCTURE):
            edited = grammar.apply(program, frozenset({edit}))
            log = edited.execute(Seed(5), self.N_EVENTS)
            assert log.n_events == self.N_EVENTS
            times = log.values[ARRIVAL]
            assert np.all(np.isfinite(times))
            assert np.all(np.diff(times) > 0.0), f"non-increasing times for {edit}"
            checked += 1
        assert checked > 0

    def test_a2_structural_coverage_is_exhaustive(self) -> None:
        """Every structural cell of the ground-truth grammar is reachable."""
        grammar = edit_grammar()
        structures = list(grammar.structures())
        assert len(structures) == 6
        by_type = {edit_type.__name__ for edit_type, _, _ in structures}
        assert by_type == {
            "AddDependency",
            "AddLatentVariable",
            "ChangeDistributionFamily",
            "ReparameteriseComponent",
        }

    def test_a2_parameter_coverage_is_reported(self) -> None:
        """State the sampled fraction rather than implying the space was covered."""
        grammar = edit_grammar()
        total = grammar.edit_space_size()
        sampled = sum(1 for _ in grammar.enumerate_edits(self.MAX_PER_STRUCTURE))
        # Five three-parameter cells and one four-parameter cell.
        assert total == 5 * GRID_SIZE**3 + GRID_SIZE**4
        # Thinned to 4 points per grid for three parameters, 3 points for four.
        assert sampled == 5 * 4**3 + 3**4 == 401
        assert sampled < total

    def test_a2_off_grid_parameters_are_rejected(self) -> None:
        """A2's converse: values outside the enumerable space are not licensed."""
        grammar = edit_grammar()
        edit = ReparameteriseComponent(
            ARRIVAL,
            grammar.parameterisations[ARRIVAL][0].family,
            FrozenDict({"base_rate": 0.5, "amplitude": 1.4142, "period": 36.84}),
        )
        with pytest.raises(OffGridParameterError):
            grammar.validate(edit)
        assert not grammar.contains(edit)

    def test_a2_out_of_library_edit_is_in_exactly_one_grammar(self) -> None:
        """SPEC §3.2's mechanical out-of-library definition holds for S11."""
        assert edit_grammar().contains(SIZE_EXCITATION)
        assert not agent_grammar().contains(SIZE_EXCITATION)
        assert edit_grammar().contains(CONFOUNDED_MECHANISMS["hawkes"])
        assert agent_grammar().contains(CONFOUNDED_MECHANISMS["hawkes"])

    def test_a2_two_edits_on_one_target_are_rejected(self) -> None:
        """The compiled family would be ambiguous, so the defect is invalid."""
        grammar = edit_grammar()
        defect = frozenset(
            {CONFOUNDED_MECHANISMS["hawkes"], CONFOUNDED_MECHANISMS["seasonality"]}
        )
        with pytest.raises(Exception) as info:
            grammar.apply(reference_program(), defect)
        assert "two edits" in str(info.value)

    def test_a2_compound_defect_on_distinct_targets_executes(self) -> None:
        """Scenario S8's shape: seasonality on arrival plus a size mixture."""
        grammar = edit_grammar()
        size_option = grammar.families[SIZE][0]
        size_edit = ChangeDistributionFamily(
            SIZE,
            size_option.family,
            _midpoint_parameters(size_option.grids),
        )
        defect = frozenset({CONFOUNDED_MECHANISMS["seasonality"], size_edit})
        log = grammar.apply(reference_program(), defect).execute(Seed(2), 256)
        assert np.all(np.isfinite(log.values[OBS]))


def _midpoint_parameters(grids: tuple[ParameterGrid, ...]) -> FrozenDict[str, float]:
    return FrozenDict({grid.name: grid.values[grid.size // 2] for grid in grids})


# ==========================================================================
# A3  Distance metric
# ==========================================================================


def _random_edit(grammar: EditGrammar, rng: np.random.Generator) -> Edit:
    structures = list(grammar.structures())
    edit_type, target, option = structures[int(rng.integers(len(structures)))]
    values = {
        grid.name: grid.values[int(rng.integers(grid.size))] for grid in option.grids
    }
    parameters = FrozenDict[str, float](values)
    if isinstance(option, LatentOption):
        return AddLatentVariable(target, option.spec, parameters)
    if isinstance(option, FamilyOption):
        if edit_type is ChangeDistributionFamily:
            return ChangeDistributionFamily(target, option.family, parameters)
        return ReparameteriseComponent(target, option.family, parameters)
    return AddDependency(option.source, target, option.kernel, parameters)


def _random_defect(grammar: EditGrammar, rng: np.random.Generator) -> Defect:
    """Return a random licensed defect of size 0-3 with distinct targets."""
    size = int(rng.integers(0, 4))
    chosen: dict[ComponentId, Edit] = {}
    for _ in range(size):
        edit = _random_edit(grammar, rng)
        chosen[edit.target] = edit
    return frozenset(chosen.values())


def _wide_grammar() -> EditGrammar:
    """Return a synthetic grammar with six targetable components.

    The point-process grammar can only target ``arrival`` and ``size``, so its
    defects never exceed two edits and the partial-matching branch of
    ``distance`` is barely exercised. This grammar exists solely to drive the
    metric over defects large enough to make matching, insertion and deletion all
    matter at once. It is never executed, only measured.
    """
    grids = (
        ParameterGrid("alpha", 0.1, 10.0, 8, "log"),
        ParameterGrid("beta", 0.0, 1.0, 8, "linear"),
    )
    targets = [ComponentId(f"c{index}") for index in range(6)]
    families = FrozenDict[ComponentId, tuple[FamilyOption, ...]](
        {
            target: (
                FamilyOption(FamilyId(f"{target}_family_a"), grids),
                FamilyOption(FamilyId(f"{target}_family_b"), grids),
            )
            for target in targets
        }
    )
    return EditGrammar(
        version=GrammarVersion("synthetic/1.0.0"),
        allowed=frozenset({ChangeDistributionFamily}),
        families=families,
    )


class TestA3DistanceMetric:
    """``distance`` satisfies identity, symmetry and the triangle inequality."""

    N_PAIRS = 10_000
    TOLERANCE = 1e-9

    @pytest.mark.parametrize("builder", [edit_grammar, _wide_grammar])
    def test_a3_identity_symmetry_and_triangle_on_10k_pairs(
        self, builder: Callable[[], EditGrammar]
    ) -> None:
        grammar = builder()
        rng = np.random.default_rng(20240801)
        previous: Defect | None = None
        checked = 0
        for _ in range(self.N_PAIRS):
            left = _random_defect(grammar, rng)
            right = _random_defect(grammar, rng)
            forward = grammar.distance(left, right)
            backward = grammar.distance(right, left)

            assert forward >= -self.TOLERANCE
            assert abs(forward - backward) <= self.TOLERANCE, "symmetry"
            assert grammar.distance(left, left) == pytest.approx(
                0.0, abs=self.TOLERANCE
            )
            if left == right:
                assert forward == pytest.approx(0.0, abs=self.TOLERANCE)
            else:
                assert forward > self.TOLERANCE, "identity of indiscernibles"

            if previous is not None:
                direct = grammar.distance(previous, right)
                via = grammar.distance(previous, left) + forward
                assert direct <= via + self.TOLERANCE, (
                    f"triangle inequality violated: d(a,c)={direct} > "
                    f"d(a,b)+d(b,c)={via}"
                )
            previous = left
            checked += 1
        assert checked == self.N_PAIRS

    @settings(
        max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(seeds=st.tuples(*[st.integers(0, 2**32 - 1)] * 3))
    def test_a3_triangle_inequality_property(self, seeds: tuple[int, int, int]) -> None:
        """Property-based restatement of the triangle inequality."""
        grammar = edit_grammar()
        a, b, c = (_random_defect(grammar, np.random.default_rng(s)) for s in seeds)
        assert grammar.distance(a, c) <= (
            grammar.distance(a, b) + grammar.distance(b, c) + self.TOLERANCE
        )

    def test_a3_distance_is_bounded_by_edit_count(self) -> None:
        """One wholly missing edit costs exactly one unit."""
        grammar = edit_grammar()
        hawkes = mechanism_defect("hawkes")
        assert grammar.distance(frozenset(), hawkes) == pytest.approx(1.0)
        assert grammar.distance(hawkes, frozenset()) == pytest.approx(1.0)

    def test_a3_ordering_reflects_structural_similarity(self) -> None:
        """A near-miss on parameters must cost less than the wrong mechanism.

        Not required by A3, which only asks for a metric, but a metric that
        ranked these the other way would make D1 meaningless.
        """
        grammar = edit_grammar()
        hawkes = CONFOUNDED_MECHANISMS["hawkes"]
        nudged = replace(
            hawkes,
            parameters=FrozenDict(
                dict(hawkes.parameters)
                | {"decay": _neighbour(grammar, hawkes, "decay")}
            ),
        )
        near = grammar.distance(frozenset({hawkes}), frozenset({nudged}))
        wrong_mechanism = grammar.distance(
            frozenset({hawkes}), mechanism_defect("seasonality")
        )
        missing = grammar.distance(frozenset({hawkes}), frozenset())
        assert 0.0 < near < missing <= wrong_mechanism


def _neighbour(grammar: EditGrammar, edit: Edit, name: str) -> float:
    grid = next(g for g in grammar.option_of(edit).grids if g.name == name)
    index = grid.index(edit.parameters[name])
    return grid.values[index + 1 if index + 1 < grid.size else index - 1]


# ==========================================================================
# A4  Prefix code
# ==========================================================================


OptionT = TypeVar("OptionT", FamilyOption, LatentOption, DependencyOption)


def _shrink_grids(
    grids: tuple[ParameterGrid, ...], size: int
) -> tuple[ParameterGrid, ...]:
    return tuple(replace(grid, size=size) for grid in grids)


def _shrink_options[OptionT: (FamilyOption, LatentOption, DependencyOption)](
    table: Mapping[ComponentId, tuple[OptionT, ...]], size: int
) -> FrozenDict[ComponentId, tuple[OptionT, ...]]:
    return FrozenDict[ComponentId, tuple[OptionT, ...]](
        {
            target: tuple(
                replace(option, grids=_shrink_grids(option.grids, size))
                for option in options
            )
            for target, options in table.items()
        }
    )


def _shrink(grammar: EditGrammar, size: int) -> EditGrammar:
    """Return ``grammar`` with every parameter grid reduced to ``size`` points.

    Structurally identical, so the Kraft argument is unchanged, but small enough
    that the entire defect space can be materialised and summed.
    """
    return replace(
        grammar,
        families=_shrink_options(grammar.families, size),
        parameterisations=_shrink_options(grammar.parameterisations, size),
        latent_specs=_shrink_options(grammar.latent_specs, size),
        dependencies=_shrink_options(grammar.dependencies, size),
    )


class TestA4PrefixCode:
    """``code_length`` satisfies Kraft's inequality over the enumerable space."""

    @pytest.mark.parametrize("grammar_name", ["edit_grammar", "agent_grammar"])
    def test_a4_kraft_inequality_over_single_edit_space(
        self, grammar_name: str
    ) -> None:
        grammar = {"edit_grammar": edit_grammar, "agent_grammar": agent_grammar}[
            grammar_name
        ]()
        total = grammar.kraft_sum_single_edits()
        assert total <= 1.0 + 1e-12, f"Kraft violated: sum = {total}"
        assert total == pytest.approx(1.0, abs=1e-12), (
            "every level of the code is uniform, so the single-edit space should "
            f"exhaust the budget exactly; got {total}"
        )

    def test_a4_grouped_sum_matches_brute_force_enumeration(self) -> None:
        """The grouped count is verified against a materialised sum.

        ``kraft_sum_single_edits`` counts parameter combinations rather than
        instantiating them. On a two-point-grid variant of the same grammar the
        space is small enough to instantiate every edit and sum ``2**-L``
        directly; the two must agree.
        """
        grammar = _shrink(edit_grammar(), 2)
        brute = math.fsum(
            2.0 ** -grammar.edit_code_length(edit)
            for edit in grammar.enumerate_edits(None)
        )
        assert brute == pytest.approx(grammar.kraft_sum_single_edits(), abs=1e-12)
        assert brute <= 1.0 + 1e-12

    def test_a4_kraft_inequality_over_full_defect_space(self) -> None:
        """The whole space of defects, not just single edits, respects Kraft.

        Enumerated exhaustively on the two-point-grid grammar: every defect of
        every size the grammar admits, which is every subset of the edit space
        with distinct targets.
        """
        grammar = _shrink(edit_grammar(), 2)
        edits = list(grammar.enumerate_edits(None))
        total = 0.0
        counted = 0
        max_size = len({edit.target for edit in edits})
        for size in range(max_size + 1):
            for combination in combinations(edits, size):
                if len({edit.target for edit in combination}) != size:
                    continue
                total += 2.0 ** -grammar.code_length(frozenset(combination))
                counted += 1

        # The whole space, counted independently: the null defect, every single
        # edit, and every (arrival, size) pair. Two targets, so nothing larger.
        on_arrival = sum(1 for edit in edits if edit.target == ARRIVAL)
        on_size = sum(1 for edit in edits if edit.target == SIZE)
        assert max_size == 2
        assert counted == 1 + len(edits) + on_arrival * on_size == 441
        assert total <= 1.0 + 1e-12, f"Kraft violated over defects: sum = {total}"

    def test_a4_length_prefix_is_complete(self) -> None:
        """Elias gamma over ``|D| + 1`` exhausts exactly one unit of budget."""
        total = math.fsum(
            2.0 ** -(2 * (k + 1).bit_length() - 1) for k in range(0, 2**16)
        )
        assert total <= 1.0 + 1e-12
        assert total == pytest.approx(1.0, abs=1e-4)

    def test_a4_null_defect_is_the_cheapest_code(self) -> None:
        """Scenario S9's null must be the a-priori most probable hypothesis."""
        grammar = edit_grammar()
        assert grammar.code_length(frozenset()) == 1.0
        for name in CONFOUNDED_MECHANISMS:
            assert grammar.code_length(mechanism_defect(name)) > 1.0

    def test_a4_extra_parameters_cost_bits(self) -> None:
        """The parsimony prior's whole content: more parameters, longer code.

        Under the agent grammar both cells hold exactly one construct, so the
        only difference between regime switching and Hawkes is that the former
        has a fourth parameter: exactly ``log2(GRID_SIZE)`` bits, so a factor of
        GRID_SIZE in prior probability.

        Under the ground-truth grammar the gap is 4 bits instead, because there
        the Hawkes cell holds two dependency constructs and naming one of them
        costs an extra bit. That is the grammar-relativity of the code, not an
        inconsistency.
        """
        hawkes = mechanism_defect("hawkes")
        regime = mechanism_defect("regime_switching")

        agent = agent_grammar()
        assert agent.code_length(regime) - agent.code_length(hawkes) == pytest.approx(
            math.log2(GRID_SIZE), abs=1e-12
        )

        ground = edit_grammar()
        assert ground.code_length(regime) - ground.code_length(hawkes) == pytest.approx(
            math.log2(GRID_SIZE) - 1.0, abs=1e-12
        )

    def test_a4_code_length_is_grammar_relative(self) -> None:
        """Documented consequence: the two grammars induce different priors."""
        hawkes = mechanism_defect("hawkes")
        assert edit_grammar().code_length(hawkes) > agent_grammar().code_length(hawkes)


# ==========================================================================
# A5  Collateral derivation
# ==========================================================================


class TestA5Descendants:
    """``descendants`` matches hand-computed reachability on the reference DAG."""

    def test_a5_reference_dag_reachability(self) -> None:
        """Edges: arrival->size, arrival->sign, size->obs, sign->obs.

        By hand: arrival reaches size and sign directly and obs through either;
        size and sign reach obs only; obs reaches nothing.
        """
        program = reference_program()
        assert program.descendants(ARRIVAL) == frozenset({SIZE, SIGN, OBS})
        assert program.descendants(SIZE) == frozenset({OBS})
        assert program.descendants(SIGN) == frozenset({OBS})
        assert program.descendants(OBS) == frozenset()

    def test_a5_hawkes_self_loop_makes_arrival_its_own_descendant(self) -> None:
        """A history edge arrival->arrival is reachability, not a contradiction.

        Collateral for an intervention on arrival is ``descendants - {arrival}``,
        so the self-loop does not corrupt the collateral set; it records that a
        forced arrival changes later arrivals, which is exactly the effect the
        ``ForceArrival`` intervention exploits.
        """
        program = edited_program(mechanism_defect("hawkes"))
        assert program.descendants(ARRIVAL) == frozenset({ARRIVAL, SIZE, SIGN, OBS})
        assert program.descendants(ARRIVAL) - {ARRIVAL} == frozenset({SIZE, SIGN, OBS})
        assert program.descendants(SIZE) == frozenset({OBS})

    def test_a5_size_excitation_closes_a_lagged_loop(self) -> None:
        """Scenario S11: size -> arrival makes every component reach every other.

        By hand: size reaches arrival by the new lagged edge, arrival reaches
        size and sign, both reach obs; so size's descendants are all four
        components including itself.
        """
        program = edited_program(frozenset({SIZE_EXCITATION}))
        assert program.descendants(SIZE) == frozenset({ARRIVAL, SIZE, SIGN, OBS})
        assert program.descendants(ARRIVAL) == frozenset({ARRIVAL, SIZE, SIGN, OBS})
        assert program.descendants(SIGN) == frozenset({OBS})
        assert program.descendants(OBS) == frozenset()

    def test_a5_non_dependency_edits_do_not_change_reachability(self) -> None:
        """Only ``AddDependency`` touches the graph; the other three do not."""
        expected = reference_program().descendants(ARRIVAL)
        for name in ("regime_switching", "seasonality", "poisson_mixture"):
            program = edited_program(mechanism_defect(name))
            assert program.descendants(ARRIVAL) == expected, name

    def test_a5_instantaneous_cycles_are_still_rejected(self) -> None:
        """The lagged exemption must not weaken acyclicity for instant edges."""
        program = reference_program()
        with pytest.raises(CyclicDependencyError):
            GenerativeProgram(
                components=program.components,
                edges=program.edges | {(OBS, ARRIVAL)},
                library=program.library,
                history_edges=frozenset(),
            )

    def test_a5_execution_order_is_a_topological_sort(self) -> None:
        order = reference_program().order()
        position = {component: index for index, component in enumerate(order)}
        for source, target in reference_program().instantaneous_edges:
            assert position[source] < position[target]
        assert order[0] == ARRIVAL
        assert order[-1] == OBS

    def test_a5_canonical_order_is_independent_of_set_iteration(self) -> None:
        """Defects are sets; their encoding and application order must not be."""
        edits = [CONFOUNDED_MECHANISMS["hawkes"], _size_mixture_edit()]
        assert canonical(frozenset(edits)) == canonical(frozenset(reversed(edits)))


def _size_mixture_edit() -> Edit:
    option = edit_grammar().families[SIZE][0]
    return ChangeDistributionFamily(
        SIZE, option.family, _midpoint_parameters(option.grids)
    )
