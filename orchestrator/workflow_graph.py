"""Assemble the LangGraph for one workflow run."""

from langgraph.graph import END, START, StateGraph

from .state.workflow_state import WorkflowState
from .tools.workflow_context_builder import build_context
from .tools.question_formatter import compile_decision_question
from .tools.workflow_reader import load_workflow


def prepare(state: WorkflowState) -> dict:
    """Prepare the values used by the workflow's model calls."""

    # Load the workflow once.
    workflow = load_workflow(state.workflow_name)
    context = build_context(workflow, state.inputs)
    tool_specs = workflow.get("tools", {}).get("available", [])

    # Return updates for the prepared state fields.
    return {
        "resolved_args": context["args"],
        "workflow_instructions": context["workflow_instructions"],
        "state_prefix": context["state_prefix"],
        "state_suffix": context["state_suffix"],
        "full_state": context["state_prefix"] + context["state_suffix"],
        "questions": context["questions"],
        "tool_specs": tool_specs,
    }


def prepare_state_cache(state: WorkflowState) -> dict:
    """Get or build the model cache for this workflow state."""
    # kjl3: Prepare the selected prefix/suffix combination and register its
    # state ID/cache key without overwriting references to other states.
    # kjl3: Record exact input identity with the cache association so a call
    # can verify the selected cache represents the intended state version.
    prepared = state.decision_runtime.prepare_state_cache(
        prefix_text="Context:\n" + state.state_prefix,
        suffix_text=state.state_suffix,
    )

    return {
        "state_cache_key": prepared["state_cache_key"],
    }


def ask_question(state: WorkflowState) -> dict:
    """Ask the next unanswered question against the selected state cache."""
    # kjl3: Resolve an explicit state/question pair to that state's cache key,
    # then store the returned answer under the same state/question pair.
    question = next(
        question
        for question in state.questions
        if question["id"] not in state.answers
    )

    runtime_question = compile_decision_question(
        state.workflow_instructions,
        question,
    )
    answer = state.decision_runtime.ask_question(
        state_cache_key=state.state_cache_key,
        question=runtime_question,
    )

    return {
        "current_question_id": question["id"],
        "answers": {
            **state.answers,
            question["id"]: answer,
        },
    }


def route_answer(state: WorkflowState) -> str:
    """Continue asking questions or finish when all are answered."""
    # kjl3: Route using answers for the selected state and select the next
    # state/question pair, including switching back to an earlier state.
    # kjl3: Implement answer-dependent run_tool routing and its tool handler;
    # select the pending tool before returning the run_tool route.
    has_unanswered_questions = any(
        question["id"] not in state.answers
        for question in state.questions
    )

    if has_unanswered_questions:
        return "ask_next"

    return "finish"


def run_tool(state: WorkflowState) -> dict:
    """Execute the workflow tool selected by the answer-routing rules."""
    raise NotImplementedError


def refresh_state_cache(state: WorkflowState) -> dict:
    """Refresh the state cache after a tool changes the available evidence."""
    # kjl3: Create a new suffix/state version after evidence changes, preserving
    # earlier states and answers; reuse a prefix only when its input matches.
    # kjl3: Release runtime caches when their states are no longer needed,
    # while retaining caches needed for planned return visits to earlier states.
    raise NotImplementedError


def finalize(state: WorkflowState) -> dict:
    """Release the combined state cache and clear its reference."""
    state.decision_runtime.release_state_cache(
        state.state_cache_key,
    )

    return {
        "state_cache_key": None,
    }


def build_workflow_graph():
    """Compile the workflow's prepare, model, and tool loop."""
    builder = StateGraph(WorkflowState)

    builder.add_node("prepare", prepare)
    builder.add_node("prepare_state_cache", prepare_state_cache)
    builder.add_node("ask_question", ask_question)
    builder.add_node("run_tool", run_tool)
    builder.add_node("refresh_state_cache", refresh_state_cache)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "prepare_state_cache")
    builder.add_edge("prepare_state_cache", "ask_question")
    builder.add_conditional_edges(
        "ask_question",
        route_answer,
        {
            "ask_next": "ask_question",
            "run_tool": "run_tool",
            "finish": "finalize",
        },
    )
    builder.add_edge("run_tool", "refresh_state_cache")
    builder.add_edge("refresh_state_cache", "ask_question")
    builder.add_edge("finalize", END)

    return builder.compile()
