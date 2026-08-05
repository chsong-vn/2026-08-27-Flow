"""
File: errors.py
Author: Simone Pilon - Noël Research Group - 2024
GitHub: https://github.com/simone16

Description: Custom Exceptions for the unit tasks module.
"""


class UnitTaskError(Exception):
    """Base class for unit-task errors."""


class TaskTimeoutError(UnitTaskError):
    """Task reached timeout before receiving required signal."""


class BadSlugQualityError(UnitTaskError):
    """Slug phase analysis failed."""


class CloggingError(UnitTaskError):
    """The flow system appears to be clogged."""


class NoSuitableVialError(UnitTaskError):
    """Error raised when an operation cannot be completed due to missing viable vial."""

    vial_type: str

    def __init__(self, *args, vial_type: str = None):
        super(Exception, self).__init__(*args)
        self.vial_type = vial_type

    def __str__(self) -> str:
        """A string representation of the error."""
        return (
            super(Exception, self).__str__()
            + f"\nRequired vial type: '{self.vial_type}'"
        )


class RecipeError(UnitTaskError):
    """Indicates an error in the calculation of a recipe for a reaction."""
