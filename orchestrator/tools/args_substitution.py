"""Substitutes ${...} placeholders in a graph_data block's args dict
against a workflow's resolved inputs.

Lean first pass: only handles a value that IS exactly one placeholder
(e.g. "${target_node_id}"), not one embedded inside a longer string —
the only pattern any current workflow actually uses. No validation: a
placeholder naming a variable that isn't in resolved_inputs raises a raw
KeyError.
"""

import re

_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def substitute_args(args: dict, resolved_inputs: dict) -> dict:
    substituted = {}
    for key, value in args.items():
        if isinstance(value, str):
            match = _PLACEHOLDER.fullmatch(value)
            if match:
                value = resolved_inputs[match.group(1)]
        substituted[key] = value
    return substituted
