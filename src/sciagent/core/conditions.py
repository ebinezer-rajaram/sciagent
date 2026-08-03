"""Conditions on a single diagnostic, and exact satisfiability over its range.

A :class:`~sciagent.core.types.Prediction` carries two conditions: what the
hypothesis predicts, and what would refute it. Acceptance test A16 rejects a
hypothesis whose refutation is *unsatisfiable over the diagnostic's declared
range*, so "is this condition satisfiable" has to be a decided question rather
than an estimated one. Sampling the range would answer it for the conditions that
happen to be wide and get it wrong for ``value == 3.0``.

So the algebra here is deliberately small enough to decide exactly. Every
condition denotes a finite union of real intervals with open or closed endpoints,
and every operation -- conjunction, disjunction, negation -- is a closed operation
on that representation. Satisfiability is then emptiness of an intersection, which
is a comparison of the endpoints the caller supplied and involves no arithmetic on
them at all. Nothing here can drift with floating point because nothing here
computes a new float.

The price is expressiveness: a condition may constrain one diagnostic, by
comparison against constants. Conditions relating two diagnostics, or involving a
computed threshold, are not representable and are not needed by the slice --
SPEC §3.3 gives ``Prediction`` a single ``diagnostic`` field.

Domain
------

The values modelled are the *finite* reals. An infinite endpoint is always open,
so a range of ``0..inf`` means "non-negative and arbitrarily large" and not "may
be literally infinite". A diagnostic that returned ``inf`` would therefore fall
outside the domain, and :func:`satisfiable_over` could call a refutation
unsatisfiable that such an observation would meet -- a false rejection, the
direction that costs a good hypothesis.

That is sound here because an infinite diagnostic is a broken estimator rather
than an extreme measurement: the slice's estimators raise on the inputs that
would produce one, so the value never reaches a condition. The restriction is
recorded rather than assumed, and ``test_a16_infinite_values_are_outside_the
_domain`` pins the behaviour so a later environment cannot inherit it silently.

Placement note: this lives in ``core/`` rather than beside the hypothesis
validator because :class:`~sciagent.core.types.Prediction` holds two of these and
``core`` may not import from its own siblings. See ``docs/DECISIONS.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from sciagent.core.errors import ConditionError

# --------------------------------------------------------------------------
# The condition algebra
# --------------------------------------------------------------------------

type CompareOp = Literal["<", "<=", ">", ">=", "==", "!="]

#: Every comparison operator, in a fixed order.
COMPARE_OPS: tuple[CompareOp, ...] = ("<", "<=", ">", ">=", "==", "!=")


def _check_finite_or_infinite(value: float, where: str) -> None:
    if math.isnan(value):
        raise ConditionError(f"{where} is NaN; a condition must denote a real set")


@dataclass(frozen=True, slots=True)
class Compare:
    """``value <op> threshold``, where ``value`` is the diagnostic's outcome."""

    op: CompareOp
    threshold: float

    def __post_init__(self) -> None:
        if self.op not in COMPARE_OPS:
            raise ConditionError(
                f"unknown comparison operator {self.op!r}; known operators are "
                f"{list(COMPARE_OPS)!r}"
            )
        _check_finite_or_infinite(self.threshold, f"threshold of {self.op!r}")


@dataclass(frozen=True, slots=True)
class Between:
    """Membership of a single interval, with explicit endpoint closure."""

    low: float
    high: float
    low_closed: bool = True
    high_closed: bool = True

    def __post_init__(self) -> None:
        _check_finite_or_infinite(self.low, "interval lower bound")
        _check_finite_or_infinite(self.high, "interval upper bound")


@dataclass(frozen=True, slots=True)
class And:
    """Conjunction. An empty conjunction is vacuously true."""

    terms: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class Or:
    """Disjunction. An empty disjunction is vacuously false."""

    terms: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class Not:
    """Negation."""

    term: Condition


type Condition = Compare | Between | And | Or | Not

#: The condition satisfied by every real value.
ALWAYS: Condition = And(())

#: The condition satisfied by no value.
NEVER: Condition = Or(())


# --------------------------------------------------------------------------
# Interval sets
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interval:
    """One connected subset of the reals.

    Guarantees infinite endpoints are always open, so that ``[-inf, inf]`` and
    ``(-inf, inf)`` cannot denote the same set through two different values --
    which would break the emptiness test that everything here rests on.
    """

    low: float
    high: float
    low_closed: bool = True
    high_closed: bool = True

    def __post_init__(self) -> None:
        if math.isinf(self.low) and self.low_closed:
            object.__setattr__(self, "low_closed", False)
        if math.isinf(self.high) and self.high_closed:
            object.__setattr__(self, "high_closed", False)

    @property
    def empty(self) -> bool:
        """Whether the interval contains no real value."""
        if self.low > self.high:
            return True
        if self.low == self.high:
            return not (self.low_closed and self.high_closed)
        return False

    def contains(self, value: float) -> bool:
        """Whether ``value`` lies in the interval."""
        above = value > self.low or (self.low_closed and value == self.low)
        below = value < self.high or (self.high_closed and value == self.high)
        return above and below


#: A normalised union: disjoint, ordered, non-empty intervals.
type IntervalSet = tuple[Interval, ...]

WHOLE_LINE: IntervalSet = (Interval(-math.inf, math.inf, False, False),)
EMPTY: IntervalSet = ()


def _lower_first(interval: Interval) -> tuple[float, bool]:
    """Sort key placing a closed lower bound before an open one at the same point."""
    return (interval.low, not interval.low_closed)


def _touch_or_overlap(left: Interval, right: Interval) -> bool:
    """Whether ``right`` (ordered after ``left``) joins onto it without a gap."""
    if right.low < left.high:
        return True
    if right.low == left.high:
        return right.low_closed or left.high_closed
    return False


def _normalise(intervals: tuple[Interval, ...]) -> IntervalSet:
    """Return the same point set as disjoint, ordered, non-empty intervals."""
    ordered = sorted(
        (interval for interval in intervals if not interval.empty), key=_lower_first
    )
    merged: list[Interval] = []
    for interval in ordered:
        if merged and _touch_or_overlap(merged[-1], interval):
            previous = merged[-1]
            if interval.high > previous.high or (
                interval.high == previous.high
                and interval.high_closed
                and not previous.high_closed
            ):
                merged[-1] = Interval(
                    previous.low,
                    interval.high,
                    previous.low_closed,
                    interval.high_closed,
                )
        else:
            merged.append(interval)
    return tuple(merged)


def _intersect_pair(left: Interval, right: Interval) -> Interval:
    """Return the intersection of two intervals, possibly empty."""
    if left.low > right.low:
        low, low_closed = left.low, left.low_closed
    elif right.low > left.low:
        low, low_closed = right.low, right.low_closed
    else:
        low, low_closed = left.low, left.low_closed and right.low_closed

    if left.high < right.high:
        high, high_closed = left.high, left.high_closed
    elif right.high < left.high:
        high, high_closed = right.high, right.high_closed
    else:
        high, high_closed = left.high, left.high_closed and right.high_closed

    return Interval(low, high, low_closed, high_closed)


def intersection(left: IntervalSet, right: IntervalSet) -> IntervalSet:
    """Return the intersection of two interval sets."""
    return _normalise(tuple(_intersect_pair(a, b) for a in left for b in right))


def union(left: IntervalSet, right: IntervalSet) -> IntervalSet:
    """Return the union of two interval sets."""
    return _normalise((*left, *right))


def complement(intervals: IntervalSet) -> IntervalSet:
    """Return the set of reals not in ``intervals``."""
    pieces: list[Interval] = []
    cursor, cursor_closed = -math.inf, False
    for interval in intervals:
        pieces.append(
            Interval(cursor, interval.low, cursor_closed, not interval.low_closed)
        )
        cursor, cursor_closed = interval.high, not interval.high_closed
    pieces.append(Interval(cursor, math.inf, cursor_closed, False))
    return _normalise(tuple(pieces))


# --------------------------------------------------------------------------
# Denotation and the questions A16 asks
# --------------------------------------------------------------------------


def _compare_intervals(comparison: Compare) -> IntervalSet:
    """Return the denotation of one comparison."""
    threshold = comparison.threshold
    point = _normalise((Interval(threshold, threshold, True, True),))
    match comparison.op:
        case "<":
            return _normalise((Interval(-math.inf, threshold, False, False),))
        case "<=":
            return _normalise((Interval(-math.inf, threshold, False, True),))
        case ">":
            return _normalise((Interval(threshold, math.inf, False, False),))
        case ">=":
            return _normalise((Interval(threshold, math.inf, True, False),))
        case "==":
            return point
        case "!=":
            return complement(point)


def intervals(condition: Condition) -> IntervalSet:
    """Return the set of diagnostic values satisfying ``condition``.

    Guarantees the result is normalised, and that it is the exact denotation:
    no endpoint is widened, narrowed or rounded anywhere in the recursion.
    """
    match condition:
        case Compare():
            return _compare_intervals(condition)
        case Between(low=low, high=high, low_closed=lc, high_closed=hc):
            return _normalise((Interval(low, high, lc, hc),))
        case And(terms=and_terms):
            conjunction = WHOLE_LINE
            for term in and_terms:
                conjunction = intersection(conjunction, intervals(term))
            return conjunction
        case Or(terms=or_terms):
            disjunction = EMPTY
            for term in or_terms:
                disjunction = union(disjunction, intervals(term))
            return disjunction
        case Not(term=inner):
            return complement(intervals(inner))


def evaluate(condition: Condition, value: float) -> bool:
    """Whether ``value`` satisfies ``condition``.

    Deliberately independent of the interval machinery above: it recurses on the
    condition and compares directly. That makes it the second opinion the gate
    tests cross-check :func:`intervals` against, and it is what a verifier will
    use to ask whether an observed diagnostic met a prediction or refuted it.
    """
    match condition:
        case Compare(op=op, threshold=threshold):
            match op:
                case "<":
                    return value < threshold
                case "<=":
                    return value <= threshold
                case ">":
                    return value > threshold
                case ">=":
                    return value >= threshold
                case "==":
                    return value == threshold
                case "!=":
                    return value != threshold
        case Between(low=low, high=high, low_closed=lc, high_closed=hc):
            above = value > low or (lc and value == low)
            below = value < high or (hc and value == high)
            return above and below
        case And(terms=and_terms):
            return all(evaluate(term, value) for term in and_terms)
        case Or(terms=or_terms):
            return any(evaluate(term, value) for term in or_terms)
        case Not(term=inner):
            return not evaluate(inner, value)


def _interval_witness(interval: Interval) -> float | None:
    """Return some value inside ``interval``, or ``None`` if none is representable.

    A closed endpoint is preferred because it needs no arithmetic. Where both
    ends are open the midpoint is tried and then the next representable value,
    so an interval spanning two adjacent floats is reported honestly as holding
    nothing rather than silently yielding one of its excluded endpoints.
    """
    if interval.empty:
        return None
    if interval.low_closed:
        return interval.low
    if interval.high_closed:
        return interval.high
    if math.isinf(interval.low) and math.isinf(interval.high):
        return 0.0
    if math.isinf(interval.low):
        return math.nextafter(interval.high, -math.inf)
    if math.isinf(interval.high):
        return math.nextafter(interval.low, math.inf)
    midpoint = interval.low / 2.0 + interval.high / 2.0
    if interval.low < midpoint < interval.high:
        return midpoint
    stepped = math.nextafter(interval.low, interval.high)
    return stepped if stepped < interval.high else None


def range_of(low: float, high: float) -> IntervalSet:
    """Return a diagnostic's declared range as an interval set.

    Finite endpoints are attainable and so are closed; infinite ones are not.
    """
    if math.isnan(low) or math.isnan(high):
        raise ConditionError(f"diagnostic range {low}..{high} contains NaN")
    if not high > low:
        raise ConditionError(f"diagnostic range {low}..{high} is empty")
    return _normalise((Interval(low, high, True, True),))


def witness(condition: Condition, low: float, high: float) -> float | None:
    """Return an attainable diagnostic value satisfying ``condition``, if one exists.

    Guarantees the returned value lies in ``low..high`` and satisfies the
    condition under :func:`evaluate`, so a "satisfiable" verdict always comes
    with the concrete outcome that justifies it. A validation report that says a
    refutation is satisfiable can therefore name the observation that would
    refute the hypothesis, rather than merely asserting that one exists.
    """
    for interval in intersection(intervals(condition), range_of(low, high)):
        found = _interval_witness(interval)
        if found is not None:
            return found
    return None


def satisfiable_over(condition: Condition, low: float, high: float) -> bool:
    """Whether some value in ``low..high`` satisfies ``condition``.

    This is the question A16 asks of a ``refutation``. A refutation no attainable
    diagnostic value could ever meet does not make the hypothesis hard to refute;
    it makes it unfalsifiable.

    Answers the mathematical question, over the reals. It can therefore differ
    from ``witness(...) is not None`` in one corner: an interval spanning two
    adjacent floats holds real values but no representable one. Nothing in the
    slice's grammar can produce such a condition, and the two are kept distinct
    rather than conflated so that the corner stays visible if one ever does.
    """
    return bool(intersection(intervals(condition), range_of(low, high)))


def covers(condition: Condition, low: float, high: float) -> bool:
    """Whether *every* value in ``low..high`` satisfies ``condition``.

    A refutation that covers the range is the mirror fault: the hypothesis is
    refuted by whatever happens, so the prediction carries no information.
    """
    declared = range_of(low, high)
    return not intersection(declared, complement(intervals(condition)))


def overlap(left: Condition, right: Condition, low: float, high: float) -> bool:
    """Whether some attainable value satisfies both conditions.

    Used to reject a prediction whose ``condition`` and ``refutation`` share an
    outcome: one observation may not both confirm and refute.
    """
    declared = range_of(low, high)
    joint = intersection(intervals(left), intervals(right))
    return bool(intersection(joint, declared))
