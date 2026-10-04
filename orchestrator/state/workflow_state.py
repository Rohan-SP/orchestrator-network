"""State shape for one workflow run."""

from dataclasses import dataclass, field
from typing import Any

from ..decision_client import DecisionClient


@dataclass
class WorkflowState:
    """Data shared by the nodes of a single workflow run."""

    workflow_name: str
    inputs: dict[str, Any]
    decision_runtime: DecisionClient

    resolved_args: dict[str, Any] | None = None
    workflow_instructions: str | None = None
    # kjl3: Track multiple prefixes by stable ID and exact text, allowing
    # different combined states to reference the same instructions/contracts.
    state_prefix: str | None = None
    # kjl3: Track multiple suffix versions by ID and exact text; preserve older
    # graph-data versions when tools produce updated evidence.
    state_suffix: str | None = None
    # kjl3: Add combined-state records linking a state ID to its prefix ID,
    # suffix ID, and runtime cache key. Keep cache tensors in DecisionRuntime.
    full_state: str | None = None
    questions: list[dict[str, Any]] | None = None
    # kjl3: Track an active state ID so the graph can switch between existing
    # states without losing their text associations or cache references.
    state_cache_key: str | None = None
    # kjl3: Identify each question call by both state ID and question ID;
    # the same question can be asked independently against multiple states.
    current_question_id: str | None = None
    # kjl3: Store answers by state ID and question ID rather than question ID
    # alone, preserving which exact state each answer was computed against.
    answers: dict[str, Any] = field(default_factory=dict)

    tool_specs: list[dict[str, Any]] | None = None
    pending_tool: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
