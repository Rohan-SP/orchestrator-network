"""Compile a workflow question into the decision service's keyed-choice API."""

from copy import deepcopy
from typing import Any


def compile_decision_question(
    workflow_instructions: str,
    question: dict[str, Any],
) -> dict[str, Any]:
    """Combine workflow/question guidance and preserve keyed answer meanings."""
    if not isinstance(workflow_instructions, str) or not workflow_instructions.strip():
        raise ValueError("Workflow instructions must be non-empty plain text.")
    if question.get("type") != "choice":
        raise ValueError("Only choice questions can be sent to the decision service.")

    stem = question.get("stem")
    rubric = question.get("rubric")
    choices = question.get("choices")
    if not isinstance(stem, str) or not stem.strip():
        raise ValueError("Choice question stem must be non-empty text.")
    if not isinstance(rubric, str) or not rubric.strip():
        raise ValueError("Choice question rubric must be non-empty text.")
    if not isinstance(choices, dict) or not 2 <= len(choices) <= 64:
        raise ValueError("Choice question choices must be a keyed map of 2–64 options.")
    if any(
        not isinstance(key, str)
        or not key.strip()
        or (description is not None and not isinstance(description, str))
        for key, description in choices.items()
    ):
        raise ValueError("Choice keys must be non-empty text with text descriptions.")

    instructions = (
        f"Workflow instruction:\n{workflow_instructions}\n\n"
        f"Question:\n{stem}\n\n"
        f"Rubric:\n{rubric}"
    )
    compiled = {
        "id": question["id"],
        "type": "choice",
        "instructions": instructions,
        "criteria": deepcopy(choices),
    }
    if question.get("decision_options") is not None:
        compiled["decision_options"] = deepcopy(question["decision_options"])
    return compiled
