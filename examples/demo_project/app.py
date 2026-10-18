"""Mini checkout app used as a GhostVerify fixture project."""

from formatting import format_total
from pricing import compute_total


def checkout(items):
    """Return the formatted checkout total for a list of prices."""
    return format_total(compute_total(items))
