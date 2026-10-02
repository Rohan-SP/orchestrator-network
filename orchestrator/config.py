"""Load machine-local paths and runtime targets for orchestration."""

import json
import os
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "orchestrator.local.json"


def load_config() -> dict[str, Any]:
    """Load local configuration, resolving configured paths beside that file."""

    config_path = Path(
        os.environ.get("ORCHESTRATOR_LOCAL_CONFIG", DEFAULT_CONFIG_PATH)
    ).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(
            "Orchestrator local configuration is missing. Create "
            f"{DEFAULT_CONFIG_PATH.name} beside pyproject.toml or set "
            "ORCHESTRATOR_LOCAL_CONFIG."
        )

    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    config["_config_path"] = config_path
    config["_config_dir"] = config_path.parent
    return config


def configured_path(name: str) -> Path:
    """Return one configured caller-data directory as an absolute path."""

    config = load_config()
    try:
        value = config["data_roots"][name]
    except KeyError as exc:
        raise KeyError(f"Missing data_roots.{name} in local configuration") from exc
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config["_config_dir"] / path
    return path.resolve()


def runtime_config(name: str | None = None) -> tuple[str, dict[str, Any]]:
    """Return the selected runtime profile name and its local deployment record."""

    config = load_config()
    runtimes = config.get("decision_runtimes", {})
    selected = name or config.get("default_decision_runtime")
    if not selected or selected not in runtimes:
        choices = ", ".join(sorted(runtimes)) or "none configured"
        raise KeyError(f"Unknown decision runtime {selected!r}; configured: {choices}")
    record = dict(runtimes[selected])
    for key in ("compose_project_dir", "env_file"):
        if record.get(key):
            path = Path(record[key]).expanduser()
            if not path.is_absolute():
                path = config["_config_dir"] / path
            record[key] = path.resolve()
    if record.get("compose_files"):
        record["compose_files"] = [
            str((record["compose_project_dir"] / compose_file).resolve())
            for compose_file in record["compose_files"]
        ]
    return selected, record
