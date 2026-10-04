"""Run a configured workflow benchmark through its standalone LangGraph."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .benchmark_graph import build_benchmark_graph


def _result_payload(final_state: dict[str, Any]) -> dict[str, Any]:
    report = final_state.get("report")
    predictions = final_state.get("predictions")
    if not isinstance(report, dict) or not isinstance(predictions, list):
        raise RuntimeError("Benchmark graph completed without a report and predictions.")
    return {"report": report, "predictions": predictions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow",
        required=True,
        help="Workflow name; the matching gold suite is resolved from local config.",
    )
    parser.add_argument(
        "--runtime-container",
        help=(
            "Runtime-container alias or runtime profile from "
            "benchmark_orchestrator.local.json. Omit to use its default."
        ),
    )
    parser.add_argument(
        "--output",
        help=(
            "Optional JSON file for the scored report and every case/question "
            "prediction. Without this, the report is printed to stdout."
        ),
    )
    args = parser.parse_args()

    initial_state = {"workflow_name": args.workflow}
    if args.runtime_container:
        initial_state["runtime_container"] = args.runtime_container

    runtime_label = args.runtime_container or "configured default"
    print(
        f"Running benchmark for workflow {args.workflow!r} using {runtime_label}...",
        file=sys.stderr,
        flush=True,
    )
    final_state = build_benchmark_graph().invoke(initial_state)
    payload = _result_payload(final_state)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("Saved the report and per-question predictions to the requested file.")

    print(json.dumps(payload["report"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
