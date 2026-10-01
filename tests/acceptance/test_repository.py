"""Acceptance test A38: the repository is legally readable and mechanically checked.

A38 is a post-freeze gate. v1 SPEC §6 stops at A24, so the criterion is stated in
``docs/v1/BACKLOG.md`` under *"A LICENSE and a CI gate, before any third party reads
this"*, and reads:

    ``test_the_repository_is_licensed`` -- a LICENSE exists, the
    ``pyproject`` field names it, and the CI workflow file invokes the suite and
    mypy. (The green run itself lives in the forge, not the test.)

Two credibility items, and the parenthesis matters
--------------------------------------------------

**The licence.** No ``LICENSE`` and no ``license`` field is default
all-rights-reserved: an external reviewer may not legally run or cite the code,
which for a research repository whose whole claim is that a third party could
re-run it is not a paperwork problem.

**The CI gate.** Every verification claim in this repository is session-local.
v1's ``.claude/hooks/suite-freshness.sh`` existed because a suite that ran 18:00-18:07
on 2026-08-15 was certified green against a tree a second session had changed at
18:05:56, and that had to be caught by hand. A check-only workflow is what makes
a green run an artefact rather than a recollection.

**What this test cannot check, and does not pretend to.** Whether CI *passes*
lives in the forge. A test that shelled out to ``gh`` would fail offline, would
fail in a fresh clone with no runs yet, and would be asserting something about a
web service rather than about this repository. So the assertions here are about
the workflow's *content*: that a file exists, that it runs on push, and that it
invokes both checks with the invocation CLAUDE.md's measurements settled --
``-n 4 --dist loadfile``, where ``--dist loadfile`` is load-bearing rather than
a tuning knob, because without it a module's tests split across workers and each
re-simulates rows its siblings already built.

The platform pin is not in tension with this
--------------------------------------------

The project is pinned to Windows and this workflow runs on Ubuntu. That is
consistent, because the pin governs *produced numbers* -- registry entries,
cached tables, reported figures -- and not verification. The job writes no
registry entry and no ``.cache/tables`` artefact that outlives it, and it reports
no figure. ``test_the_workflow_produces_no_artefact_that_outlives_it`` is
that condition made checkable rather than promised in a comment.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LICENSE = ROOT / "LICENSE"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOWS = ROOT / ".github" / "workflows"


def _check_workflow() -> str:
    """Return the text of the one workflow that runs both checks.

    **One file, not every file joined.** An earlier version of this helper joined
    them, which let ``on:``/``push`` be satisfied by one workflow and the checks
    by another -- so a ``check.yml`` on ``workflow_dispatch`` beside any unrelated
    push-triggered workflow passed a gate that is about the checks running on
    push. Which *file* the checks live in is still not pinned; that they live in
    one file with the trigger that runs them is the whole content of "on push".

    Comment lines are stripped, and that is a correctness point rather than
    tidiness. Every assertion below is about what the job *does*, and a ``#``
    line does nothing: the first version of this module read the raw text and
    failed on the workflow's own comment explaining that it deliberately has no
    ``actions/cache`` step. A test that a mechanism is absent cannot be satisfied
    or broken by prose describing its absence.
    """
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, f"no workflow file under {WORKFLOWS}"
    texts = [
        "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        for path in files
    ]
    matching = [
        text for text in texts if "uv run mypy" in text and "uv run pytest" in text
    ]
    assert len(matching) == 1, (
        f"{len(matching)} of {len(files)} workflow file(s) run both the suite and "
        f"mypy; the criterion is about one job that runs both, so zero means the "
        f"gate is unmet and two means it is ambiguous which one it grades"
    )
    return matching[0]


class TestRepositoryIsLicensed:
    """The two things an external reader hits in the first five minutes."""

    def test_the_repository_is_licensed(self) -> None:
        """A LICENSE exists, is the whole text, and ``pyproject`` names it.

        The full text and not a stub: a file saying "Apache-2.0" grants nothing,
        and the length check is what separates the licence from a note about one.
        The declared identifier and the file have to agree, or the package
        metadata claims a licence the repository does not carry.
        """
        assert LICENSE.is_file(), f"no LICENSE at {LICENSE}"
        text = LICENSE.read_text(encoding="utf-8")
        assert "Apache License" in text and "Version 2.0" in text
        assert len(text) > 10_000, (
            f"LICENSE is {len(text)} characters; the Apache-2.0 text is about "
            f"11k, so this is a reference to a licence rather than a grant of one"
        )

        declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
        assert declared["license"] == "Apache-2.0"
        # A glob rather than the literal: `["LICENSE*"]` is the commoner PEP 639
        # idiom and satisfies the criterion exactly as well, so pinning the
        # literal would fail a repository that met the standard.
        assert any(
            entry.startswith("LICENSE") for entry in declared["license-files"]
        ), f"license-files is {declared['license-files']!r} and names no LICENSE"

    def test_the_copyright_line_is_filled_in(self) -> None:
        """The appendix boilerplate is completed, not shipped as brackets.

        Apache-2.0's appendix ships as ``Copyright [yyyy] [name of copyright
        owner]``. Left unedited it names nobody, which is the one way to have a
        LICENSE file and still no attributable grant.
        """
        text = LICENSE.read_text(encoding="utf-8")
        assert "[yyyy]" not in text and "[name of copyright owner]" not in text
        assert "Ebinezer Rajaram" in text

    def test_the_workflow_invokes_the_suite_and_mypy(self) -> None:
        """Both checks, at the invocation the measurements settled.

        ``--dist loadfile`` is asserted rather than left to taste. Tests in one
        module share simulated table rows, so splitting a module across workers
        makes each worker re-simulate what a sibling already did: CLAUDE.md
        records one test costing 0.00s serially and 118.14s under the default
        ``load``. A CI job without it is slower than serial, which is how a
        green gate becomes one nobody waits for.
        """
        text = _check_workflow()
        assert "uv run mypy" in text
        invocations = [line for line in text.splitlines() if "uv run pytest" in line]
        assert invocations, "the workflow does not run the suite"
        for line in invocations:
            assert "-n 4" in line and "--dist loadfile" in line, (
                f"the workflow runs {line.strip()!r}. `--dist loadfile` does "
                f"nothing without `-n`: measured, `uv run pytest --dist loadfile` "
                f"alone runs serially, so the flag that matters is asserted on the "
                f"same line as the flag that activates it"
            )
        trigger = text.split("jobs:", 1)[0]
        assert "on:" in trigger and "push" in trigger, (
            "the suite and mypy do not run on push; a workflow that only runs on "
            "demand leaves every verification claim session-local, which is what "
            "this gate exists to end"
        )

    def test_the_workflow_produces_no_artefact_that_outlives_it(self) -> None:
        """The platform pin's condition, checked rather than promised.

        The pin governs produced numbers, not verification, so a Linux runner is
        consistent with it exactly as long as the job leaves nothing behind that
        a Windows-produced figure could later be compared against.

        **``actions/cache`` is forbidden outright, and that is the point of this
        test rather than an aside.** An earlier version enumerated mechanisms --
        ``upload-artifact``, the literal ``cache/tables``, ``git push``, ``git
        commit`` -- and a workflow caching ``path: .cache`` passed all four while
        persisting Ubuntu-built tables into every later job. That is not a
        contrived bypass: CLAUDE.md measures slice-table acquisition at 3m11s cold
        against 1.055s warm, so whoever watches CI spend three extra minutes a
        push will reach for exactly that step. The condition is therefore stated
        as *no cross-job cache and no mention of the repository's cache
        directory*, not as a list of the ways one might be built.

        A package cache is unaffected: ``astral-sh/setup-uv``'s ``enable-cache``
        caches uv's own downloads under the runner's home, names neither string,
        and carries no table.
        """
        text = _check_workflow()
        forbidden = (
            ("upload-artifact", "would carry a table or ledger row off the runner"),
            ("actions/cache", "would persist a built table into every later job"),
            (".cache", "names the directory the empirical tables are written to"),
            ("git push", "would commit a Linux-produced artefact back"),
            ("git commit", "would commit a Linux-produced artefact back"),
        )
        for token, why in forbidden:
            assert token not in text, (
                f"the check workflow mentions {token!r}, which {why}. A number, "
                f"table or registry entry that outlives a Linux job reopens the "
                f"cross-platform comparison the Windows pin exists to close"
            )

    def test_the_licence_is_not_contradicted_elsewhere(self) -> None:
        """One licence, named once.

        A ``classifiers`` entry or a second declaration naming a different
        licence is worse than none: it makes the terms ambiguous, which is the
        state a reviewer's legal counsel cannot clear.
        """
        declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
        classifiers = declared.get("classifiers", [])
        conflicting = [
            item
            for item in classifiers
            if item.startswith("License ::") and "Apache" not in item
        ]
        assert not conflicting, f"pyproject also claims {conflicting!r}"


@pytest.mark.parametrize("path", [LICENSE, WORKFLOWS])
def test_every_named_path_is_tracked(path: Path) -> None:
    """None of the three is a local file a fresh clone would not get.

    The failure this forecloses is specific and has happened here: ``136550a``
    shipped a cache override in an untracked file, so no worktree could read it
    and the setting was silently absent everywhere but the machine that wrote
    it. A LICENSE or a workflow in that state is a gate that passes for the
    author and for nobody else.

    ``pyproject.toml`` was a third parameter and is not one any more: it is
    tracked already and no implementation of A38 could untrack it, so the case
    could never go red. A parameter that cannot fail is not a guard, it is a
    count.
    """
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{path.relative_to(ROOT)} is not tracked by git"
