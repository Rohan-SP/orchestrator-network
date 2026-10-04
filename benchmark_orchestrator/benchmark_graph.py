"""Standalone graph scaffold for evaluating a workflow against gold cases."""

import json
import math
import os
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from orchestrator.config import runtime_config
from orchestrator.tools.question_formatter import compile_decision_question
from orchestrator.tools.workflow_context_builder import (
    build_state_prefix,
    build_state_suffix_from_graph_data,
)
from orchestrator.tools.workflow_reader import load_workflow

from .state.benchmark_state import BenchmarkState
from .tools.runtime_connector import connect_runtime


def load_inputs(state: BenchmarkState) -> dict[str, Any]:
    """Load the selected workflow and gold benchmark cases."""
    package_root = Path(__file__).resolve().parents[1]
    config_path = Path(
        os.environ.get(
            "BENCHMARK_ORCHESTRATOR_LOCAL_CONFIG",
            package_root / "benchmark_orchestrator.local.json",
        )
    ).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(
            "Benchmark orchestrator local configuration is missing: "
            f"{config_path}"
        )

    local_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    workflow_name = state.get("workflow_name")
    if not isinstance(workflow_name, str) or not workflow_name.strip():
        raise ValueError("BenchmarkState.workflow_name must be a non-empty string.")

    benchmark_addresses = local_config.get("benchmarks", {})
    benchmark_address = benchmark_addresses.get(workflow_name)
    if not isinstance(benchmark_address, str) or not benchmark_address.strip():
        raise KeyError(
            f"No benchmark file address is configured for workflow "
            f"{workflow_name!r} in {config_path.name}."
        )
    benchmark_path = Path(benchmark_address).expanduser()
    if not benchmark_path.is_absolute():
        benchmark_path = config_path.parent / benchmark_path
    benchmark_path = benchmark_path.resolve()
    if not benchmark_path.is_file():
        raise FileNotFoundError(
            f"Configured benchmark file for {workflow_name!r} does not exist: "
            f"{benchmark_path}"
        )

    container_registry = local_config.get("runtime_containers", {})
    runtime_container = state.get("runtime_container") or local_config.get(
        "default_runtime_container"
    )
    if not isinstance(runtime_container, str):
        configured = ", ".join(sorted(container_registry)) or "none"
        raise KeyError(
            f"Unknown runtime container {runtime_container!r}; configured: {configured}."
        )
    if runtime_container in container_registry:
        container = container_registry[runtime_container]
    else:
        profile_matches = [
            record
            for record in container_registry.values()
            if isinstance(record, dict)
            and record.get("runtime_profile") == runtime_container
        ]
        if len(profile_matches) != 1:
            configured = ", ".join(sorted(container_registry)) or "none"
            raise KeyError(
                f"Unknown or ambiguous runtime container/profile "
                f"{runtime_container!r}; configured aliases: {configured}."
            )
        container = profile_matches[0]
    if not isinstance(container, dict) or not container.get("runtime_profile"):
        raise ValueError(
            f"Runtime container {runtime_container!r} must name a runtime_profile."
        )
    runtime_profile, _ = runtime_config(container["runtime_profile"])

    workflow = load_workflow(workflow_name)
    suite = json.loads(benchmark_path.read_text(encoding="utf-8"))
    if not isinstance(suite, dict):
        raise ValueError(f"Benchmark suite must be a JSON object: {benchmark_path}")
    if suite.get("schema_version") != 1:
        raise ValueError(
            f"Unsupported benchmark schema version {suite.get('schema_version')!r} "
            f"in {benchmark_path}; expected 1."
        )
    if suite.get("workflow") != workflow_name:
        raise ValueError(
            f"Benchmark suite {suite.get('suite_id')!r} names workflow "
            f"{suite.get('workflow')!r}, expected {workflow_name!r}."
        )
    cases = suite.get("cases")
    if not isinstance(cases, list) or any(
        not isinstance(case, dict) for case in cases
    ):
        raise ValueError(
            "Benchmark suite cases must be a list of objects: "
            f"{benchmark_path}"
        )

    return {
        "workflow_name": workflow_name,
        "runtime_container": runtime_container,
        "runtime_profile": runtime_profile,
        "workflow": workflow,
        "benchmark_cases": cases,
        "benchmark_suite_id": suite.get("suite_id", ""),
        "benchmark_path": str(benchmark_path),
        "benchmark_review_status": suite.get("review_status", "unknown"),
    }


def run_cases(state: BenchmarkState) -> dict[str, Any]:
    """Run each benchmark case through each question in the workflow."""
    workflow = state.get("workflow")
    cases = state.get("benchmark_cases")
    if not isinstance(workflow, dict):
        raise ValueError("BenchmarkState.workflow must be loaded before run_cases.")
    if not isinstance(cases, list) or not cases:
        raise ValueError("The benchmark suite must contain at least one case.")

    workflow_questions = workflow.get("questions")
    if not isinstance(workflow_questions, list) or not workflow_questions:
        raise ValueError("The workflow must define at least one question.")
    if any(not isinstance(question, dict) for question in workflow_questions):
        raise ValueError("Every workflow question must be an object.")
    question_ids = [question.get("id") for question in workflow_questions]
    if any(not isinstance(question_id, str) or not question_id for question_id in question_ids):
        raise ValueError("Every workflow question must have a non-empty id.")
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("Workflow question ids must be unique.")

    declared_graph_data = workflow.get("graph_data")
    if not isinstance(declared_graph_data, dict):
        raise ValueError("Workflow graph_data must be an object.")
    graph_data_keys = set(declared_graph_data)
    prefix = "Context:\n" + build_state_prefix(workflow)
    workflow_instructions = workflow.get("instructions")
    compiled_questions = [
        (
            question,
            compile_decision_question(workflow_instructions, question),
        )
        for question in workflow_questions
    ]

    case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("Every benchmark case must have a non-empty case_id.")
        if case_id in case_ids:
            raise ValueError(f"Duplicate benchmark case_id {case_id!r}.")
        case_ids.add(case_id)

        graph_fixture = case.get("graph_data")
        if not isinstance(graph_fixture, dict) or set(graph_fixture) != graph_data_keys:
            fixture_keys = set(graph_fixture) if isinstance(graph_fixture, dict) else set()
            missing = sorted(graph_data_keys - fixture_keys)
            extra = sorted(fixture_keys - graph_data_keys)
            raise ValueError(
                f"Case {case_id!r} graph_data keys must match the workflow; "
                f"missing={missing}, extra={extra}."
            )

        gold = case.get("gold")
        if not isinstance(gold, dict) or set(gold) != set(question_ids):
            gold_ids = set(gold) if isinstance(gold, dict) else set()
            missing = sorted(set(question_ids) - gold_ids)
            extra = sorted(gold_ids - set(question_ids))
            raise ValueError(
                f"Case {case_id!r} gold must contain exactly one "
                f"gold label per workflow question; missing={missing}, extra={extra}."
            )
        for question in workflow_questions:
            question_id = question["id"]
            choices = question.get("choices")
            judgement = gold[question_id]
            expected_choice = (
                judgement.get("choice") if isinstance(judgement, dict) else None
            )
            if (
                not isinstance(expected_choice, str)
                or not isinstance(choices, dict)
                or expected_choice not in choices
            ):
                raise ValueError(
                    f"Case {case_id!r} gold label {expected_choice!r} for "
                    f"question {question_id!r} is not a workflow choice key."
                )

    predictions: list[dict[str, Any]] = []
    with connect_runtime(state.get("runtime_container")) as runtime:
        model_info = runtime.get_model_info()
        runtime.create_run()
        for case in cases:
            state_suffix = build_state_suffix_from_graph_data(case["graph_data"])
            prepared_state = runtime.prepare_state_cache(
                prefix_text=prefix,
                suffix_text=state_suffix,
            )
            cache_key = prepared_state["state_cache_key"]
            try:
                for question, runtime_question in compiled_questions:
                    question_id = question["id"]
                    answer = runtime.ask_question(
                        state_cache_key=cache_key,
                        question=runtime_question,
                    )
                    predicted_choice = answer.get("choice")
                    if not isinstance(predicted_choice, str):
                        raise ValueError(
                            f"Runtime answer for case {case['case_id']!r}, "
                            f"question {question_id!r} has no choice key."
                        )
                    predictions.append(
                        {
                            "case_id": case["case_id"],
                            "split": case.get("split"),
                            "question_id": question_id,
                            "expected_choice": case["gold"][question_id]["choice"],
                            "predicted_choice": predicted_choice,
                            "answer": answer,
                        }
                    )
            finally:
                runtime.release_state_cache(cache_key)

    return {"predictions": predictions, "model_info": model_info}


def score_results(state: BenchmarkState) -> dict[str, Any]:
    """Compare model answers with gold labels and assemble the report."""
    workflow = state.get("workflow")
    cases = state.get("benchmark_cases")
    predictions = state.get("predictions")
    if not isinstance(workflow, dict):
        raise ValueError("BenchmarkState.workflow is required for scoring.")
    if not isinstance(cases, list) or not cases:
        raise ValueError("At least one benchmark case is required for scoring.")
    if not isinstance(predictions, list):
        raise ValueError("BenchmarkState.predictions must be populated before scoring.")

    questions = workflow.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("The workflow must define questions before scoring.")
    question_choices: dict[str, list[str]] = {}
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("Every workflow question must be an object.")
        question_id = question.get("id")
        choices = question.get("choices")
        if (
            not isinstance(question_id, str)
            or not question_id
            or not isinstance(choices, dict)
            or not choices
            or any(not isinstance(choice, str) or not choice for choice in choices)
        ):
            raise ValueError("Each workflow question needs an id and keyed choices.")
        if question_id in question_choices:
            raise ValueError(f"Duplicate workflow question id {question_id!r}.")
        question_choices[question_id] = list(choices)

    gold_by_pair: dict[tuple[str, str], str] = {}
    case_ids: set[str] = set()
    case_order: list[str] = []
    split_by_case: dict[str, str] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Every benchmark case must be an object.")
        case_id = case.get("case_id")
        gold = case.get("gold")
        split = case.get("split")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError("Benchmark case ids must be non-empty and unique.")
        if not isinstance(split, str) or not split:
            raise ValueError(f"Case {case_id!r} must have a non-empty split.")
        if not isinstance(gold, dict) or set(gold) != set(question_choices):
            raise ValueError(
                f"Case {case_id!r} gold must have one judgment per question."
            )
        case_ids.add(case_id)
        case_order.append(case_id)
        split_by_case[case_id] = split
        for question_id, choice_keys in question_choices.items():
            judgement = gold[question_id]
            gold_choice = (
                judgement.get("choice") if isinstance(judgement, dict) else None
            )
            if not isinstance(gold_choice, str) or gold_choice not in choice_keys:
                raise ValueError(
                    f"Case {case_id!r} has an invalid gold choice for "
                    f"question {question_id!r}."
                )
            gold_by_pair[(case_id, question_id)] = gold_choice

    expected_pairs = set(gold_by_pair)
    prediction_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for prediction in predictions:
        if not isinstance(prediction, dict):
            raise ValueError("Every prediction must be an object.")
        case_id = prediction.get("case_id")
        question_id = prediction.get("question_id")
        if not isinstance(case_id, str) or not isinstance(question_id, str):
            raise ValueError("Prediction case_id and question_id must be strings.")
        pair = (case_id, question_id)
        if pair not in expected_pairs:
            raise ValueError(
                f"Prediction refers to an unknown case/question pair {pair!r}."
            )
        if pair in prediction_by_pair:
            raise ValueError(f"Duplicate prediction for case/question pair {pair!r}.")
        expected_choice = gold_by_pair[pair]
        if prediction.get("expected_choice") != expected_choice:
            raise ValueError(
                f"Prediction gold label for {pair!r} does not match the benchmark case."
            )
        if prediction.get("split") != split_by_case[case_id]:
            raise ValueError(
                f"Prediction split for {pair!r} does not match the benchmark case."
            )
        predicted_choice = prediction.get("predicted_choice")
        if predicted_choice not in question_choices[question_id]:
            raise ValueError(
                f"Prediction for {pair!r} is not a declared workflow choice key."
            )
        prediction_by_pair[pair] = prediction

    missing_pairs = expected_pairs - set(prediction_by_pair)
    if missing_pairs:
        preview = sorted(missing_pairs)[:5]
        raise ValueError(
            f"Missing {len(missing_pairs)} benchmark predictions; first missing "
            f"pairs: {preview}."
        )

    aggregate_metrics = _summarize_case_group(
        case_order,
        question_choices,
        gold_by_pair,
        prediction_by_pair,
    )
    split_groups: dict[str, list[str]] = {}
    for case_id in case_order:
        split_groups.setdefault(split_by_case[case_id], []).append(case_id)
    per_split = {
        split: {
            "case_count": len(split_case_ids),
            **_summarize_case_group(
                split_case_ids,
                question_choices,
                gold_by_pair,
                prediction_by_pair,
            ),
        }
        for split, split_case_ids in split_groups.items()
    }
    report = {
        "workflow": state.get("workflow_name", workflow.get("workflow")),
        "benchmark_suite_id": state.get("benchmark_suite_id"),
        "benchmark_review_status": state.get("benchmark_review_status"),
        "runtime_container": state.get("runtime_container"),
        "runtime_profile": state.get("runtime_profile"),
        "model_info": state.get("model_info", {}),
        "case_count": len(case_ids),
        "prediction_count": len(prediction_by_pair),
        **aggregate_metrics,
        "per_split": per_split,
    }
    return {"report": report}


def _summarize_case_group(
    case_ids: list[str],
    question_choices: dict[str, list[str]],
    gold_by_pair: dict[tuple[str, str], str],
    prediction_by_pair: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Calculate workflow and per-question metrics for one case subset."""
    per_question: dict[str, dict[str, Any]] = {}
    class_error_rates: list[float] = []
    correct_total = 0
    all_calibration_rows: list[tuple[dict[str, Any], str]] = []

    for question_id, choice_keys in question_choices.items():
        confusion = {
            gold: {predicted: 0 for predicted in choice_keys}
            for gold in choice_keys
        }
        question_correct = 0
        calibration_rows = []
        for case_id in case_ids:
            pair = (case_id, question_id)
            gold_choice = gold_by_pair[pair]
            prediction = prediction_by_pair[pair]
            predicted_choice = prediction["predicted_choice"]
            confusion[gold_choice][predicted_choice] += 1
            question_correct += gold_choice == predicted_choice
            calibration_rows.append((prediction, gold_choice))
            all_calibration_rows.append((prediction, gold_choice))

        per_choice: dict[str, dict[str, Any]] = {}
        supported_class_errors: list[float] = []
        for choice_key in choice_keys:
            support = sum(confusion[choice_key].values())
            correct = confusion[choice_key][choice_key]
            predicted_count = sum(
                confusion[gold][choice_key] for gold in choice_keys
            )
            recall = correct / support if support else None
            error_rate = 1.0 - recall if recall is not None else None
            precision = correct / predicted_count if predicted_count else None
            if error_rate is not None:
                supported_class_errors.append(error_rate)
                class_error_rates.append(error_rate)
            per_choice[choice_key] = {
                "support": support,
                "predicted_count": predicted_count,
                "correct": correct,
                "incorrect": support - correct,
                "recall": recall,
                "precision": precision,
                "error_rate": error_rate,
            }

        macro_error = (
            sum(supported_class_errors) / len(supported_class_errors)
            if supported_class_errors
            else None
        )
        per_question[question_id] = {
            "prediction_count": len(case_ids),
            "correct": question_correct,
            "incorrect": len(case_ids) - question_correct,
            "accuracy": question_correct / len(case_ids) if case_ids else None,
            "macro_error_rate": macro_error,
            "balanced_accuracy": 1.0 - macro_error if macro_error is not None else None,
            "worst_class_error_rate": max(supported_class_errors)
            if supported_class_errors
            else None,
            "class_coverage": {
                "represented": len(supported_class_errors),
                "total": len(choice_keys),
                "complete": len(supported_class_errors) == len(choice_keys),
            },
            "per_choice": per_choice,
            "confusion_matrix": confusion,
            "calibration": _calibration_summary(calibration_rows, choice_keys),
        }

    prediction_count = len(case_ids) * len(question_choices)
    macro_error = (
        sum(class_error_rates) / len(class_error_rates) if class_error_rates else None
    )
    return {
        "prediction_count": prediction_count,
        "overall": {
            "correct": sum(result["correct"] for result in per_question.values()),
            "incorrect": prediction_count
            - sum(result["correct"] for result in per_question.values()),
            "accuracy": (
                sum(result["correct"] for result in per_question.values())
                / prediction_count
                if prediction_count
                else None
            ),
            "macro_error_rate": macro_error,
            "balanced_accuracy": 1.0 - macro_error if macro_error is not None else None,
            "worst_class_error_rate": max(class_error_rates)
            if class_error_rates
            else None,
            "class_coverage_complete": all(
                result["class_coverage"]["complete"]
                for result in per_question.values()
            ),
        },
        "per_question": per_question,
        "calibration": _calibration_summary(
            all_calibration_rows,
            {
                (question_id, choice_key)
                for question_id, choice_keys in question_choices.items()
                for choice_key in choice_keys
            },
            keyed_by_question=True,
        ),
    }


def _calibration_summary(
    rows: list[tuple[dict[str, Any], str]],
    choice_keys: list[str] | set[tuple[str, str]],
    *,
    bin_count: int = 10,
    keyed_by_question: bool = False,
) -> dict[str, Any]:
    """Summarize confidence calibration and Brier score where probabilities exist."""
    valid: list[tuple[dict[str, float], str, str]] = []
    missing_count = 0
    invalid_count = 0
    for prediction, gold_choice in rows:
        answer = prediction.get("answer")
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if not isinstance(probabilities, dict):
            missing_count += 1
            continue
        question_id = prediction.get("question_id")
        expected_keys = (
            {key for qid, key in choice_keys if qid == question_id}
            if keyed_by_question
            else set(choice_keys)
        )
        if set(probabilities) != expected_keys:
            invalid_count += 1
            continue
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or value > 1
            for value in probabilities.values()
        ):
            invalid_count += 1
            continue
        total_probability = sum(probabilities.values())
        if not math.isclose(total_probability, 1.0, rel_tol=1e-3, abs_tol=1e-3):
            invalid_count += 1
            continue
        normalized = {
            key: value / total_probability for key, value in probabilities.items()
        }
        predicted_choice = prediction["predicted_choice"]
        valid.append((normalized, gold_choice, predicted_choice))

    if not valid:
        return {
            "available": False,
            "probability_predictions": 0,
            "missing_probability_predictions": missing_count,
            "invalid_probability_predictions": invalid_count,
            "expected_calibration_error": None,
            "multiclass_brier_score": None,
            "reliability_bins": [],
        }

    bin_records = [
        {"count": 0, "confidence_sum": 0.0, "correct": 0}
        for _ in range(bin_count)
    ]
    brier_total = 0.0
    for probabilities, gold_choice, predicted_choice in valid:
        confidence = probabilities[predicted_choice]
        is_correct = predicted_choice == gold_choice
        index = min(int(confidence * bin_count), bin_count - 1)
        record = bin_records[index]
        record["count"] += 1
        record["confidence_sum"] += confidence
        record["correct"] += int(is_correct)
        brier_total += sum(
            (probability - int(choice_key == gold_choice)) ** 2
            for choice_key, probability in probabilities.items()
        ) / len(probabilities)

    reliability_bins = []
    weighted_gap = 0.0
    for index, record in enumerate(bin_records):
        count = record["count"]
        if not count:
            continue
        mean_confidence = record["confidence_sum"] / count
        observed_accuracy = record["correct"] / count
        weighted_gap += count / len(valid) * abs(mean_confidence - observed_accuracy)
        reliability_bins.append(
            {
                "lower_bound": index / bin_count,
                "upper_bound": (index + 1) / bin_count,
                "count": count,
                "mean_confidence": mean_confidence,
                "observed_accuracy": observed_accuracy,
            }
        )

    return {
        "available": True,
        "probability_predictions": len(valid),
        "missing_probability_predictions": missing_count,
        "invalid_probability_predictions": invalid_count,
        "expected_calibration_error": weighted_gap,
        "multiclass_brier_score": brier_total / len(valid),
        "reliability_bins": reliability_bins,
    }


def build_benchmark_graph():
    """Compile the minimal load → run → score benchmark flow."""
    builder = StateGraph(BenchmarkState)

    builder.add_node("load_inputs", load_inputs)
    builder.add_node("run_cases", run_cases)
    builder.add_node("score_results", score_results)

    builder.add_edge(START, "load_inputs")
    builder.add_edge("load_inputs", "run_cases")
    builder.add_edge("run_cases", "score_results")
    builder.add_edge("score_results", END)

    return builder.compile()
