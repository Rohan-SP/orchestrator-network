# Orchestrator repository instructions

- This repository owns reusable LangGraph orchestration, workflow loading and
  context assembly, and the decision-service HTTP client.
- Callers own workflow definitions, contracts, graph data, verb implementations,
  credentials, and service deployment data.
- Keep machine-specific caller paths and deployment profiles in the ignored
  `orchestrator.local.json`. Never add caller names, datasets, private paths,
  credentials, or local environment values to tracked files.
- Read paths from local configuration; do not add hardcoded caller directories
  or workflow-specific values to reusable modules.
- Leave Git operations to the workspace owner. Do not stage, commit, or push.
- Keep deployment configuration in the service repository and address it via a
  local runtime profile. Do not move model loading, caches, or inference into
  this package.
