"""Run a configured workflow through the compiled LangGraph."""

import argparse
import json
import os
from time import perf_counter

from .config import load_config, runtime_config
from .decision_client import DecisionClient
from .state.workflow_state import WorkflowState
from .workflow_graph import build_workflow_graph


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default=config.get("default_workflow"))
    parser.add_argument("--runtime", default=config.get("default_decision_runtime"))
    parser.add_argument("--inputs", help="JSON object overriding configured default inputs")
    args = parser.parse_args()

    if not args.workflow:
        parser.error("Set default_workflow in the local configuration or pass --workflow")
    _, selected_runtime = runtime_config(args.runtime)
    inputs = config.get("default_inputs", {})
    if args.inputs:
        inputs = json.loads(args.inputs)
        if not isinstance(inputs, dict):
            parser.error("--inputs must be a JSON object")

    with DecisionClient(
        base_url=os.environ.get("DECISION_API_URL", selected_runtime["api_url"]),
        timeout_seconds=float(os.environ.get("DECISION_API_TIMEOUT", "900")),
    ) as client:
        client.get_model_info()
        created = client.create_run()
        print(f"Decision log run: {created['run_id']}", flush=True)
        initial_state = WorkflowState(
            workflow_name=args.workflow,
            inputs=inputs,
            decision_runtime=client,
        )
        graph = build_workflow_graph()
        print("Running workflow...", flush=True)
        started = perf_counter()
        final_state = graph.invoke(initial_state)
        elapsed = perf_counter() - started

    for question_id, answer in final_state["answers"].items():
        print(f"\nQuestion: {question_id}")
        print(json.dumps(answer, indent=2, ensure_ascii=False))

    print(f"\nQuestions answered: {len(final_state['answers'])}")
    print(f"Workflow time: {elapsed:.2f}s")


if __name__ == "__main__":
    main()
