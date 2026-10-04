"""State carried through one benchmark graph run."""

from typing import Any, TypedDict


class BenchmarkState(TypedDict, total=False):
    """Inputs and intermediate results for one workflow benchmark run.

    ``workflow_name`` identifies both the workflow and its associated
    benchmark. ``runtime_container`` selects a friendly entry in the local
    benchmark runtime registry. ``model`` records the requested model/runtime
    selection; the decision service reports the model it actually loaded.
    """

    workflow_name: str
    runtime_container: str
    model: str
    runtime_profile: str
    decision_runtime: Any
    workflow: dict[str, Any]
    benchmark_cases: list[dict[str, Any]]
    benchmark_suite_id: str
    benchmark_path: str
    benchmark_review_status: str
    predictions: list[dict[str, Any]]
    model_info: dict[str, Any]
    report: dict[str, Any]
