"""Training-fee and company-funding arithmetic (docs/programs.md § 5a).

Pure functions — no I/O — so every amount the platform shows or stores is
computed in one place, the same way:

* money is `Decimal`, quantized to 2 places with ROUND_HALF_UP (the
  `Numeric(12, 2)` columns hold exactly that);
* a PERCENTAGE funding basis is a percentage of the fee (0 < p <= 100);
  a FIXED_AMOUNT basis is an amount in the fee's currency (> 0, never more
  than the fee);
* the candidate payable amount is always fee - company contribution, so the
  two parts add up to the fee exactly, whatever the rounding.

Nothing here supplies a business value: the fee, the funding basis and the
value always come from what HR configured or proposed.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.models.program import FundingType

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class FundingError(ValueError):
    """An invalid fee / funding combination; the message is user-facing."""


class PaymentStatus(StrEnum):
    """Derived from the obligation and the recorded (non-voided) payments —
    never stored, so it can't drift from the records."""

    #: No fee obligation fixed yet.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    #: Company funding covers the whole fee; the candidate owes nothing.
    FULLY_FUNDED = "FULLY_FUNDED"
    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    #: A balance is still outstanding after the due date.
    OVERDUE = "OVERDUE"


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class FundingSplit:
    fee: Decimal
    company_contribution: Decimal
    candidate_payable: Decimal


def split_fee(fee: Decimal, funding_type: FundingType | None, value: Decimal | None) -> FundingSplit:
    """How `fee` divides between the company and the candidate. With no
    funding (`funding_type` None) the candidate pays the whole fee.

    Example (illustrative only, not policy): fee 100000.00 at PERCENTAGE 40
    -> company 40000.00, candidate 60000.00."""
    fee = money(fee)
    if fee < 0:
        raise FundingError("The training fee can't be negative.")
    if funding_type is None:
        return FundingSplit(fee=fee, company_contribution=Decimal("0.00"), candidate_payable=fee)
    if value is None or value <= 0:
        raise FundingError("Enter a funding amount or percentage greater than zero.")
    if funding_type == FundingType.PERCENTAGE:
        if value > HUNDRED:
            raise FundingError("A funding percentage can't be more than 100%.")
        contribution = money(fee * value / HUNDRED)
    else:
        contribution = money(value)
        if contribution > fee:
            raise FundingError(
                "Company funding can't be more than the training fee "
                f"({fee:,.2f})."
            )
    return FundingSplit(fee=fee, company_contribution=contribution, candidate_payable=fee - contribution)


def check_contribution_range(
    contribution: Decimal, minimum: Decimal | None, maximum: Decimal | None
) -> None:
    """The program's approved contribution range (`scholarship_amount_min` /
    `_max`), when HR configured one."""
    if minimum is not None and contribution < minimum:
        raise FundingError(
            f"The company contribution ({contribution:,.2f}) is below the program's approved "
            f"minimum ({minimum:,.2f})."
        )
    if maximum is not None and contribution > maximum:
        raise FundingError(
            f"The company contribution ({contribution:,.2f}) is above the program's approved "
            f"maximum ({maximum:,.2f})."
        )


def payment_status(
    payable: Decimal | None, paid: Decimal, due_date: date | None, today: date
) -> PaymentStatus:
    if payable is None:
        return PaymentStatus.NOT_APPLICABLE
    if payable == 0:
        return PaymentStatus.FULLY_FUNDED
    if paid >= payable:
        return PaymentStatus.PAID
    if due_date is not None and today > due_date:
        return PaymentStatus.OVERDUE
    return PaymentStatus.PARTIALLY_PAID if paid > 0 else PaymentStatus.UNPAID
