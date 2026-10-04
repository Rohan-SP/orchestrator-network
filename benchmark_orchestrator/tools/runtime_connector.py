"""Connect the benchmark graph to its selected decision-runtime container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

import httpx

from orchestrator.config import runtime_config
from orchestrator.decision_client import DecisionClient


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
BUILD_STATE_PATH = PACKAGE_ROOT / "benchmark_orchestrator.runtime_build_state.json"


def load_benchmark_config() -> tuple[Path, dict[str, Any]]:
    """Load the ignored benchmark config, allowing an explicit path override."""
    config_path = Path(
        os.environ.get(
            "BENCHMARK_ORCHESTRATOR_LOCAL_CONFIG",
            PACKAGE_ROOT / "benchmark_orchestrator.local.json",
        )
    ).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(
            "Benchmark orchestrator local configuration is missing: "
            f"{config_path}"
        )
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    return config_path, config


def _select_runtime(runtime_name: str | None) -> tuple[str, str, dict, dict]:
    _, local_config = load_benchmark_config()
    containers = local_config.get("runtime_containers", {})
    selected = runtime_name or local_config.get("default_runtime_container")
    if not isinstance(selected, str):
        raise KeyError("No runtime profile or container alias was selected.")

    if selected in containers:
        container_name = selected
    else:
        matches = [
            name
            for name, container in containers.items()
            if isinstance(container, dict)
            and container.get("runtime_profile") == selected
        ]
        if len(matches) != 1:
            choices = sorted(containers)
            profiles = sorted(
                {
                    container.get("runtime_profile")
                    for container in containers.values()
                    if isinstance(container, dict)
                    and isinstance(container.get("runtime_profile"), str)
                }
            )
            choices.extend(profile for profile in profiles if profile not in choices)
            available = ", ".join(choices) or "none configured"
            raise KeyError(
                f"Unknown or ambiguous runtime {selected!r}; configured: {available}."
            )
        container_name = matches[0]

    container = containers[container_name]
    if not isinstance(container, dict):
        raise KeyError(
            f"Runtime container {container_name!r} must be an object."
        )

    transport = container.get("transport")
    if transport not in {"compose", "ssh_tunnel", "direct_api"}:
        raise ValueError(
            f"Runtime container {container_name!r} needs transport set to compose, "
            "ssh_tunnel, or direct_api in benchmark_orchestrator.local.json."
        )
    profile_name = container.get("runtime_profile")
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise ValueError(
            f"Runtime container {container_name!r} must name a runtime_profile."
        )
    resolved_name, profile = runtime_config(profile_name)
    if profile.get("api_url") is None:
        raise ValueError(f"Runtime profile {resolved_name!r} is missing api_url.")
    if transport == "ssh_tunnel":
        profile["ssh_target"] = container.get("ssh_target")
    return container_name, resolved_name, container, profile


def _build_input_fingerprint(profile: dict) -> str:
    """Hash local image inputs so unchanged source does not rebuild every run."""
    compose_dir = Path(profile["compose_project_dir"]).resolve()
    dockerfile = Path(profile.get("dockerfile", "Dockerfile"))
    if not dockerfile.is_absolute():
        dockerfile = compose_dir / dockerfile

    inputs = [
        dockerfile,
        compose_dir / "pyproject.toml",
        compose_dir / ".dockerignore",
    ]
    source_dir = compose_dir / "src"
    if source_dir.is_dir():
        inputs.extend(
            path
            for path in source_dir.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
            and ".egg-info" not in path.parts
        )
    for compose_file in profile.get("compose_files", []):
        inputs.append(Path(compose_file))

    digest = hashlib.sha256()
    for path in sorted(set(inputs), key=lambda item: str(item)):
        if not path.is_file():
            raise FileNotFoundError(f"Runtime image input does not exist: {path}")
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _should_build_local_image(profile_name: str, profile: dict) -> tuple[bool, str]:
    fingerprint = _build_input_fingerprint(profile)
    try:
        state = json.loads(BUILD_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    known = state.get("profiles", {}).get(profile_name)
    return known != fingerprint, fingerprint


def _record_local_build(profile_name: str, fingerprint: str) -> None:
    try:
        state = json.loads(BUILD_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    profiles = state.get("profiles", {})
    profiles[profile_name] = fingerprint
    state = {"profiles": profiles}
    temporary_path = BUILD_STATE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary_path.replace(BUILD_STATE_PATH)


def _start_local_compose(profile: dict, build: bool = False) -> None:
    compose_dir = profile.get("compose_project_dir")
    compose_files = profile.get("compose_files")
    service = profile.get("service")
    if not compose_dir or not compose_files or not service:
        raise ValueError(
            "A local runtime profile needs compose_project_dir, compose_files, "
            "and service."
        )
    compose_dir = Path(compose_dir)
    if not compose_dir.is_dir():
        raise FileNotFoundError(f"Compose project directory does not exist: {compose_dir}")

    command = ["docker", "compose"]
    project_name = profile.get("compose_project_name")
    if project_name:
        command.extend(["--project-name", project_name])
    env_file = profile.get("env_file")
    if env_file:
        env_path = Path(env_file)
        if not env_path.is_file():
            raise FileNotFoundError(f"Compose environment file does not exist: {env_path}")
        command.extend(["--env-file", str(env_path)])
    for compose_file in compose_files:
        command.extend(["--file", str(compose_file)])
    command.extend(["up", "--detach"])
    if build:
        command.append("--build")
    command.append(service)

    completed = subprocess.run(
        command,
        cwd=compose_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        details = (completed.stderr or completed.stdout).strip()
        if details:
            details = f" Output: {details[-2000:]}"
        raise RuntimeError(
            f"docker compose could not start service {service!r} "
            f"(exit code {completed.returncode}).{details}"
        )


def _container_snapshot(
    profile: dict,
    docker_env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Read the selected Compose service's identity and running state."""
    project = profile.get("compose_project_name")
    service = profile.get("service")
    if not project or not service:
        raise ValueError("A runtime profile needs compose_project_name and service.")
    filters = [
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        f"label=com.docker.compose.service={service}",
    ]
    listed = subprocess.run(
        filters,
        check=False,
        capture_output=True,
        text=True,
        env=docker_env,
    )
    if listed.returncode:
        details = (listed.stderr or listed.stdout).strip()
        raise RuntimeError(
            "Could not inspect the decision-runtime Compose service. "
            f"Docker exited with code {listed.returncode}. {details[-1000:]}"
        )
    container_ids = listed.stdout.split()
    if not container_ids:
        return None

    inspected = subprocess.run(
        ["docker", "inspect", *container_ids],
        check=False,
        capture_output=True,
        text=True,
        env=docker_env,
    )
    if inspected.returncode:
        details = (inspected.stderr or inspected.stdout).strip()
        raise RuntimeError(
            "Could not inspect the decision-runtime container. "
            f"Docker exited with code {inspected.returncode}. {details[-1000:]}"
        )
    records = json.loads(inspected.stdout)
    running = [
        record
        for record in records
        if record.get("State", {}).get("Status") == "running"
    ]
    if len(running) > 1:
        raise RuntimeError(
            "Multiple decision-runtime containers are running for Compose service "
            f"{project}/{service}; stop the duplicate before connecting."
        )
    record = running[0] if running else records[0]
    return {
        "id": record.get("Id", ""),
        "image_id": record.get("Image", ""),
        "state": record.get("State", {}).get("Status", "unknown"),
        "published_ports": record.get("NetworkSettings", {}).get("Ports", {}),
    }


def _container_action(
    before: dict[str, str] | None,
    after: dict[str, str] | None,
) -> str:
    if after is None:
        raise RuntimeError("Compose completed but the decision-runtime container is missing.")
    if before is None:
        return "started"
    if before["id"] != after["id"] or before["image_id"] != after["image_id"]:
        return "updated"
    if before["state"] != "running" and after["state"] == "running":
        return "started"
    return "already_current"


def _compose_command(
    profile: dict,
    *,
    override_file: Path | None = None,
    subcommand: str = "up",
    pull_always: bool = False,
    no_build: bool = False,
) -> list[str]:
    compose_dir = profile.get("compose_project_dir")
    compose_files = profile.get("compose_files")
    service = profile.get("service")
    if not compose_dir or not compose_files or not service:
        raise ValueError(
            "A managed runtime profile needs compose_project_dir, compose_files, "
            "and service."
        )
    command = ["docker", "compose"]
    project_name = profile.get("compose_project_name")
    if project_name:
        command.extend(["--project-name", project_name])
    env_file = profile.get("env_file")
    if env_file:
        if not Path(env_file).is_file():
            raise FileNotFoundError(f"Runtime environment file does not exist: {env_file}")
        command.extend(["--env-file", str(env_file)])
    command.extend(["--project-directory", str(compose_dir)])
    for compose_file in compose_files:
        command.extend(["--file", str(compose_file)])
    if override_file is not None:
        command.extend(["--file", str(override_file)])
    command.append(subcommand)
    if subcommand != "up":
        return command
    command.append("--detach")
    if pull_always:
        command.extend(["--pull", "always"])
    if no_build:
        command.append("--no-build")
    command.append(profile["service"])
    return command


def _remote_container_snapshot(
    ssh_target: str,
    profile: dict,
) -> dict[str, Any] | None:
    """Read the remote Compose service through Docker's native SSH transport."""
    docker_env = os.environ.copy()
    # An explicit host is ignored when DOCKER_CONTEXT is also set.
    docker_env.pop("DOCKER_CONTEXT", None)
    docker_env["DOCKER_HOST"] = f"ssh://{ssh_target}"
    return _container_snapshot(profile, docker_env)


def _ensure_remote_compose(
    profile_name: str,
    profile: dict,
) -> dict[str, Any]:
    """Pull the configured image and reconcile the service on its SSH Docker host."""
    ssh_target = profile.get("ssh_target")
    image_reference = profile.get("image_reference")
    if not isinstance(image_reference, str) or not image_reference.strip():
        raise ValueError(
            "A remote runtime profile needs image_reference set to its registry image."
        )
    if not isinstance(ssh_target, str) or not ssh_target.strip():
        raise ValueError("The selected remote runtime has no SSH target configured.")

    before = _remote_container_snapshot(ssh_target, profile)
    api = urlsplit(str(profile["api_url"]))
    api_port = api.port or (443 if api.scheme == "https" else 80)
    api_host = api.hostname or "127.0.0.1"
    existing_api_bindings = (
        before.get("published_ports", {}).get(f"{api_port}/tcp", [])
        if before is not None
        else []
    )
    has_api_binding = any(
        binding.get("HostPort") == str(api_port)
        and binding.get("HostIp") in {api_host, "0.0.0.0", ""}
        for binding in (existing_api_bindings or [])
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".yaml",
        prefix="decision-runtime-image-",
        delete=False,
    ) as override:
        override.write(
            "services:\n"
            f"  {profile['service']}:\n"
            f"    image: {json.dumps(image_reference)}\n"
        )
        override_path = Path(override.name)
    try:
        render_command = _compose_command(
            profile,
            override_file=override_path,
            subcommand="config",
        )
        rendered = subprocess.run(
            render_command,
            cwd=profile["compose_project_dir"],
            check=False,
            capture_output=True,
            text=True,
        )
        if rendered.returncode:
            details = (rendered.stderr or rendered.stdout).strip()
            raise RuntimeError(
                "Could not render the remote decision-runtime Compose settings "
                f"(exit code {rendered.returncode}). {details[-2000:]}"
            )
        docker_env = os.environ.copy()
        # Keep registry credential-helper access in the local Docker client.
        docker_env.pop("DOCKER_CONTEXT", None)
        docker_env["DOCKER_HOST"] = f"ssh://{ssh_target}"
        command = [
            "docker",
            "compose",
            "--project-name",
            str(profile["compose_project_name"]),
            "--file",
            "-",
            "up",
            "--detach",
            "--pull",
            "always",
            "--no-build",
        ]
        if before is not None and not has_api_binding:
            # A previous failed start can leave a running, healthy container
            # without the host API binding Compose was meant to install.
            command.append("--force-recreate")
        command.append(str(profile["service"]))
        completed = subprocess.run(
            command,
            input=rendered.stdout,
            check=False,
            capture_output=True,
            text=True,
            env=docker_env,
        )
        if completed.returncode:
            details = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                "Could not pull/start the remote decision-runtime service "
                f"(exit code {completed.returncode}). {details[-2000:]}"
            )
    finally:
        override_path.unlink(missing_ok=True)

    after = _remote_container_snapshot(ssh_target, profile)
    published_api_bindings = (
        after.get("published_ports", {}).get(f"{api_port}/tcp", [])
        if after is not None
        else []
    )
    if not any(
        binding.get("HostPort") == str(api_port)
        and binding.get("HostIp") in {api_host, "0.0.0.0", ""}
        for binding in (published_api_bindings or [])
    ):
        raise RuntimeError(
            "The remote decision-runtime container started without publishing "
            f"its API port {api_port}; the SSH tunnel cannot reach it."
        )
    return {
        "profile": profile_name,
        "update_checked": True,
        "container_action": _container_action(before, after),
        "image_reference": image_reference,
    }


def _ensure_local_compose(
    profile_name: str,
    profile: dict,
) -> dict[str, Any]:
    before = _container_snapshot(profile)
    build_on_connect = profile.get("build_on_connect", False)
    if not isinstance(build_on_connect, bool):
        raise ValueError("Runtime profile build_on_connect must be true or false.")
    build = False
    fingerprint = None
    if build_on_connect:
        build, fingerprint = _should_build_local_image(profile_name, profile)
    _start_local_compose(profile, build=build)
    if build and fingerprint is not None:
        _record_local_build(profile_name, fingerprint)
    after = _container_snapshot(profile)
    return {
        "profile": profile_name,
        "update_checked": build_on_connect,
        "container_action": _container_action(before, after),
        "local_build_performed": build,
    }


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _start_ssh_tunnel(target: str, api_url: str) -> tuple[subprocess.Popen, str]:
    parsed = urlsplit(api_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("SSH forwarding currently requires an HTTP api_url.")
    remote_host = parsed.hostname
    remote_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    local_port = _unused_local_port()
    command = [
        "ssh",
        "-T",
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=30",
        "-L",
        f"127.0.0.1:{local_port}:{remote_host}:{remote_port}",
        target,
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    local_url = urlunsplit(
        (parsed.scheme, f"127.0.0.1:{local_port}", parsed.path, parsed.query, "")
    )
    return process, local_url


def _wait_until_ready(
    client: DecisionClient,
    process: subprocess.Popen | None,
    timeout_seconds: int = 60,
) -> dict:
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("Runtime profile startup_timeout_seconds must be positive.")
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                "SSH tunnel exited before the decision service became ready "
                f"(exit code {process.returncode}); check the alias and SSH access."
            )
        try:
            return client.get_model_details()
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
            time.sleep(0.5)
    raise TimeoutError(
        "Decision service did not become ready within "
        f"{timeout_seconds} seconds. "
        "Check the selected runtime profile and container logs."
    ) from last_error


@contextmanager
def connect_runtime(runtime_name: str | None = None) -> Iterator[DecisionClient]:
    """Ensure the named runtime profile is current and yield a ready client.

    ``runtime_name`` may be a runtime profile such as ``cpu_local`` or
    ``gpu_laptop_remote``, or a friendly alias from the benchmark local config.
    """
    container_name, profile_name, container, profile = _select_runtime(runtime_name)
    transport = container["transport"]
    ssh_target = container.get("ssh_target")
    tunnel: subprocess.Popen | None = None
    base_url = str(profile["api_url"]).rstrip("/")
    runtime_status: dict[str, Any]
    if transport == "ssh_tunnel":
        if (
            not isinstance(ssh_target, str)
            or not ssh_target.strip()
            or ssh_target.startswith("SET_")
        ):
            raise ValueError(
                f"Runtime container {container_name!r} has an unconfigured ssh_target."
            )
        runtime_status = _ensure_remote_compose(profile_name, profile)
        tunnel, base_url = _start_ssh_tunnel(ssh_target, base_url)
    elif transport == "compose":
        runtime_status = _ensure_local_compose(profile_name, profile)
    else:
        runtime_status = {
            "profile": profile_name,
            "update_checked": False,
            "container_action": "connected_only",
        }

    client = DecisionClient(base_url=base_url)
    client.runtime_status = runtime_status
    try:
        _wait_until_ready(
            client,
            tunnel,
            timeout_seconds=profile.get("startup_timeout_seconds", 60),
        )
        yield client
    finally:
        client.close()
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Connect to the selected benchmark decision-runtime container."
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--profile",
        help="Runtime profile from orchestrator.local.json, such as cpu_local.",
    )
    selection.add_argument(
        "--container",
        help="Friendly runtime alias from benchmark_orchestrator.local.json.",
    )
    args = parser.parse_args()
    with connect_runtime(args.profile or args.container) as client:
        details = client.get_model_details()
        model = details.get("model_info", {})
        print(
            json.dumps(
                {
                    "connected": True,
                    "runtime": client.runtime_status,
                    "model_id": model.get("model_id"),
                    "configuration_id": model.get("configuration_id"),
                    "device": model.get("device"),
                    "service_instance_id": details.get("service_info", {}).get(
                        "instance_id"
                    ),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
