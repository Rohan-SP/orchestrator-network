# Benchmark Orchestrator

This package is a standalone LangGraph for evaluating a configured workflow
against benchmark cases. It is a peer of the main `orchestrator` package and
owns its own graph, run state, case iteration, and benchmark result handling.

Each case supplies a stable `case_id`, a `split`, a `graph_data` object
containing the precomputed value for every graph-data key declared by the
workflow, and a `gold` object mapping every workflow question id to its
judgment. Each judgment has a `choice` plus review rationale and evidence;
inference and scoring use the choice label only. `run_cases` uses the workflow's
resolved contracts, instruction, question definitions, and fixture data to
make one runtime call per case-question pair. It records expected and predicted
choices plus the full runtime answer. It does not run the workflow's graph-data
verbs while evaluating cases.

The scoring report includes ordinary accuracy, per-question confusion matrices,
per-choice support/precision/recall/error, macro error, balanced accuracy, and
the worst represented choice error. Class coverage marks choices that did not
appear as gold labels, so a benchmark with missing class coverage is visible.
Metrics are reported for the full suite and separately for each case `split`.
When every response includes a valid probability for every choice, the report
also includes ten-bin expected calibration error and multiclass Brier score.
The Brier score is the per-prediction mean squared error across choice
probabilities; missing or malformed probability maps are counted and excluded
from those calibration summaries, not from accuracy.

It may reuse general-purpose tools from this repository, such as workflow
loading, shared context and question formatting via
`orchestrator.tools.question_formatter.compile_decision_question`, and the decision-service
client. It must not import or call the main workflow graph. Both orchestrators
read caller-owned workflow files; benchmark cases and their expected answers
belong to the benchmark caller, not this reusable package.

Benchmark graph data comes from each benchmark case's controlled fixture. It
does not dispatch live graph-data verbs as part of its case setup. Shared
formatting helpers should keep the model-facing input consistent with the main
orchestrator while allowing the benchmark graph to supply fixture data.

The initial implementation will define the benchmark graph's input and output
contracts, load a workflow and case set, run each case through the decision
service, and return per-question and aggregate evaluation results. The graph
should remain independently runnable; a stable subgraph interface can be
considered later without coupling it to the main workflow graph.

The ignored `benchmark_orchestrator.local.json` maps friendly runtime-container
names to runtime profiles in `orchestrator.local.json`. Copy the shape from
`benchmark_orchestrator.local.example.json`; the remote GPU entry can use an
SSH alias that resolves through the machine's Tailscale/SSH configuration. The
same ignored file maps workflow names to benchmark file addresses, so each
workflow resolves its associated gold suite through an explicit local entry.

`benchmark_orchestrator.tools.runtime_connector.connect_runtime()` accepts a
runtime profile name such as `cpu_local` or `gpu_laptop_remote`, as well as a
friendly alias from `benchmark_orchestrator.local.json`. It ensures the selected
container is current and running before checking `/v1/model` and yielding the
shared `DecisionClient`.

For local Compose profiles, `build_on_connect: true` fingerprints the local
Dockerfile, Compose files, Python package metadata, and runtime source. It builds
only when those inputs change, then starts or reuses the service. Fingerprints
are saved in the ignored `benchmark_orchestrator.runtime_build_state.json`.
For a remote Compose profile, the connector uses Docker's SSH transport and the
local Compose files, with the remote host's bind-mount paths supplied by the
ignored `env_file`. The local Docker client pulls the configured
`image_reference` through that transport, so registry credential helpers stay
on the local machine. Compose reuses the container when the image and service
configuration are unchanged, while recreating a stale container if its API
port is missing. The remote profile therefore needs a mutable registry tag
such as `latest` or `gpu-test`.

The runtime profile's `startup_timeout_seconds` controls how long it waits for
`/v1/model`. The connection result exposes `runtime_status.container_action` as
`started`, `updated`, or `already_current`. The connector does not publish
images or install model weights. Keep the remote GPU profile, SSH target, image
reference, and its Compose env file in ignored local configuration.

To start/check the selected runtime by itself, use either its profile name or
the friendly alias:

```powershell
.\.venv\Scripts\python.exe -m benchmark_orchestrator.tools.runtime_connector --container cpu_local
```

```powershell
python -m benchmark_orchestrator.tools.runtime_connector --profile cpu_local
python -m benchmark_orchestrator.tools.runtime_connector --profile gpu_laptop_remote
```

Omit `--profile` and `--container` to use `default_runtime_container`. The
remote profile needs a valid SSH target in `benchmark_orchestrator.local.json`,
a registry image reference, and an ignored Compose env file containing the GPU
laptop's model, cache, and log mount paths. Copy
`benchmark_orchestrator.gpu_laptop_remote.compose.env.example` to the ignored
file name referenced by `orchestrator.local.json`, then set those remote paths.

## Run a workflow benchmark

From the `orchestrator-network` directory, pass the workflow name and the
runtime-container alias or runtime profile configured in local config.
For example, to run the node-condition-response suite on the remote GPU:

```powershell
.\.venv\Scripts\python.exe -m benchmark_orchestrator `
  --workflow node_condition_response_decision `
  --runtime-container gpu_laptop_remote `
  --output benchmark-result.json
```

Omit `--runtime-container` to use the configured default. The command starts or
updates the selected runtime through the connector, runs the full benchmark
graph, prints the scored report, and optionally saves that report with all
per-case predictions to the `--output` JSON file.
