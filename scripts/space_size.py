"""Print the size of the v2 hypothesis space (SPEC §2.1, last paragraph).

For the pointproc channel set (``size`` POSITIVE, ``sign`` SIGN) and the QTM
channel set (``magnitude`` REAL) it prints, at max depth 1, 2 and 3 and at
``MAX_FEATURES`` features (the null, intercept-only, structure included):

* the number of canonical features, by depth, and their ψ grid-point counts;
* the number of canonical structures, and of parameterised structures (every ψ
  at a grid point);
* the B-sparse dictionary (SPEC §4.1: every feature up to depth 2 at every ψ
  grid point), in groups and in design columns;
* the ratio of the space to the evaluation budgets F = 40 and 10 F = 400, and to
  the dictionary.

All numbers are exact integers from :mod:`sciagent.glm.space`. Usage::

    uv run python scripts/space_size.py
"""

from __future__ import annotations

from sciagent.glm.grammar import MAX_DEPTH, MAX_FEATURES, ChannelKind, ChannelSpec
from sciagent.glm.space import (
    count_features,
    count_features_by_depth,
    count_structure_points,
    count_structures,
    dictionary_size,
    psi_summary,
)

F = 40
BUDGETS = (F, 10 * F)

CHANNEL_SETS: tuple[tuple[str, tuple[ChannelSpec, ...]], ...] = (
    (
        "pointproc (size POSITIVE, sign SIGN)",
        (
            ChannelSpec("size", ChannelKind.POSITIVE, 1.0, 1.0),
            ChannelSpec("sign", ChannelKind.SIGN, 0.0, 1.0),
        ),
    ),
    (
        "QTM (magnitude REAL)",
        (ChannelSpec("magnitude", ChannelKind.REAL, 0.0, 1.0),),
    ),
)


def sci(n: int) -> str:
    """Scientific notation for an exact integer of any size."""
    return f"{n:.3e}"


def ratio(n: int, d: int) -> str:
    """``n / d`` for exact integers (true division of ints is correctly rounded)."""
    return f"{n / d:.3e}"


def report(name: str, channels: tuple[ChannelSpec, ...]) -> None:
    dictionary = dictionary_size(channels)
    print(f"== {name}; 0..K = {MAX_FEATURES} features (null included), 3 links ==")
    print(
        f"B-sparse dictionary (depth <= 2): {dictionary.n_features} features, "
        f"{dictionary.n_groups} groups, {dictionary.n_columns} columns"
    )
    rows = []
    for depth in range(1, MAX_DEPTH + 1):
        by_depth = count_features_by_depth(channels, depth)
        psi = psi_summary(channels, depth)
        structures = count_structures(channels, depth, MAX_FEATURES)
        points = count_structure_points(channels, depth, MAX_FEATURES)
        rows.append((depth, by_depth, psi, structures, points))
    print(
        f"{'D':>1} {'features':>9} {'per depth':>20} {'psi min/med/max':>16} "
        f"{'structures':>24} {'param. structures':>24}"
    )
    for depth, by_depth, psi, structures, points in rows:
        total = count_features(channels, depth)
        per_depth = "/".join(str(c) for c in by_depth)
        psi_text = f"{psi.minimum}/{psi.median}/{psi.maximum}"
        print(
            f"{depth:>1} {total:>9} {per_depth:>20} {psi_text:>16} "
            f"{structures:>24} {points:>24}"
        )
    print()
    print(
        f"{'D':>1} {'structs/F':>11} {'structs/10F':>12} "
        f"{'structs/dict cols':>18} {'param/dict cols':>16}"
    )
    for depth, _, _, structures, points in rows:
        print(
            f"{depth:>1} {ratio(structures, BUDGETS[0]):>11} "
            f"{ratio(structures, BUDGETS[1]):>12} "
            f"{ratio(structures, dictionary.n_columns):>18} "
            f"{ratio(points, dictionary.n_columns):>16}"
        )
    in_dictionary = rows[1][4]
    full = rows[-1][4]
    print(
        f"\nparameterised structures of depth <= 2 / of depth <= {MAX_DEPTH}: "
        f"{sci(in_dictionary)} / {sci(full)} = {in_dictionary / full:.3e} "
        f"(the share of the space a depth-2 dictionary can express)"
    )
    print()


def main() -> None:
    print(f"budgets: F = {BUDGETS[0]}, 10 F = {BUDGETS[1]}\n")
    for name, channels in CHANNEL_SETS:
        report(name, channels)


if __name__ == "__main__":
    main()
