"""Acceptance test A33: the verification substrate is versioned.

A33 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The verification substrate has unversioned
randomness, caches and dependencies"*, and reads:

    ``test_a33_the_substrate_is_versioned`` -- the Hypothesis profile is
    registered and in force under pytest; a table cached under one
    simulator-code digest is refused under another; the rendered report names
    the numpy version.

What was wrong
--------------

Three holes in the substrate the *rest* of the verification stands on. Each is a
case where something that should move a result silently does not, so nothing in
the suite reports the difference and every gate stays green across it.

**The property gates certify one random sample per run.** Six ``@given`` tests
carry the only unbounded search in the repository, on a project whose third
invariant is that all randomness is seeded and explicit. Hypothesis defaulted to
its own database under ``.hypothesis/``, which writes a ``.gitignore`` holding
``*`` into itself -- so a counterexample found on one machine died with that
working tree and no other checkout ever saw it.

**A mechanism bugfix reuses stale tables.** ``cache_key`` mixed ``ENV_VERSION``
into the address of a cached table, and ``ENV_VERSION`` is three hand-maintained
version literals. A change to what a mechanism *does*, landed without someone
remembering to bump one of them, leaves every 2000-replicate table on disk
readable -- across every worktree, since they share one cache directory by
design. ``environments/pointproc/tables.py`` said so in as many words and called
it "a pre-existing limitation the environment protocol is meant to close".

**A dependency bump moves every number invisibly.** ``pyproject`` declares
``numpy>=2.1``, a lower bound. Bit-exact determinism rides numpy's generator
bit-stream stability, and the registry content-addresses over (env version,
config, data version, metric version, seed) with no dependency term. A
``uv lock --upgrade`` could therefore move every reported figure while every
content address stayed fixed -- the same confusion the Windows pin exists to
prevent, reopened through the lockfile.

The reading this gate encodes
-----------------------------

**The profile samples randomly and keeps a committed corpus**, rather than
derandomising. The backlog entry asked for ``derandomize`` *and* a committed
example database; Hypothesis refuses that pairing outright::

    InvalidArgument: derandomize=True implies database=None, so passing
    database=DirectoryBasedExampleDatabase(...) too is invalid.

So it is one or the other, and the entry's own parenthetical -- "or a recorded
seed printed on failure" -- names the branch taken here. Derandomising would
freeze the sample: every machine would try the identical examples forever, which
buys cross-machine agreement by giving up the widening that makes a property
test worth more than a fixed one. Random sampling plus a *tracked* corpus keeps
the widening and still carries a counterexample between machines, because the
minimal failing example is written into ``tests/regressions/`` and committed.
``print_blob`` supplies the recorded-seed half: a failure carries a
``@reproduce_failure`` decorator in its notes, which pytest prints.

Safe to track, and measured rather than assumed: a *passing* run writes zero
files into the database. Only a failure writes, so the corpus does not churn the
working tree and cannot make ``suite-freshness.sh record`` refuse.

**The table cache is keyed on the environment's source, not on a version
literal.** ``SIMULATOR_DIGEST`` hashes every ``.py`` in
``environments/pointproc/`` and enters ``cache_key`` beside ``ENV_VERSION``. Two
choices in it are load-bearing and are tested here rather than argued:

*Every module in the package, not a curated subset.* A hand-kept list of "the
modules that decide a row" rots in exactly the way ``ENV_VERSION`` already does,
which is the defect being closed. A false miss costs a rebuild; a false hit
costs the guarantee. That asymmetry is ``suite-freshness.sh``'s own stated
principle, applied one door further along.

*Line endings normalised before hashing.* The cache is shared across worktrees,
and ``docs/DECISIONS.md`` records a measured CRLF/LF divergence between the main
tree and fresh checkouts at one commit. Hashing raw bytes would give those trees
different digests and permanently cold caches -- reintroducing the 181x cold
penalty that sharing exists to avoid.

**The numpy version is carried on the report, and is required.** Beside
``platform`` and for the same reason the module's own "What a report must say,
and therefore cannot default" section gives: the ledger has no such column and
cannot grow one without retiring every stored row, so it has to be told. It is
*not* read from ``numpy.__version__`` at summarise time -- a report may be
rendered on a machine other than the one that filled the ledger, and deriving it
locally would print a confident fact about the wrong process.

What this gate does not close
-----------------------------

Nothing here pins numpy. ``pyproject`` still declares a lower bound, and a
``uv lock --upgrade`` still moves the resolved version. What changes is that the
move becomes *visible* in the artefact rather than silent: two reports built
under different numpy versions no longer render identically. Pinning is a
separate decision about dependency policy and is not this gate's to take.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.database import DirectoryBasedExampleDatabase

import environments.pointproc.tables as tables
from environments.pointproc.outcomes import held_out_designs
from environments.pointproc.tables import (
    CLOSED_SET,
    SIMULATOR_DIGEST,
    cache_key,
    cached_table,
    simulator_digest,
    table_probe,
)
from sciagent.core.errors import MalformedDesignError, TableError
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    GrammarVersion,
    MetricVersion,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.eval.agency import AgencyMetrics
from sciagent.eval.campaign import Adjudication
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    battery_key,
    cell_key,
)
from sciagent.eval.report import MatrixReport, render, summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import ClosedWorldScore, DimensionVector
from sciagent.registry.ledger import LedgerEntry
from sciagent.registry.partitions import DataPartition

ROOT = Path(__file__).resolve().parents[2]


#: A ``@given`` site decorated **when this module is imported**, which is the
#: only instant that answers the question clause 1 asks.
#:
#: ``@given`` snapshots ``settings.default`` at decoration time, and the six real
#: property sites are decorated during collection. A profile loaded any later --
#: from an autouse session fixture, say -- therefore governs none of them while
#: leaving ``settings()`` reporting the profile perfectly, because ``settings()``
#: is read at call time. A probe decorated inside a test body measures that
#: post-load state and cannot tell the two implementations apart; this one is
#: decorated at the same instant the real sites are, and can.
@settings(max_examples=1)
@given(st.integers())
def _decorated_at_import(value: int) -> None:
    assert isinstance(value, int)


def _modules(root: Path) -> list[Path]:
    """Return the ``.py`` files under ``root``, selected as the digest selects.

    Mirrors :func:`~environments.pointproc.tables.simulator_digest` deliberately:
    ``rglob("*.py")`` matches case-insensitively on Windows, so a test using it
    against a digest that compares ``path.suffix`` would disagree with the thing
    it is testing, on exactly the platform the digest was corrected for.
    """
    return [path for path in root.rglob("*") if path.is_file() and path.suffix == ".py"]


def _copy_package(into: Path) -> Path:
    """Copy the environment package's modules to ``into``, preserving structure.

    Recursive, because :func:`simulator_digest` is: a flat copy would agree with
    a recursive digest today, when the package happens to have no subpackage,
    and diverge silently the moment one is added.
    """
    for module in sorted(_modules(PACKAGE)):
        target = into / module.relative_to(PACKAGE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(module.read_bytes())
    return into


def _snapshot(test: object) -> settings:
    """Return the settings a ``@given`` test captured when it was decorated.

    Reached through ``vars`` rather than attribute access because Hypothesis
    publishes no accessor for it, and ``getattr`` with a literal name is what
    ruff's B009 exists to stop.
    """
    captured = vars(test).get("_hypothesis_internal_use_settings")
    assert isinstance(captured, settings)
    return captured


#: Where the committed corpus lives. The directory the profile's database is
#: rooted at, named here independently of ``tests/conftest.py`` so that a test
#: of "the profile points at the tracked directory" compares two statements
#: rather than one statement with itself.
REGRESSIONS = ROOT / "tests" / "regressions"

#: The environment package whose source the cache digest covers.
PACKAGE = ROOT / "src" / "environments" / "pointproc"

BATTERY = held_out_designs()
PLATFORM = "Windows-11-x86_64"
NUMPY = "2.5.1"
GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")

#: A version string carrying a newline, which ``render`` would lay out as a
#: second header line indistinguishable from one the framework computed.
_FORGED = "pointproc/1.0.0\n  platform        forged"

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("pointproc/generated/1.0.0"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)


def _reading() -> CellReading:
    """Return a reading carrying stated numbers and nothing derived.

    Every field is fixed rather than scored: this file tests what a report
    *names*, not what it computes, so a reading that varied would only add a way
    for these tests to fail for reasons that are not A33's.
    """
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=1.0,
            d2_held_out_predictive=-2.0,
            d3_intervention_similarity=0.5,
            d4_explanatory_coverage=0.25,
            d5_enabled_experiment_value=0.75,
            d6_complexity=12.0,
            n_held_out=3,
        ),
        score=ClosedWorldScore(
            truth_mass=Probability(0.5),
            log_score=-1.0,
            leading_mass=Probability(0.5),
            correct=True,
            identified=False,
        ),
        ppc_p_value=0.2,
        inadequate=False,
        probe_p_value=0.03,
        probe_inadequate=False,
        agency=AgencyMetrics(
            system="V1",
            scenario=ScenarioId("S1"),
            experiments=8,
            entertained=4,
            escalated=0,
            proposals=None,
            causes=None,
        ),
        adjudication=Adjudication(
            claims=80, adjudicated=80, contradictions=0, zombies=0
        ),
        null_mass=Probability(0.25),
        abstain_mass=Probability(0.5),
        max_defect_mass=0.4,
        experiments=8,
        structural_distance=1.0,
        battery=battery_key(BATTERY),
    )


def _rows(replicates: int = 2) -> tuple[LedgerEntry, ...]:
    """Return ``replicates`` ledger rows for one cell at :data:`ADDRESS`."""
    cell = Cell("V1", ScenarioId("S1"), replicates)
    return tuple(
        LedgerEntry(
            key=cell_key(
                CellTask(cell=cell, replicate=index, seed=Seed(index)),
                ADDRESS,
                battery=BATTERY,
            ),
            reading=FrozenDict[str, float](dict(_reading().as_payload())),
            sequence=index,
        )
        for index in range(replicates)
    )


def _summarise(
    *, numpy_version: str = NUMPY, address: CampaignAddress = ADDRESS
) -> MatrixReport:
    """Summarise a one-cell campaign with the provenance a report needs."""
    fallback: ScenarioClass = "single"
    return summarise(
        _rows(),
        address=address,
        scenario_class=lambda _target: fallback,
        battery=lambda _target: BATTERY,
        platform=PLATFORM,
        numpy_version=numpy_version,
        grammar=GRAMMAR,
    )


class TestA33SubstrateVersioning:
    """A33: randomness, caches and dependencies each carry a version."""

    # ----------------------------------------------------------------------
    # Clause 1: the Hypothesis profile is registered and in force
    # ----------------------------------------------------------------------

    def test_a33_the_hypothesis_profile_is_in_force_under_pytest(self) -> None:
        """The active profile is this project's, not Hypothesis's default.

        ``settings()`` reports the *currently active* defaults, so this asks
        whether ``load_profile`` actually ran under pytest -- not merely whether
        ``register_profile`` was called somewhere. Registering without loading is
        the silent failure this distinguishes: every setting below would be
        Hypothesis's own and nothing else in the suite would notice.
        """
        active = settings()
        # Not derandomised, deliberately. See this module's docstring: the
        # committed corpus is the branch taken, and Hypothesis forbids both.
        assert active.derandomize is False
        # A failing property must carry a reproduction that survives the process.
        assert active.print_blob is True
        # These properties simulate; a 200ms deadline is not a statement about
        # correctness and would fail on load rather than on a counterexample.
        assert active.deadline is None

    def test_a33_the_profile_database_is_the_tracked_corpus(self) -> None:
        """The database is a directory in the repository, not ``.hypothesis/``.

        Hypothesis's default database writes under ``.hypothesis/``, which holds a
        ``.gitignore`` containing ``*`` -- so its contents can never be committed
        and a counterexample cannot leave the machine that found it. Asserting the
        *type and location* is what separates "a database exists" from "a
        counterexample reaches the repo".
        """
        database = settings().database
        # The type alone decides nothing and is asserted for readability only:
        # Hypothesis's own default is a `_StorageDirectoryDatabase`, which is a
        # *subclass* of this, so the default under `.hypothesis/` satisfies the
        # isinstance. The path is the discriminating half.
        assert isinstance(database, DirectoryBasedExampleDatabase)
        assert Path(database.path).resolve() == REGRESSIONS.resolve()

    def test_a33_a_property_decorated_at_import_carries_the_profile(self) -> None:
        """The profile is loaded early enough to govern the six real gates.

        This is the assertion that separates "in force" from "in force by the
        time somebody asks". ``@given`` captures ``settings.default`` when the
        decorator runs, so a profile loaded from an autouse session fixture --
        after collection -- leaves every ``settings()`` reading correct while the
        six real property sites keep Hypothesis's defaults and write their
        counterexamples into ``.hypothesis/``, which is self-ignoring. The
        distinction is invisible to anything decorated inside a test body.

        :data:`_decorated_at_import` is decorated at the same instant they are.
        """
        captured = _snapshot(_decorated_at_import)
        database = captured.database
        assert isinstance(database, DirectoryBasedExampleDatabase)
        # Again the path, not the type: the default is a subclass.
        assert Path(database.path).resolve() == REGRESSIONS.resolve()
        assert captured.print_blob is True

    def test_a33_the_corpus_directory_is_tracked(self) -> None:
        """The corpus directory is in the index, not merely on disk.

        Git tracks files rather than directories, so a corpus directory that
        exists only locally would satisfy every assertion above and still carry
        nothing between machines. The check is against the index for the same
        reason ``test_a38_every_named_path_is_tracked`` shells out: a path present
        on disk and absent from the index is exactly the state that looks correct.
        """
        assert REGRESSIONS.is_dir()
        tracked = subprocess.run(
            ["git", "ls-files", "tests/regressions"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert tracked, "tests/regressions holds no tracked file"

    # ----------------------------------------------------------------------
    # Clause 2: a table cached under one simulator digest is refused under
    # another
    # ----------------------------------------------------------------------

    def test_a33_a_table_cached_under_one_digest_is_refused_under_another(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A moved simulator digest is a cache miss, not a stale read.

        Both halves are needed and the second is why the first means anything: a
        cache that is never consulted would pass the miss assertion trivially. So
        this pins that an unchanged digest *does* hit -- one file after two calls
        -- and that a moved one does not.
        """
        monkeypatch.setattr(tables, "CACHE", tmp_path)
        defects = [CLOSED_SET["null"]]

        first = cached_table(defects, replicates=2, seed=Seed(1), label="a33")
        assert len(list(tmp_path.glob("*.json"))) == 1

        # Same digest: the cached file is read, and nothing new is written.
        again = cached_table(defects, replicates=2, seed=Seed(1), label="a33")
        assert len(list(tmp_path.glob("*.json"))) == 1
        assert again.counts == first.counts

        # A moved digest addresses a different file, so the stale one is
        # unreachable rather than merely wrong.
        monkeypatch.setattr(tables, "SIMULATOR_DIGEST", "0" * 12)
        cached_table(defects, replicates=2, seed=Seed(1), label="a33")
        assert len(list(tmp_path.glob("*.json"))) == 2

    def test_a33_the_digest_is_derived_from_the_package_source(
        self, tmp_path: Path
    ) -> None:
        """The digest is a function of the source, and moves when the source does.

        Without this every other assertion in this clause is satisfiable by a
        constant: a literal ``SIMULATOR_DIGEST = "abc"`` keys the cache perfectly
        well and closes nothing. Recomputed over a *copy* of the package so that
        the comparison is against real source bytes rather than against a second
        call of the same expression.
        """
        copy = _copy_package(tmp_path / "pointproc")

        assert simulator_digest(copy) == SIMULATOR_DIGEST

        edited = copy / "mechanisms.py"
        edited.write_bytes(edited.read_bytes() + b"\n# a mechanism moved\n")
        assert simulator_digest(copy) != SIMULATOR_DIGEST

    def test_a33_every_module_in_the_package_enters_the_digest(
        self, tmp_path: Path
    ) -> None:
        """No module in the package is outside the digest.

        The module docstring above argues for hashing the whole package rather
        than a curated subset, because a hand-kept list of "the modules that
        decide a row" rots exactly as ``ENV_VERSION`` does. Without this test
        that is an argument and not a property: a digest over ``mechanisms.py``
        alone satisfies every other assertion in this clause, since that is where
        the rationale's example bugfix lands.

        Each module is edited in turn and restored, so a failure names the one
        module that is invisible rather than reporting that some module is.
        """
        copy = _copy_package(tmp_path / "pointproc")

        baseline = simulator_digest(copy)
        modules = sorted(_modules(copy))
        assert len(modules) > 1
        for module in modules:
            original = module.read_bytes()
            module.write_bytes(original + b"\n# moved\n")
            moved = simulator_digest(copy)
            module.write_bytes(original)
            assert moved != baseline, f"{module.name} does not enter the digest"
            assert simulator_digest(copy) == baseline

    def test_a33_the_digest_enters_the_cache_key_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The digest is mixed in at ``cache_key``, not at one of its callers.

        ``cache_key`` is the single door every cached table is addressed through,
        and it has three callers -- :func:`cached_table`, the search table and
        the matrix table. A digest folded into one caller's filename would leave
        the other two reading stale files while the clause-2 test above, which
        goes through :func:`cached_table`, stayed green.
        """
        probe = table_probe(2, Seed(1))
        before = cache_key(probe, "structures")
        monkeypatch.setattr(tables, "SIMULATOR_DIGEST", "0" * 12)
        assert cache_key(probe, "structures") != before

    def test_a33_the_digest_ignores_line_endings(self, tmp_path: Path) -> None:
        """Two checkouts differing only in line endings agree on the digest.

        Not hypothetical. ``suite-freshness.sh`` records the case that motivated
        it: the main tree held 71 of 140 tracked files as CRLF while a fresh
        worktree checkout obeyed ``.gitattributes`` and held LF, at the same
        commit, with ``git status`` clean throughout. Hashing raw bytes would give
        those two trees different digests and a permanently cold shared cache --
        the 181x penalty the sharing exists to remove.
        """
        lf = _copy_package(tmp_path / "lf")
        crlf = _copy_package(tmp_path / "crlf")
        for module in sorted(_modules(lf)):
            source = module.read_bytes().replace(b"\r\n", b"\n")
            module.write_bytes(source)
            (crlf / module.relative_to(lf)).write_bytes(source.replace(b"\n", b"\r\n"))

        assert simulator_digest(lf) == simulator_digest(crlf)

    def test_a33_the_digest_does_not_depend_on_case_folded_ordering(
        self, tmp_path: Path
    ) -> None:
        """The module order feeding the hash is ordinal, not the platform's.

        ``PurePath.__lt__`` compares through ``os.path.normcase``, which
        lower-cases on Windows and is a no-op on POSIX, so sorting ``Path``
        objects feeds the same file set to sha256 in a different order on the two
        platforms — an iteration-order dependence reaching a content address,
        which the third invariant forbids.

        The fixture is deliberately **mixed case**. Every module in the real
        package is lowercase, so case folding is a no-op over it and a digest
        computed from the package alone cannot tell the two sort keys apart; a
        test built on a copy of the package would have inherited the defect
        rather than exposed it. This is the whole reason the digest is recursive:
        ``rglob`` exists to anticipate modules that do not exist yet.

        Asserted against the ordinal order computed here, rather than against a
        second call of the implementation, so the expected value comes from
        somewhere other than the thing under test.
        """
        root = tmp_path / "mixed"
        root.mkdir()
        (root / "sub").mkdir()
        contents = {
            "Zebra.py": b"z = 1\n",
            "apple.py": b"a = 2\n",
            "sub/Helper.py": b"h = 3\n",
            "sub/abstract.py": b"b = 4\n",
        }
        for name, body in contents.items():
            (root / name).write_bytes(body)

        # The fixture must actually discriminate, or the assertion below passes
        # against either sort key and tests nothing.
        #
        # `PureWindowsPath`, not `Path`, and that is not a stylistic choice.
        # `Path` *is* the defective comparator only on Windows: on POSIX
        # `normcase` is the identity, so `sorted(key=Path)` equals the ordinal
        # sort there, this guard would be `x != x`, and the test would fail on
        # Ubuntu -- which `.github/workflows/check.yml` runs and cloud sessions
        # use. Naming the Windows flavour explicitly asks the question the guard
        # means on both platforms.
        assert sorted(contents, key=PureWindowsPath) != sorted(contents)

        expected = hashlib.sha256()
        for name in sorted(contents):
            for field in (name.encode("utf-8"), contents[name]):
                expected.update(len(field).to_bytes(8, "big"))
                expected.update(field)
        assert simulator_digest(root) == expected.hexdigest()[:12]

    def test_a33_the_digest_frames_each_field_unambiguously(
        self, tmp_path: Path
    ) -> None:
        """Two different trees cannot produce one digest.

        The framing was ``name + NUL + source + NUL``, which is not injective: a
        filename cannot hold a NUL, but the file set is chosen by *suffix* and
        not by parsability, so one ``.py`` file holding arbitrary bytes can
        reproduce the byte stream of two. The exact collision below was
        demonstrated by ``/preflight``'s ordering lens against that framing.

        CPython refuses to compile source containing a NUL, so no importable
        module can reach this — but the function's guarantee is stated
        unconditionally, and a guarantee true only of inputs one happens to feed
        it is not one a content address can rest on.
        """
        one = tmp_path / "one"
        two = tmp_path / "two"
        one.mkdir()
        two.mkdir()
        (one / "a.py").write_bytes(b"x=1\x00y.py\x00z=2\n")
        (two / "a.py").write_bytes(b"x=1")
        (two / "y.py").write_bytes(b"z=2\n")

        assert simulator_digest(one) != simulator_digest(two)

    def test_a33_the_file_set_does_not_depend_on_extension_case(
        self, tmp_path: Path
    ) -> None:
        """An uppercase extension is in or out of the digest on both platforms.

        ``rglob("*.py")`` matches case-*insensitively* on Windows and
        case-sensitively on POSIX, so ``Model.PY`` would enter the digest on one
        and not the other — the same platform divergence as the sort key, moved
        from the ordering to the membership. Selecting on ``path.suffix`` is an
        exact comparison and answers identically everywhere.

        The assertion is that ``.PY`` is **excluded**, matching what POSIX
        Python itself would import.
        """
        # The discriminator, and the reason it names the Windows flavour
        # explicitly: on POSIX `rglob("*.py")` would not have matched `Model.PY`
        # either, so without this the test passes against the defective
        # implementation on CI's ubuntu-24.04 and asserts nothing. Stating the
        # Windows glob semantics as a fact makes the test's subject visible on
        # both platforms, exactly as its sibling ordering test does.
        assert PureWindowsPath("Model.PY").match("*.py")
        assert not PurePosixPath("Model.PY").match("*.py")

        root = tmp_path / "ext"
        root.mkdir()
        (root / "lower.py").write_bytes(b"a = 1\n")
        without = simulator_digest(root)

        (root / "Model.PY").write_bytes(b"b = 2\n")
        assert simulator_digest(root) == without

        (root / "included.py").write_bytes(b"c = 3\n")
        assert simulator_digest(root) != without

    def test_a33_an_empty_root_is_refused_rather_than_digested(
        self, tmp_path: Path
    ) -> None:
        """A root holding no module raises instead of hashing nothing.

        The sha256 of an empty input is a perfectly valid 64-character digest --
        ``e3b0c442...`` -- and it is the *same* one every time. So a missing or
        wrongly-rooted directory would silently reduce :func:`cache_key` to what
        it was before this gate, ``ENV_VERSION`` alone, and every stale table on
        disk would become readable again with nothing to say so.

        ``.claude/hooks/suite-freshness.sh`` guards the identical shape with its
        ``MIN_FILES`` check and spells out why: "sha256 of an empty input is
        still 64 characters ... only the file count catches it". A wrongly-rooted
        digest is that failure one door along, and it fails toward *refusing* for
        the same reason.
        """
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(TableError, match="digest of nothing"):
            simulator_digest(empty)
        with pytest.raises(TableError):
            simulator_digest(tmp_path / "does-not-exist")

    def test_a33_the_corpus_is_exempt_from_line_ending_normalisation(self) -> None:
        """Git does not rewrite the corpus on its way into the object store.

        The corpus holds opaque binary from Hypothesis's ``choices_to_bytes``.
        ``.gitattributes`` applies ``* text=auto eol=lf`` repository-wide, under
        which git *guesses*: an entry containing no NUL byte is judged text and
        its ``\\r\\n`` is rewritten to ``\\n``. ``choices_from_bytes`` then rejects
        the mangled entry and Hypothesis **deletes** it -- so the counterexample
        would vanish on the machine that pulled it, silently, which is precisely
        what committing a corpus is for.

        Asserted through ``git check-attr`` rather than by reading
        ``.gitattributes``, so the test answers the question git will actually be
        asked rather than the one the file appears to answer.
        """
        attribute = subprocess.run(
            ["git", "check-attr", "text", "--", "tests/regressions/abcd1234/entry"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert attribute.endswith("text: unset"), attribute

    # ----------------------------------------------------------------------
    # Clause 3: the rendered report names the numpy version
    # ----------------------------------------------------------------------

    def test_a33_the_rendered_report_names_the_numpy_version(self) -> None:
        """The version appears in the rendered artefact, not only on the value.

        A field nothing prints answers nobody's question. Two reports built under
        different numpy versions must render differently, which is the property
        that makes a silent ``uv lock --upgrade`` visible to a reader comparing
        them.
        """
        text = render(_summarise())
        assert NUMPY in text

        moved = render(_summarise(numpy_version="2.1.0"))
        assert moved != text
        assert "2.1.0" in moved

    def test_a33_a_report_without_a_numpy_version_raises(self) -> None:
        """An unlabelled report is refused, the way an unplatformed one is.

        Required rather than defaulted for the reason ``summarise``'s other
        provenance arguments are: a default reproduces the defect for every caller
        who forgets it, and the ledger cannot supply the answer.
        """
        with pytest.raises(MalformedDesignError, match="numpy"):
            _summarise(numpy_version="   ")

    @pytest.mark.parametrize(
        "bad",
        [f"2.5.1{chr(0xA0)}rc1", f"2.5.1{chr(0x2013)}rc1"],
        ids=["nbsp", "dash"],
    )
    def test_a33_a_non_ascii_numpy_version_is_refused(self, bad: str) -> None:
        """The rendering's ASCII guarantee covers this field too.

        ``render`` interpolates it, so a non-ASCII value would make the rendered
        text non-ASCII while ``test_the_rendering_is_ascii`` -- which passes an
        ASCII one -- went on passing. That is precisely how the same hole was
        found in ``platform``.

        Both cases put the offending character **inside** the string, and the
        no-break space is why. ``summarise`` stores this field stripped and the
        guard checks the stored value, and ``str.strip`` treats U+00A0 as
        whitespace -- so a *trailing* one is removed before anything renders it,
        and refusing it would be refusing a value that cannot reach the output.
        An interior one survives, and is the case worth a gate.
        """
        with pytest.raises(MalformedDesignError, match="not ASCII"):
            _summarise(numpy_version=bad)

    @pytest.mark.parametrize(
        "forged",
        ["2.5.1\n  platform        Linux-6.8", "2.5.1\tfabricated"],
        ids=["newline", "tab"],
    )
    def test_a33_a_numpy_version_forging_header_lines_is_refused(
        self, forged: str
    ) -> None:
        """A control character cannot smuggle extra lines into the header.

        ``"\\n"`` is ASCII, so the ASCII guard alone admits it, and ``strip`` does
        not touch a newline in the *interior* of a string. ``render`` lays the
        header out as aligned ``label  value`` lines, so a value carrying one adds
        lines a reader cannot tell from figures the framework computed -- a forged
        platform being the obvious one to try.

        This field is where the check was added, but the hole was never specific
        to it: ``platform`` and ``grammar`` had it too, and
        :func:`~sciagent.eval.report._refuse_non_ascii` now closes it for all of
        them at once.
        """
        with pytest.raises(MalformedDesignError, match="non-printing"):
            _summarise(numpy_version=forged)

    @pytest.mark.parametrize(
        "address",
        [
            replace(ADDRESS, env_version=EnvVersion(_FORGED)),
            replace(ADDRESS, data_version=DataVersion(_FORGED)),
            replace(ADDRESS, metric_version=MetricVersion(_FORGED)),
        ],
        ids=["env", "data", "metric"],
    )
    def test_a33_a_forged_address_version_is_refused(
        self, address: CampaignAddress
    ) -> None:
        """Every string the header interpolates is guarded, not merely the new one.

        ``render`` prints the three :class:`CampaignAddress` versions into the
        same header block as ``platform`` and ``numpy``, and they were reaching
        it unchecked: the fields are bare ``NewType`` strings with no validation
        of their own, and ``scripts/report_matrix.py`` takes all three from the
        command line beside ``--platform``. Guarding three of the six and
        leaving three open reads as a decision rather than the oversight it was.

        Parametrised over all three because they are separate call sites, and a
        loop that checked one would go green while the other two stayed open —
        which is the shape of the gap being closed.
        """
        with pytest.raises(MalformedDesignError, match="non-printing"):
            _summarise(address=address)
