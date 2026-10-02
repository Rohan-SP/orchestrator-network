"""Resolves supplied arguments first, then derived arguments."""

import re

from .graph_data_dispatcher import resolve_verb

# kjl2: Implement validation before resolution for workflow definitions and
# caller arguments: expressions, references, verbs/signatures, required inputs,
# value/item types, and circular dependencies. Derived arguments must appear
# after their dependencies and cannot be supplied by the caller.

EXPRESSION = re.compile(
    r"\$\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\(([^()]*)\))?\}"
)


def resolve_inputs(input_args: list[dict], given: dict) -> dict:
    supplied = [spec for spec in input_args if "derive" not in spec]
    derived = [spec for spec in input_args if "derive" in spec]
    resolved = {}

    # First, bind supplied arguments and defaults.
    for spec in supplied:
        name = spec["name"]

        if name in given:
            resolved[name] = given[name]
        else:
            resolved[name] = spec.get("default")

    # Then, calculate derived arguments in declaration order.
    for spec in derived:
        name, arguments = EXPRESSION.fullmatch(spec["derive"]).groups()

        if arguments is None:
            value = resolved[name]
        else:
            argument_names = (
                [arg.strip() for arg in arguments.split(",")]
                if arguments.strip()
                else []
            )
            values = [resolved[arg] for arg in argument_names]
            value = resolve_verb(name)(*values)

        resolved[spec["name"]] = value

    return resolved
