# Orchestrator

This repository contains reusable LangGraph orchestration, workflow input
resolution, context assembly, and an HTTP client for a decision service.
Callers own workflow definitions, contracts, graph data, and verb implementations.
The orchestrator reads those resources from configured directories and contains
no caller-specific paths or datasets.

## Install

From this directory, create an environment and install the package:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## Local configuration

Create `orchestrator.local.json` beside `pyproject.toml`, or set
`ORCHESTRATOR_LOCAL_CONFIG` to another JSON file. This file is ignored by Git
because it holds machine-specific caller-data paths and runtime deployment
addresses. Paths in it are relative to the configuration file unless absolute.
Do not put credentials in this file; use the runtime's own environment setup.
`orchestrator.local.example.json` shows the generic shape; copy it and replace
its placeholder values for a local installation.

The shape below is generic and contains no project paths:

```json
{
  "data_roots": {
    "workflows": "../caller/workflows",
    "contracts": "../caller/contracts",
    "graph": "../caller/graph",
    "verb_specs": "../caller/verb_specs",
    "verbs_parent": "../caller",
    "decision_logs": "../caller/decision_logs"
  },
  "default_workflow": "workflow_name",
  "default_inputs": {},
  "default_decision_runtime": "cpu_local",
  "decision_runtimes": {
    "cpu_local": {
      "api_url": "http://127.0.0.1:8000",
      "compose_project_dir": "../runtime-service",
      "compose_files": ["compose.yaml"],
      "compose_project_name": "runtime-service",
      "build_on_connect": true,
      "startup_timeout_seconds": 300,
      "service": "inference"
    }
  }
}
```

Runtime entries record a stable local profile and its Compose project/service
address. They do not store ephemeral Docker container IDs. Add one profile per
deployment (for example CPU and GPU overrides), and select it with
`--runtime PROFILE`; `DECISION_API_URL` can override its API endpoint.

## Run

With the configured service running:

```powershell
.\.venv\Scripts\python.exe -m orchestrator.run_workflow
```

Override workflow, runtime profile, or inputs as needed:

```powershell
.\.venv\Scripts\python.exe -m orchestrator.run_workflow --workflow workflow_name --runtime local_cpu --inputs '{"item_id":"example"}'
```

The workflow and runtime API contract are caller/service concerns. The graph
prepares exact context fragments, opens one remote run, asks unanswered
questions, and releases its selected state cache when complete. Tool routing,
multi-state versioning, result assembly, and durable graph resume remain
unimplemented.
