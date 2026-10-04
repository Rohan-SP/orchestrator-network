"""Build the serialized decision context for one workflow run."""

import json

from .contracts_resolver import resolve_contracts
from .graph_data_dispatcher import resolve_graph_data
from .input_resolver import resolve_inputs


def build_state_prefix(workflow: dict) -> str:
    """Build reusable state JSON, ending just before the graph_data field."""

    contracts = resolve_contracts(workflow["contracts"])
    state = {"contracts": contracts}
    lines = json.dumps(state, ensure_ascii=False, indent=2).splitlines()
    return '{\n  "state": ' + lines[0] + "\n" + "\n".join(
        "  " + line for line in lines[1:-1]
    )


def build_state_suffix(workflow: dict, args: dict) -> str:
    """Build the graph_data JSON field and close the complete state object."""

    graph_data = resolve_graph_data(workflow["graph_data"], args)
    return build_state_suffix_from_graph_data(graph_data)


def build_state_suffix_from_graph_data(graph_data: dict) -> str:
    """Format resolved graph data as the state suffix used by the runtime."""

    lines = json.dumps(graph_data, ensure_ascii=False, indent=2).splitlines()
    graph_json = lines[0] + "\n" + "\n".join(
        "    " + line for line in lines[1:]
    )
    return ',\n    "graph_data": ' + graph_json + "\n  }\n}"


def build_context(workflow: dict, given: dict) -> dict:
    """Resolve arguments and return exact context fragments plus questions."""

    instructions = workflow.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Workflow instructions must be non-empty plain text.")
    args = resolve_inputs(workflow["input_args"], given)
    return {
        "workflow_name": workflow["workflow"],
        "args": args,
        "workflow_instructions": instructions,
        "state_prefix": build_state_prefix(workflow),
        "state_suffix": build_state_suffix(workflow, args),
        "questions": workflow["questions"],
    }
