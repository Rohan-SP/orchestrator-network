"""Load a workflow definition from YAML."""

import yaml

from ..config import configured_path


def load_workflow(workflow_name: str) -> dict:
    """Return the named workflow as a dictionary."""

    workflow_path = configured_path("workflows") / f"{workflow_name}.yaml"
    return yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
