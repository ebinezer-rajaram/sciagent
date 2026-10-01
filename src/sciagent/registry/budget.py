"""Budget accounting for an investigation.

Deliberately minimal, and deliberately without policy. What a budget is *worth*
-- how many simulation calls an experiment costs, where scenario S10's
discriminating threshold sits -- is scenario data and arrives with v1 SPEC §11
item 11. What lives here is only the arithmetic, and the guarantee that spending
is monotone.

Immutable like everything else in the framework: :meth:`Budget.charge` returns a
new budget rather than mutating one, so a budget can be recorded alongside a
result without the recorded value later changing underneath it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sciagent.core.errors import BudgetError, BudgetExhaustedError


@dataclass(frozen=True, slots=True)
class Budget:
    """A total allowance and the part of it already spent.

    Guarantees ``0 <= spent <= total`` at all times, so a budget can never
    represent an overrun that has already happened.
    """

    total: float
    spent: float = 0.0

    def __post_init__(self) -> None:
        if self.total < 0.0:
            raise BudgetError(f"budget total must be non-negative, got {self.total}")
        if self.spent < 0.0:
            raise BudgetError(f"budget spend must be non-negative, got {self.spent}")
        if self.spent > self.total:
            raise BudgetError(
                f"budget is already overrun: spent {self.spent} of {self.total}"
            )

    @property
    def remaining(self) -> float:
        """Return the unspent allowance."""
        return self.total - self.spent

    @property
    def exhausted(self) -> bool:
        """Return whether nothing is left to spend."""
        return self.remaining <= 0.0

    def affords(self, cost: float) -> bool:
        """Return whether ``cost`` can be charged."""
        return 0.0 <= cost <= self.remaining

    def charge(self, cost: float) -> Budget:
        """Return the budget after spending ``cost``.

        Raises :class:`BudgetExhaustedError` rather than clamping. A system that
        silently received less than it asked for would produce results that look
        like weak reasoning under a sufficient budget, which is precisely the
        confound v1 SPEC §6 exists to prevent.
        """
        if cost < 0.0:
            raise BudgetError(f"cannot charge a negative cost {cost}")
        if cost > self.remaining:
            raise BudgetExhaustedError(
                f"charge of {cost} exceeds the remaining budget "
                f"{self.remaining} (of {self.total})"
            )
        return Budget(total=self.total, spent=self.spent + cost)
