"""Reusable tools for the standalone benchmark orchestrator."""

from typing import Any


def connect_runtime(*args: Any, **kwargs: Any):
    """Load the connector lazily so its ``python -m`` entry point stays clean."""
    from .runtime_connector import connect_runtime as connect

    return connect(*args, **kwargs)


__all__ = ["connect_runtime"]
