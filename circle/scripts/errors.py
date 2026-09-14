"""The single error type every Circle layer raises."""

from __future__ import annotations


class CircleError(Exception):
    """A user-facing failure. The fact store is left unchanged."""
