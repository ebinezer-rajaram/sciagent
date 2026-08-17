"""Print a campaign ledger as SPEC §8's D1-D6 vector table.

    uv run python scripts/report_matrix.py .cache/campaign/spec9.sqlite \\
        --platform "$(uname -sm)" --grammar pointproc-edits/1.0.0

Thin by intent. Everything that decides a number lives in
:mod:`sciagent.eval.report`, which is tested; this resolves an address, opens a
ledger read-only and prints. A script is not where a reporting rule should live,
because a script is the one part of this nobody runs under pytest.

``--platform`` and ``--grammar`` have **no defaults**, and that is the point.
``docs/DECISIONS.md`` records a measured Windows/Ubuntu divergence in a reported
number, and the registry content-addresses with no platform term -- so the ledger
cannot say which machine filled it and a default here would invent an answer.
:func:`~sciagent.eval.report.summarise` refuses an unlabelled report; this just
declines to paper over that with a plausible guess.

Nothing here has run against a real campaign, because no cell of the matrix has
been run: ``docs/DECISIONS.md`` records the platform precondition that blocks
that, and it is unchanged. What this is for is the moment the first cells land.

**Do not hash this script's redirected output as an artefact.**
:func:`~sciagent.eval.report.render` returns LF-only text and every byte of it is
ASCII, so the *string* is platform-independent -- but stdout is a text stream, so
on Windows the bytes that leave the process are CRLF. Redirecting to a file and
digesting it would therefore give a platform-dependent hash for a reason that has
nothing to do with any number in the report. The ledger is the artefact; this is a
view of it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from environments.pointproc.matrix import SPEC9_CONTRAST
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    GrammarVersion,
    MetricVersion,
    ScenarioId,
)
from sciagent.eval.matrix import CampaignAddress
from sciagent.eval.report import Contrast, contrast, render, summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.registry.ledger import CampaignLedger
from sciagent.registry.partitions import DataPartition


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="path to the campaign ledger")
    parser.add_argument(
        "--platform",
        required=True,
        help="the machine every cell ran on; no default, see the module docstring",
    )
    parser.add_argument(
        "--grammar",
        required=True,
        help="grammar version D1 and D6 were computed under",
    )
    parser.add_argument("--env-version", required=True)
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--metric-version", required=True)
    parser.add_argument(
        "--partition",
        default=DataPartition.DEV.value,
        choices=[partition.value for partition in DataPartition],
    )
    parser.add_argument(
        "--contrast",
        action="store_true",
        help=(
            "also print SPEC §9's preregistered contrast. Off by default: it "
            "raises when no replicate detected inadequacy, which is a legitimate "
            "state for a partial campaign and should not stop the table printing"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print the report, returning a process exit status."""
    args = _parser().parse_args(argv)
    if not args.ledger.exists():
        print(f"no ledger at {args.ledger}", file=sys.stderr)
        return 2

    address = CampaignAddress(
        env_version=EnvVersion(args.env_version),
        data_version=DataVersion(args.data_version),
        metric_version=MetricVersion(args.metric_version),
        partition=DataPartition(args.partition),
    )
    with CampaignLedger.open(args.ledger) as ledger:
        entries = ledger.entries()

    # A script may import an environment; the first invariant is about
    # ``sciagent`` not doing so. This is the domain-specific line.
    def scenario_class(name: ScenarioId) -> ScenarioClass:
        return scenario(str(name)).scenario_class

    report = summarise(
        entries,
        address=address,
        scenario_class=scenario_class,
        platform=args.platform,
        grammar=GrammarVersion(args.grammar),
    )
    print(render(report), end="")

    if args.contrast:
        try:
            result = contrast(
                report,
                scenario=SPEC9_CONTRAST.scenario,
                treatment=SPEC9_CONTRAST.treatment,
                comparator=SPEC9_CONTRAST.comparator,
                dimension=SPEC9_CONTRAST.dimension,
                # Read off the declaration, not left to the function default.
                # A declaration that set this to False would otherwise have the
                # *conditioned* contrast computed and then reported as
                # `preregistered False`, which is the one combination that looks
                # like a finding rather than a bug.
                conditional_on_inadequacy=SPEC9_CONTRAST.conditional_on_inadequacy,
                preregistration=SPEC9_CONTRAST,
            )
        except MalformedDesignError as error:
            # Not a traceback: --contrast's own help text calls "no replicate
            # detected inadequacy" a legitimate state for a partial campaign, and
            # the table above has already printed. Exit 3 distinguishes it from
            # the missing-ledger 2 and from a real crash.
            print(f"contrast unavailable: {error}", file=sys.stderr)
            return 3
        print(_contrast_lines(result))
    return 0


def _contrast_lines(result: Contrast) -> str:
    """Return the contrast block, read off the result rather than recomputed."""
    return "\n".join(
        [
            f"preregistered contrast on {result.scenario} -- {result.dimension}",
            f"  {'preregistered':<22s}{result.preregistered}",
            f"  {'conditioned':<22s}{result.conditioned_on_inadequacy}",
            f"  {result.treatment_system:<22s}{result.treatment.point:>10.4f}  "
            f"[{result.treatment.low:>10.4f}, {result.treatment.high:>10.4f}]  "
            f"n={result.treatment.n_finite}",
            f"  {result.comparator_system:<22s}{result.comparator.point:>10.4f}  "
            f"[{result.comparator.low:>10.4f}, {result.comparator.high:>10.4f}]  "
            f"n={result.comparator.n_finite}",
            f"  {'intervals overlap':<22s}{result.overlaps}",
            f"  {'treatment exceeds':<22s}{result.exceeds}",
            f"  {'same seeds (paired)':<22s}{result.paired}",
            "",
            "  Exploratory. Overlap and direction are two readings, not one",
            "  verdict; SPEC sec. 12 criterion 5 asks for non-overlap and does",
            "  not make non-overlap sufficient.",
            "",
            "  If 'same seeds' is False, conditioning left the arms on different",
            "  worlds and part of the difference between them is that, which",
            "  twenty seeds cannot separate out.",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
