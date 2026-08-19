"""Acceptance test A39: the scale-up interface changes are written down.

A39 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The scale-up document §12 criterion 12 asks for"*,
and reads:

    ``test_a39_the_scale_up_document_exists_and_names_its_items`` -- the
    document exists and names, at minimum, the Stage A battery, the version-hash
    promise, and the Environment protocol disposition.

SPEC §12 criterion 12 asks for "interface changes required for scale-up
documented". The thinking is done and scattered: the Stage A battery entry, the
model-tier entry and the grammar-sensitivity entry are in ``docs/BACKLOG.md``,
SPEC §3.2's ``ENV_VERSION`` content-hash promise is unimplemented in the spec
itself, and F4's protocol disposition is a fact about the code. Nothing
consolidates them, so a criterion the repository has in fact satisfied in
substance reads as unmet.

Why this asserts on content and not on headings
-----------------------------------------------

A gate that pinned heading strings would fail on a reword and pass on a document
that named its sections and said nothing -- exactly the wrong way round. What is
asserted instead is that each required item is *named together with the thing it
is about*: the Stage A battery with the single-probe limitation it exists to
answer, the version promise with ``ENV_VERSION``, the protocol with F4. That is
still a keyword check and it is worth saying plainly that a keyword check cannot
tell a paragraph from a sentence. What it can do is stop the document from
quietly losing an item, which is the failure mode for a consolidation of six
fragments that live somewhere else.

The three the gate line requires are asserted individually with their own failure
messages; the remaining three the entry lists are asserted as a group, because
the criterion names three and the entry lists six, and a gate should be the
criterion's shape rather than the entry's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "SCALE-UP.md"


def _text() -> str:
    assert DOCUMENT.is_file(), f"no scale-up document at {DOCUMENT}"
    return DOCUMENT.read_text(encoding="utf-8").lower()


def _sections() -> list[str]:
    """Return the document split at its ``##`` headings, lowercased.

    **Sections and not the whole file, and the distinction is load-bearing.** An
    earlier version of this module claimed in its docstring to check each item
    "named together with the thing it is about" and then checked membership over
    one blob, which is not the same statement: ``"stage a" in text and "battery"
    in text`` is satisfied by a document with "Stage A" in a heading on line 8
    and "battery" used in an unrelated sense on line 40. Both senses already
    coexist in this repository -- ``docs/BACKLOG.md`` carries "A Stage A battery"
    and "the found battery of in-network M5+ events". Scoping to one section is
    what makes the pairing an actual claim about the prose.
    """
    return [f"##{part}" for part in _text().split("\n##")]


def _section_with(*tokens: str) -> str | None:
    """Return the first section containing every token, or ``None``."""
    for section in _sections():
        if all(token in section for token in tokens):
            return section
    return None


#: Words that name a change to a contract rather than an opinion about one.
#: Criterion 12 asks for *interface* changes, and the counterexample this guards
#: against is a document that consolidates six titles and proposes nothing --
#: which is the cheapest way to discharge "consolidate the fragments" and reads
#: as done.
_INTERFACE_WORDS = (
    "signature",
    "protocol",
    "field",
    "content hash",
    "dataclass",
    "module",
    "contract",
    "api",
)


class TestA39ScaleUpDocumented:
    """SPEC §12 criterion 12, made checkable."""

    def test_a39_the_scale_up_document_exists_and_names_its_items(self) -> None:
        """The three the criterion names, each with what it is about.

        Paired rather than checked as bare keywords: "stage a" appears all over
        this repository, and a document that mentioned it in passing would pass a
        bare check while saying nothing about why one probe stops being
        defensible at 104 scenarios.
        """
        battery = _section_with("stage a", "battery", "probe")
        assert battery is not None, (
            "no section names the Stage A battery together with the single probe "
            "it replaces; single-direction gates are the first thing that stops "
            "being defensible at 104 scenarios, and a document that says 'Stage "
            "A battery' in a list of titles has not said that"
        )

        # Either spelling: SPEC §3.2 writes the type as `EnvVersion`, whose
        # lowercase is `envversion`, while the environment's constant is
        # `ENV_VERSION`. A document arguing the promise in the SPEC's own
        # vocabulary meets the standard and must not fail the gate.
        version = next(
            (
                found
                for name in ("env_version", "envversion")
                if (found := _section_with(name, "content hash")) is not None
            ),
            None,
        )
        assert version is not None, (
            "no section names the environment version together with the content "
            "hash SPEC section 3.2 promises it is"
        )
        assert (
            "not yet" in version or "becomes" in version or "unimplemented" in version
        ), (
            "the version section names ENV_VERSION and 'content hash' but never "
            "says the promise is unmet; naming a subject is not naming the claim, "
            "and the registry is content-addressed throughout, so the phrase "
            "collides with something already true"
        )

        protocol = _section_with("protocol", "f4")
        assert protocol is not None, (
            "no section names the Environment protocol together with F4, the "
            "frozen decision that gave it"
        )
        assert "convention" in protocol, (
            "the protocol section never says what became of it. The disposition "
            "is that the slice dissolved it into convention; a section that names "
            "the protocol without saying so has named a topic, not a disposition"
        )

    def test_a39_the_remaining_fragments_are_consolidated(self) -> None:
        """The other three the entry lists, so nothing is quietly dropped.

        Not in the gate line, so grouped: the criterion asks for three at
        minimum and the entry names six, and consolidating five of six is the
        outcome this test exists to notice.
        """
        text = _text()
        missing = [
            name
            for name, token in (
                ("model tier", "model tier"),
                ("grammar sensitivity (R5)", "grammar sensitivity"),
                ("the likelihood-free engine", "likelihood-free"),
            )
            if token not in text
        ]
        assert not missing, f"the document does not consolidate {missing!r}"

    def test_a39_the_document_says_what_is_an_interface_change(self) -> None:
        """Criterion 12 asks for *interface* changes, not a wish list.

        The distinction is the content of the criterion: a benchmark of 104
        scenarios needs to know what has to change in the framework's contracts,
        not what would be nice to have. A document that lists ideas without
        saying which interface each one moves has answered a different question.
        """
        text = _text()
        assert "104" in text, (
            "the document does not name the 104-scenario benchmark it is the "
            "scale-up to, so what it is scaling up *to* is left implicit"
        )
        named = [
            section
            for section in _sections()
            if any(word in section for word in _INTERFACE_WORDS)
        ]
        assert len(named) >= 4, (
            f"only {len(named)} section(s) name an interface at all "
            f"({_INTERFACE_WORDS}). The counterexample this guards against is a "
            f"pointer index -- six note titles with 'see BACKLOG.md' beside each, "
            f"which consolidates the fragments' *names* and proposes no change to "
            f"any contract. That satisfies 'the fragments are consolidated' and "
            f"not criterion 12's 'interface changes required for scale-up'"
        )

    def test_a39_the_document_is_tracked(self) -> None:
        """A fresh clone gets it.

        ``136550a`` shipped a setting in an untracked file that no worktree
        could read; a document that satisfies a criterion only on the machine
        that wrote it satisfies nothing.
        """
        import subprocess

        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "docs/SCALE-UP.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "docs/SCALE-UP.md is not tracked by git"

    @pytest.mark.parametrize("stale", ["todo", "tbd", "xxx", "to be decided"])
    def test_a39_the_document_is_not_a_skeleton(self, stale: str) -> None:
        """A placeholder document would satisfy every keyword check above.

        The keyword checks are the gate's weakness and this is one guard on it.
        **Case-insensitive, and that was a real hole rather than a tidy-up**: this
        read the raw text while every other check in the module goes through the
        lowercased :func:`_text`, so a document ending in "todo: everything above
        this line. To be decided: all of it." passed the whole gate. Demonstrated
        by execution, not argued.

        The length floor is kept and is deliberately not load-bearing -- a
        counterexample reached 3,245 characters on filler alone, so it measures
        typing. What carries the weight is
        ``test_a39_the_document_says_what_is_an_interface_change``.
        """
        raw = DOCUMENT.read_text(encoding="utf-8").lower()
        assert stale not in raw
        assert len(raw) > 3_000, (
            f"the document is {len(raw)} characters; six consolidated fragments "
            f"with their interface consequences do not fit in that"
        )
