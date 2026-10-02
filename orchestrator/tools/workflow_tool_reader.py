"""Resolve a workflow's available verb tools."""

from collections.abc import Callable
from copy import deepcopy
import yaml

from ..config import configured_path
from .graph_data_dispatcher import resolve_verb


# kjl3: Keep workflow verb reading available for answer-dependent follow-up
# steps. Adapt selection and execution to decision answers when that path is built.


def get_verb(verb_name: str) -> tuple[Callable[..., object], dict]:
    """Return the named verb's function and its verb spec."""

    verb_fn = resolve_verb(verb_name)

    spec_path = configured_path("verb_specs") / f"{verb_name}.yaml"
    verb_spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    return verb_fn, verb_spec


def expand_enum_from(schema: dict) -> dict:
    """Replace enum_from references with options from their verb specs."""

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]

        if not isinstance(value, dict):
            return value

        result = {
            key: expand(item)
            for key, item in value.items()
            if key != "enum_from"
        }

        if "enum_from" in value:
            verb_name, section = value["enum_from"].rsplit(".", 1)
            spec_path = configured_path("verb_specs") / f"{verb_name}.yaml"
            source_spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            result["enum"] = list(source_spec[section])

        return result

    return expand(schema)


def process_tool_args(args_spec: dict, verb_spec: dict) -> dict:
    """Narrow a verb's input schema to the workflow's argument rules."""

    base_schema = deepcopy(verb_spec["input_schema"])
    base_properties = base_schema["properties"]
    properties = {}
    subset_limits = {}

    for arg_name, rule in args_spec.items():
        if rule.get("source") == "model":
            properties[arg_name] = base_properties[arg_name]

        elif rule.get("mode") == "subset":
            allowed = rule["allowed"]
            properties[arg_name] = {
                "type": "object",
                "properties": {
                    part: {
                        "type": "array",
                        "items": {"type": "string", "enum": options},
                    }
                    for part, options in allowed.items()
                },
                "required": list(allowed),
                "additionalProperties": False,
            }
            subset_limits[arg_name] = allowed

        else:
            raise ValueError(f"Unknown tool argument rule: {rule}")

    base_schema["properties"] = properties
    base_schema["required"] = list(properties)
    base_schema["additionalProperties"] = False

    return {
        "input_schema": expand_enum_from(base_schema),
        "subset_limits": subset_limits,
    }


def make_verb_descriptor(
    name: str,
    verb_name: str,
    verb_fn: Callable[..., object],
    verb_spec: dict,
    processed_args: dict,
) -> dict:
    """Return a model-neutral verb descriptor and validated callable."""

    subset_limits = processed_args["subset_limits"]

    def call_verb(**args: object) -> object:
        for arg_name, allowed_groups in subset_limits.items():
            selected_groups = args[arg_name]

            if set(selected_groups) != set(allowed_groups):
                raise ValueError(f"Invalid field groups for {arg_name}")

            for group, selected_fields in selected_groups.items():
                if not set(selected_fields).issubset(allowed_groups[group]):
                    raise ValueError(f"Fields outside workflow allowlist: {group}")

        return verb_fn(**args)

    return {
        "name": name,
        "verb": verb_name,
        "description": verb_spec["summary"],
        "input_schema": processed_args["input_schema"],
        "call": call_verb,
    }


def resolve_workflow_tools(tool_specs: list[dict]) -> list[dict]:
    """Resolve workflow verb entries into model-neutral callable descriptors."""

    tools = []

    for entry in tool_specs:
        verb_fn, verb_spec = get_verb(entry["verb"])
        processed_args = process_tool_args(entry["args"], verb_spec)

        tools.append(make_verb_descriptor(
            entry["name"],
            entry["verb"],
            verb_fn,
            verb_spec,
            processed_args,
        ))

    return tools
