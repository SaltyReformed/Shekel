"""
Shekel Budget App -- Domain-Specific Exceptions for Tax Calculations

Raised by the withholding calculation layer when inputs fail validation.
These are *not* Flask/HTTP errors -- they are pure domain errors suitable
for use in service-layer code that has no web dependency.
"""


class WithholdingError(Exception):
    """Base exception for federal withholding calculation errors."""


class InvalidGrossPayError(WithholdingError):
    """Raised when gross_pay is negative."""

    def __init__(self, gross_pay):
        super().__init__(f"gross_pay must be >= 0, got {gross_pay}")
        self.gross_pay = gross_pay


class InvalidPayPeriodsError(WithholdingError):
    """Raised when pay_periods is not a positive integer."""

    def __init__(self, pay_periods):
        super().__init__(f"pay_periods must be > 0, got {pay_periods}")
        self.pay_periods = pay_periods


class InvalidFilingStatusError(WithholdingError):
    """Raised when filing_status is not recognized."""

    def __init__(self, filing_status):
        super().__init__(f"Unrecognized filing_status: {filing_status!r}")
        self.filing_status = filing_status


class InvalidDependentCountError(WithholdingError):
    """Raised when a dependent count is negative."""

    def __init__(self, field_name, value):
        super().__init__(f"{field_name} must be >= 0, got {value}")
        self.field_name = field_name
        self.value = value
