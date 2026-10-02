"""Dispatches a workflow's graph_data block to the actual verb functions:
substitutes each block's args, calls the matching verb, and wraps each
result under that block's own descriptive key (e.g. "review-node-info").

Lean first pass: no validation — assumes every block's verb: name is a
real "verbs.<record>.<verb>" module with a run() function. The verb name
resolves directly via Python's own import system (verbs/ is organized by
record type to match verb_specs' own dotted naming), so there is no
separate registry to keep in sync as new verbs get added.
"""

import importlib
import sys

from ..config import configured_path
from .args_substitution import substitute_args


def resolve_verb(verb_name: str):
    verbs_parent = configured_path("verbs_parent")
    if str(verbs_parent) not in sys.path:
        sys.path.insert(0, str(verbs_parent))
    module = importlib.import_module(f"verbs.{verb_name}")
    return module.run


def resolve_graph_data(graph_data: dict, resolved_inputs: dict) -> dict:
    results = {}
    for block_name, block in graph_data.items():
        verb_fn = resolve_verb(block["verb"])
        args = substitute_args(block["args"], resolved_inputs)
        results[block_name] = verb_fn(**args, fields=block["fields"])
    return results
