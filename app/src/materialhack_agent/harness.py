from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Sequence
import urllib.error
import urllib.request
import webbrowser


DEFAULT_OBJECTIVE = "design a protein that binds Zn2+ at pH 5 and can polymerize"
ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = ROOT / "app" / "web"
WORKBENCH_PORT_SCAN_LIMIT = 50
JOB_TERMINAL_STATES = {"succeeded", "failed"}
TOUCHSTONE_UVX_SPEC = "touchstone[mcp] @ git+https://github.com/charleneleong-ai/ai4science.git#subdirectory=touchstone"


@dataclass(frozen=True)
class WorkbenchRuntime:
    api_base_url: str
    ui_base_url: str
    api_port: int
    ui_port: int
    api_started: bool
    ui_started: bool


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Codex harness for the Novacore protein-design workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="check local harness, TRS, verifier, and Boltz readiness")
    preflight.add_argument("--boltz-command", default="boltz")
    preflight.add_argument("--touchstone-command", default="touchstone")
    preflight.add_argument("--json", action="store_true")
    preflight.set_defaults(func=preflight_command)

    setup = subparsers.add_parser("setup-boltz", help="install Boltz CLI tooling into a project venv")
    setup.add_argument("--python", default=None, help="Python executable for the venv, defaults to python3.11 when available")
    setup.add_argument("--venv", default=".venv", help="project venv path")
    setup.add_argument(
        "--package",
        choices=("boltz", "boltz-api", "both"),
        default="boltz",
        help="Boltz package family to install",
    )
    setup.add_argument("--skip-project", action="store_true", help="do not install local project packages")
    setup.set_defaults(func=setup_boltz_command)

    run = subparsers.add_parser("run", help="run the enforced seed-to-memory-to-loop workflow")
    run.add_argument("objective", nargs="?", default=DEFAULT_OBJECTIVE)
    run.add_argument("--seed-count", type=int, default=5)
    run.add_argument("--loops", type=int, required=True, help="fixed number of post-loop_0 optimization loops")
    run.add_argument(
        "--chat-agent",
        action="store_true",
        help="create loop_0 and leave post-seed loop planning, reflection, and finalization to Codex chat",
    )
    run.add_argument(
        "--run-mode",
        choices=("seed_and_loop", "seed_only"),
        default="seed_and_loop",
        help="workbench run mode; seed_only creates loop_0 without automatic optimization loops",
    )
    run.add_argument("--target-score", type=float, default=0.8)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument(
        "--seed-source",
        action="append",
        choices=("ccdc_csd", "de_novo"),
        required=True,
        help="seed source to allow; repeat to use both",
    )
    run.add_argument(
        "--metric-goal",
        action="append",
        default=[],
        metavar="NAME:COMPARATOR:TARGET[:WEIGHT]",
        help="optimization target, for example trs_total:gte:0.8:1.0",
    )
    run.add_argument("--boltz-command", default="boltz")
    run.add_argument("--enable-external-tools", action="store_true")
    run.add_argument(
        "--no-boltz-api",
        action="store_true",
        default=os.environ.get("NOVACORE_PREFER_BOLTZ_API", "").lower() in {"0", "false", "no"},
        help="do not prefer the hosted Boltz API even when BOLTZ_API_KEY is configured",
    )
    run.add_argument("--boltz-api-model", default=os.environ.get("NOVACORE_BOLTZ_API_MODEL", "boltz-2.1"))
    run.add_argument("--boltz-api-poll-interval-seconds", type=float, default=float(os.environ.get("NOVACORE_BOLTZ_API_POLL_INTERVAL_SECONDS", "5.0")))
    run.add_argument("--boltz-api-timeout-seconds", type=int, default=int(os.environ.get("NOVACORE_BOLTZ_API_TIMEOUT_SECONDS", os.environ.get("NOVACORE_BOLTZ_TIMEOUT_SECONDS", "3600"))))
    run.add_argument("--verifier-mcp-server", default=None, help="legacy verifier server label; Touchstone CLI/uvx is used for local verification")
    run.add_argument("--touchstone-command", default=os.environ.get("NOVACORE_TOUCHSTONE_COMMAND", "touchstone"))
    run.add_argument(
        "--no-touchstone-uvx",
        action="store_true",
        help="disable uvx fallback for Touchstone when the touchstone CLI is not already installed",
    )
    run.add_argument("--touchstone-deep", action="store_true", default=os.environ.get("NOVACORE_TOUCHSTONE_DEEP", "").lower() in {"1", "true", "yes"})
    run.add_argument("--touchstone-stress", action="store_true", default=os.environ.get("NOVACORE_TOUCHSTONE_STRESS", "").lower() in {"1", "true", "yes"})
    run.add_argument("--touchstone-timeout-seconds", type=int, default=int(os.environ.get("NOVACORE_TOUCHSTONE_TIMEOUT_SECONDS", "1800")))
    run.add_argument("--boltz-accelerator", choices=("cpu", "gpu", "tpu"), default=os.environ.get("NOVACORE_BOLTZ_ACCELERATOR", "cpu"))
    run.add_argument("--boltz-model", choices=("boltz1", "boltz2"), default=os.environ.get("NOVACORE_BOLTZ_MODEL", "boltz2"))
    run.add_argument("--boltz-cache", default=os.environ.get("BOLTZ_CACHE") or os.environ.get("NOVACORE_BOLTZ_CACHE"))
    run.add_argument(
        "--boltz-use-msa-server",
        action="store_true",
        default=os.environ.get("NOVACORE_BOLTZ_USE_MSA_SERVER", "").lower() in {"1", "true", "yes"},
    )
    run.add_argument("--boltz-msa-server-url", default=os.environ.get("NOVACORE_BOLTZ_MSA_SERVER_URL", "https://api.colabfold.com"))
    run.add_argument("--boltz-msa-pairing-strategy", default=os.environ.get("NOVACORE_BOLTZ_MSA_PAIRING_STRATEGY", "greedy"))
    run.add_argument("--boltz-timeout-seconds", type=int, default=int(os.environ.get("NOVACORE_BOLTZ_TIMEOUT_SECONDS", "3600")))
    run.add_argument("--boltz-recycling-steps", type=int, default=None)
    run.add_argument("--boltz-sampling-steps", type=int, default=None)
    run.add_argument("--boltz-diffusion-samples", type=int, default=None)
    run.add_argument(
        "--workbench",
        action="store_true",
        help="run through the local workbench API and open the live memory UI",
    )
    run.add_argument("--api-host", default=os.environ.get("NOVACORE_API_HOST", "127.0.0.1"))
    run.add_argument("--api-port", type=int, default=int(os.environ.get("NOVACORE_API_PORT", "8000")))
    run.add_argument("--ui-host", default=os.environ.get("NOVACORE_UI_HOST", "127.0.0.1"))
    run.add_argument("--ui-port", type=int, default=int(os.environ.get("NOVACORE_UI_PORT", "5173")))
    run.add_argument(
        "--no-open-browser",
        action="store_true",
        help="with --workbench, do not open the local browser automatically",
    )
    run.add_argument("--startup-timeout", type=float, default=25.0)
    run.add_argument("--poll-interval", type=float, default=0.5)
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=run_command)

    args = parser.parse_args(argv)
    args.func(args)


def preflight_command(args: argparse.Namespace) -> None:
    checks = _preflight_checks(args.boltz_command, args.touchstone_command)
    payload = {
        "ok": all(check["ok"] for check in checks if check.get("required", True)),
        "checks": checks,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print("Novacore harness preflight")
    for check in checks:
        status = "ok" if check["ok"] else "missing"
        required = "required" if check.get("required", True) else "optional"
        print(f"  {status:7} {required:8} {check['name']}: {check['detail']}")
        if not check["ok"] and check.get("remediation"):
            print(f"           fix: {check['remediation']}")


def setup_boltz_command(args: argparse.Namespace) -> None:
    python = _select_python(args.python)
    venv = (ROOT / args.venv).resolve()
    if _python_version(python) >= (3, 13):
        raise SystemExit(
            f"{python} is Python {_python_version_text(python)}. Use Python 3.11 or 3.12 for Boltz scientific wheels."
        )

    if not venv.exists():
        _run([python, "-m", "venv", str(venv)])
    pip = venv / "bin" / "pip"
    _run([str(pip), "install", "--upgrade", "pip"])
    if not args.skip_project:
        _run([
            str(pip),
            "install",
            "-e",
            str(ROOT / "memory"),
            "-e",
            str(ROOT / "loop_runner"),
            "-e",
            str(ROOT / "app"),
            "pytest",
        ])
    packages = {
        "boltz": ["boltz"],
        "boltz-api": ["boltz-api"],
        "both": ["boltz", "boltz-api"],
    }[args.package]
    _run([str(pip), "install", *packages])
    print(f"Installed {', '.join(packages)} into {venv}")
    print(f"Use {venv / 'bin' / 'boltz'} or add {venv / 'bin'} to PATH.")


def run_command(args: argparse.Namespace) -> None:
    if args.chat_agent and not args.workbench:
        raise SystemExit("--chat-agent requires --workbench so Codex can operate the memory API")
    if args.workbench:
        run_workbench_command(args)
        return
    if args.run_mode != "seed_and_loop":
        raise SystemExit("--run-mode seed_only requires --workbench")
    run_direct_command(args)


def run_direct_command(args: argparse.Namespace) -> None:
    from materialhack_agent.application import MaterialHackAgentApp
    from materialhack_memory import CandidateOrigin, MetricGoal, to_jsonable

    origin_map = {
        "ccdc_csd": CandidateOrigin.CCDC_CSD,
        "de_novo": CandidateOrigin.DE_NOVO,
    }
    goals = tuple(_parse_metric_goal(item, MetricGoal) for item in args.metric_goal)
    app = MaterialHackAgentApp()
    result = app.run(
        args.objective,
        seed_count=args.seed_count,
        loop_count=args.loops,
        target_score=args.target_score,
        rng_seed=args.seed,
        seed_sources=tuple(origin_map[source] for source in args.seed_source),
        optimization_goals=goals or None,
        boltz_command=args.boltz_command,
        enable_external_tools=args.enable_external_tools,
        prefer_boltz_api=not args.no_boltz_api,
        boltz_api_model=args.boltz_api_model,
        boltz_api_poll_interval_seconds=args.boltz_api_poll_interval_seconds,
        boltz_api_timeout_seconds=args.boltz_api_timeout_seconds,
        verifier_mcp_server=args.verifier_mcp_server,
        touchstone_command=args.touchstone_command,
        touchstone_use_uvx=not args.no_touchstone_uvx,
        touchstone_deep=args.touchstone_deep,
        touchstone_stress=args.touchstone_stress,
        touchstone_timeout_seconds=args.touchstone_timeout_seconds,
        boltz_accelerator=args.boltz_accelerator,
        boltz_model=args.boltz_model,
        boltz_cache=args.boltz_cache,
        boltz_use_msa_server=args.boltz_use_msa_server,
        boltz_msa_server_url=args.boltz_msa_server_url,
        boltz_msa_pairing_strategy=args.boltz_msa_pairing_strategy,
        boltz_timeout_seconds=args.boltz_timeout_seconds,
        boltz_recycling_steps=args.boltz_recycling_steps,
        boltz_sampling_steps=args.boltz_sampling_steps,
        boltz_diffusion_samples=args.boltz_diffusion_samples,
    )
    payload = {
        "run_id": result.run_id,
        "seed_pool_id": result.seed_flow.pool.pool_id,
        "seed_selection_decision_id": result.seed_flow.decision.decision_id,
        "selected_seed_candidate_id": result.seed_flow.selected_seed.seed_candidate_id,
        "ranked_seed_candidate_ids": list(result.seed_flow.decision.ranked_seed_candidate_ids),
        "runner_result": result.runner_result.__dict__ if result.runner_result else None,
        "memory": result.snapshot.to_frontend_payload(),
    }
    if args.json:
        print(json.dumps(to_jsonable(payload), indent=2, sort_keys=True))
        return

    active_node = next(node for node in result.snapshot.nodes if node.is_active)
    print("Novacore harness run complete")
    print(f"  run_id        : {result.run_id}")
    print(f"  selected_seed : {result.seed_flow.selected_seed.seed_candidate_id}")
    print(f"  active_loop   : {active_node.loop_id}")
    print(f"  loop_index    : {active_node.index}")
    print(f"  latest_metrics: {dict(sorted(active_node.latest_metrics.items()))}")
    if result.runner_result:
        print(f"  completed     : {result.runner_result.loops_completed}")
        print(f"  stop_reason   : {result.runner_result.stop_reason.value}")


def run_workbench_command(args: argparse.Namespace) -> None:
    from materialhack_memory import MetricGoal, to_jsonable

    goals = tuple(_parse_metric_goal(item, MetricGoal) for item in args.metric_goal)
    runtime = _ensure_workbench_runtime(args)
    payload = _workbench_run_payload(args, goals)
    run_response = _post_json(f"{runtime.api_base_url}/api/runs", payload, timeout=args.startup_timeout)
    run_id = str(run_response["run_id"])
    job_id = str(run_response["job_id"])
    memory_url = f"{runtime.ui_base_url}/memory/{run_id}"

    if not args.no_open_browser:
        _open_browser(memory_url)

    if not args.json:
        api_status = "started" if runtime.api_started else "reused"
        ui_status = "started" if runtime.ui_started else "reused"
        if args.chat_agent:
            print("Novacore chat-agent workbench seed started")
        else:
            print("Novacore live workbench run started")
        print(f"  run_id     : {run_id}")
        print(f"  job_id     : {job_id}")
        print(f"  api        : {runtime.api_base_url} ({api_status})")
        print(f"  memory_ui  : {memory_url} ({ui_status})")
        if args.no_open_browser:
            print("  browser    : not opened (--no-open-browser)")
        else:
            print("  browser    : opened")

    job = _wait_for_workbench_job(
        runtime.api_base_url,
        job_id,
        run_id=run_id,
        poll_interval=args.poll_interval,
        quiet=args.json,
    )
    memory = _get_json(f"{runtime.api_base_url}/api/runs/{run_id}/memory", timeout=args.startup_timeout)
    output = {
        "run_id": run_id,
        "job_id": job_id,
        "memory_url": memory_url,
        "api_url": runtime.api_base_url,
        "ui_url": runtime.ui_base_url,
        "job": job,
        "memory": memory,
    }
    if args.json:
        print(json.dumps(to_jsonable(output), indent=2, sort_keys=True))
        return

    active_node = next(node for node in memory["nodes"] if node["is_active"])
    if args.chat_agent:
        print("Novacore chat-agent workbench seed ready")
        print("  next_step     : Codex chat must call /agent-context, /agent-loops, evaluations, reflection, and finalize")
    else:
        print("Novacore live workbench run complete")
    print(f"  status        : {job['status']}")
    print(f"  active_loop   : {active_node['loop_id']}")
    print(f"  loop_index    : {active_node['index']}")
    print(f"  latest_metrics: {dict(sorted(active_node['latest_metrics'].items()))}")
    print(f"  memory_ui     : {memory_url}")


def _workbench_run_payload(args: argparse.Namespace, goals: Sequence[object]) -> dict[str, object]:
    from materialhack_agent.seed_flow import parse_objective
    from materialhack_memory import to_jsonable

    parsed = parse_objective(args.objective)
    run_mode = "seed_only" if args.chat_agent else args.run_mode
    return {
        "objective": args.objective,
        "target": parsed.target,
        "ph": parsed.ph,
        "functions": list(parsed.functions),
        "length": parsed.length,
        "seed_count": args.seed_count,
        "seed_sources": list(args.seed_source),
        "target_score": args.target_score,
        "loop_count": args.loops,
        "optimization_targets": [to_jsonable(goal) for goal in goals],
        "run_mode": run_mode,
        "rng_seed": args.seed,
    }


def _ensure_workbench_runtime(args: argparse.Namespace) -> WorkbenchRuntime:
    api_port, api_started = _ensure_api_server(args)
    api_base_url = f"http://{args.api_host}:{api_port}"
    ui_port, ui_started = _ensure_ui_server(
        ui_host=args.ui_host,
        preferred_ui_port=args.ui_port,
        api_base_url=api_base_url,
        startup_timeout=args.startup_timeout,
    )
    return WorkbenchRuntime(
        api_base_url=api_base_url,
        ui_base_url=f"http://{args.ui_host}:{ui_port}",
        api_port=api_port,
        ui_port=ui_port,
        api_started=api_started,
        ui_started=ui_started,
    )


def _ensure_api_server(args: argparse.Namespace) -> tuple[int, bool]:
    requires_fresh_api = bool(
        args.chat_agent
        or args.enable_external_tools
        or args.no_boltz_api
        or args.boltz_api_model != "boltz-2.1"
        or args.boltz_api_poll_interval_seconds != 5.0
        or args.boltz_api_timeout_seconds != 3600
        or args.verifier_mcp_server
        or args.touchstone_command != "touchstone"
        or args.no_touchstone_uvx
        or args.touchstone_deep
        or args.touchstone_stress
        or args.boltz_command != "boltz"
        or args.boltz_accelerator != "cpu"
        or args.boltz_model != "boltz2"
        or args.boltz_cache
        or args.boltz_use_msa_server
        or args.boltz_recycling_steps is not None
        or args.boltz_sampling_steps is not None
        or args.boltz_diffusion_samples is not None
    )
    for port in range(args.api_port, args.api_port + WORKBENCH_PORT_SCAN_LIMIT):
        if _is_novacore_api(args.api_host, port) and not requires_fresh_api:
            return port, False
        if _port_available(args.api_host, port):
            env = os.environ.copy()
            env["NOVACORE_BOLTZ_COMMAND"] = args.boltz_command
            env["NOVACORE_BOLTZ_ACCELERATOR"] = args.boltz_accelerator
            env["NOVACORE_BOLTZ_MODEL"] = args.boltz_model
            env["NOVACORE_PREFER_BOLTZ_API"] = "0" if args.no_boltz_api else "1"
            env["NOVACORE_BOLTZ_API_MODEL"] = args.boltz_api_model
            env["NOVACORE_BOLTZ_API_POLL_INTERVAL_SECONDS"] = str(args.boltz_api_poll_interval_seconds)
            env["NOVACORE_BOLTZ_API_TIMEOUT_SECONDS"] = str(args.boltz_api_timeout_seconds)
            env["NOVACORE_BOLTZ_MSA_SERVER_URL"] = args.boltz_msa_server_url
            env["NOVACORE_BOLTZ_MSA_PAIRING_STRATEGY"] = args.boltz_msa_pairing_strategy
            env["NOVACORE_BOLTZ_TIMEOUT_SECONDS"] = str(args.boltz_timeout_seconds)
            if args.boltz_cache:
                env["NOVACORE_BOLTZ_CACHE"] = args.boltz_cache
                env["BOLTZ_CACHE"] = args.boltz_cache
            if args.boltz_use_msa_server:
                env["NOVACORE_BOLTZ_USE_MSA_SERVER"] = "1"
            if args.boltz_recycling_steps is not None:
                env["NOVACORE_BOLTZ_RECYCLING_STEPS"] = str(args.boltz_recycling_steps)
            if args.boltz_sampling_steps is not None:
                env["NOVACORE_BOLTZ_SAMPLING_STEPS"] = str(args.boltz_sampling_steps)
            if args.boltz_diffusion_samples is not None:
                env["NOVACORE_BOLTZ_DIFFUSION_SAMPLES"] = str(args.boltz_diffusion_samples)
            if args.enable_external_tools:
                env["NOVACORE_ENABLE_EXTERNAL_TOOLS"] = "1"
            if args.verifier_mcp_server:
                env["NOVACORE_VERIFIER_MCP_SERVER"] = args.verifier_mcp_server
            env["NOVACORE_TOUCHSTONE_COMMAND"] = args.touchstone_command
            env["NOVACORE_TOUCHSTONE_USE_UVX"] = "0" if args.no_touchstone_uvx else "1"
            env["NOVACORE_TOUCHSTONE_TIMEOUT_SECONDS"] = str(args.touchstone_timeout_seconds)
            if args.touchstone_deep:
                env["NOVACORE_TOUCHSTONE_DEEP"] = "1"
            if args.touchstone_stress:
                env["NOVACORE_TOUCHSTONE_STRESS"] = "1"
            _start_background_process(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "materialhack_agent.workbench_api:app",
                    "--host",
                    args.api_host,
                    "--port",
                    str(port),
                ],
                name=f"api-{port}",
                cwd=ROOT,
                env=env,
            )
            _wait_for_json_health(f"http://{args.api_host}:{port}/api/health", args.startup_timeout)
            return port, True
    raise SystemExit(f"No available Novacore API port found near {args.api_port}.")


def _ensure_ui_server(
    *,
    ui_host: str,
    preferred_ui_port: int,
    api_base_url: str,
    startup_timeout: float,
) -> tuple[int, bool]:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm is required to start the Novacore memory workbench UI.")
    if not (WEB_ROOT / "node_modules").exists():
        _run([npm, "install"], cwd=WEB_ROOT)

    for port in range(preferred_ui_port, preferred_ui_port + WORKBENCH_PORT_SCAN_LIMIT):
        if _port_available(ui_host, port):
            env = os.environ.copy()
            env["NOVACORE_API_TARGET"] = api_base_url
            _start_background_process(
                [
                    npm,
                    "run",
                    "dev",
                    "--",
                    "--host",
                    ui_host,
                    "--port",
                    str(port),
                    "--strictPort",
                ],
                name=f"ui-{port}",
                cwd=WEB_ROOT,
                env=env,
            )
            _wait_for_http(f"http://{ui_host}:{port}", startup_timeout)
            return port, True
    raise SystemExit(f"No available Novacore UI port found near {preferred_ui_port}.")


def _start_background_process(command: list[str], *, name: str, cwd: Path, env: dict[str, str]) -> None:
    log_dir = ROOT / ".novacore" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    log_file = log_path.open("ab", buffering=0)
    subprocess.Popen(  # noqa: S603 - commands are fixed harness entrypoints with explicit args.
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _wait_for_workbench_job(
    api_base_url: str,
    job_id: str,
    *,
    run_id: str,
    poll_interval: float,
    quiet: bool,
) -> dict[str, object]:
    last_loop_id: str | None = None
    last_status: str | None = None
    while True:
        job = _get_json(f"{api_base_url}/api/jobs/{job_id}", timeout=10)
        status = str(job["status"])
        if not quiet and status != last_status:
            print(f"  job_status : {status}")
            last_status = status
        if status in JOB_TERMINAL_STATES:
            return job
        if not quiet:
            try:
                memory = _get_json(f"{api_base_url}/api/runs/{run_id}/memory", timeout=10)
                active_node = next(node for node in memory["nodes"] if node["is_active"])
                loop_id = str(active_node["loop_id"])
                if loop_id != last_loop_id:
                    print(f"  active_loop: {loop_id} (index {active_node['index']})")
                    last_loop_id = loop_id
            except Exception:
                pass
        time.sleep(max(poll_interval, 0.1))


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception:
        if sys.platform == "darwin":
            subprocess.run(["open", url], cwd=ROOT, check=False)


def _is_novacore_api(host: str, port: int) -> bool:
    try:
        payload = _get_json(f"http://{host}:{port}/api/health", timeout=1)
    except Exception:
        return False
    return payload.get("service") == "novacore-workbench"


def _wait_for_json_health(url: str, timeout: float) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _get_json(url, timeout=1)
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise SystemExit(f"Timed out waiting for {url}: {last_error}")


def _wait_for_http(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise SystemExit(f"Timed out waiting for {url}: {last_error}")


def _post_json(url: str, payload: dict[str, object], *, timeout: float) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _read_json_response(request, timeout=timeout)


def _get_json(url: str, *, timeout: float) -> dict[str, object]:
    request = urllib.request.Request(url, method="GET")
    return _read_json_response(request, timeout=timeout)


def _read_json_response(request: urllib.request.Request, *, timeout: float) -> dict[str, object]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{request.full_url} returned HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{request.full_url} did not return a JSON object")
    return payload


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def _preflight_checks(boltz_command: str, touchstone_command: str) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    checks.append({
        "name": "python",
        "ok": sys.version_info < (3, 13),
        "required": False,
        "detail": sys.version.split()[0],
        "remediation": "Use .venv/bin/python from `materialhack-novacore setup-boltz` for Boltz execution.",
    })
    checks.append(_import_check("materialhack_memory", required=True))
    checks.append(_import_check("materialhack_loop_runner", required=True))
    checks.append(_import_check("trs", required=True))
    checks.append(_import_check("fastapi", required=False))
    checks.append({
        "name": "ligands_10000.zip",
        "ok": (ROOT / "ligands_10000.zip").exists(),
        "required": False,
        "detail": str(ROOT / "ligands_10000.zip"),
        "remediation": "Place the CCDC/CSD ligand archive at the repository root.",
    })
    boltz_path = shutil.which(boltz_command) or _venv_binary(boltz_command)
    checks.append({
        "name": boltz_command,
        "ok": boltz_path is not None,
        "required": False,
        "detail": boltz_path or "not found on PATH or .venv/bin",
        "remediation": "Run `materialhack-novacore setup-boltz --package boltz`.",
    })
    checks.append(_import_check("boltz_api", required=False, remediation="Run `materialhack-novacore setup-boltz --package boltz-api` if using hosted Boltz."))
    checks.append({
        "name": "BOLTZ_API_KEY",
        "ok": bool(os.environ.get("BOLTZ_API_KEY")),
        "required": False,
        "detail": "configured" if os.environ.get("BOLTZ_API_KEY") else "not configured",
        "remediation": "Set BOLTZ_API_KEY or enter it in the workbench before evaluating loops.",
    })
    touchstone_path = shutil.which(touchstone_command) or _venv_binary(touchstone_command)
    checks.append({
        "name": touchstone_command,
        "ok": touchstone_path is not None,
        "required": False,
        "detail": touchstone_path or "not found on PATH or .venv/bin",
        "remediation": f"Use uvx fallback or install Touchstone from {TOUCHSTONE_UVX_SPEC}.",
    })
    uvx_path = shutil.which("uvx") or _venv_binary("uvx")
    checks.append({
        "name": "touchstone uvx fallback",
        "ok": uvx_path is not None,
        "required": False,
        "detail": uvx_path or "not found on PATH or .venv/bin",
        "remediation": "Install uv so Novacore can run Touchstone via uvx from the verifier PR.",
    })
    checks.append({
        "name": "touchstone MCP config",
        "ok": (ROOT / ".mcp.json").exists(),
        "required": False,
        "detail": str(ROOT / ".mcp.json") if (ROOT / ".mcp.json").exists() else "not configured",
        "remediation": "Restore .mcp.json from the Touchstone verifier PR.",
    })
    return checks


def _import_check(module_name: str, *, required: bool, remediation: str | None = None) -> dict[str, object]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        return {
            "name": module_name,
            "ok": False,
            "required": required,
            "detail": f"{type(exc).__name__}: {exc}",
            "remediation": remediation or "Install local packages with `pip install -e memory -e loop_runner -e app`.",
        }
    return {
        "name": module_name,
        "ok": True,
        "required": required,
        "detail": "importable",
    }


def _parse_metric_goal(spec: str, metric_goal_cls):
    parts = spec.split(":")
    if len(parts) not in {3, 4}:
        raise SystemExit(f"Invalid --metric-goal {spec!r}; expected NAME:COMPARATOR:TARGET[:WEIGHT]")
    name, comparator, target = parts[:3]
    if comparator not in {"gte", "lte", "eq"}:
        raise SystemExit(f"Invalid comparator {comparator!r}; use gte, lte, or eq")
    weight = float(parts[3]) if len(parts) == 4 else 1.0
    return metric_goal_cls(name=name, comparator=comparator, target=float(target), weight=weight)


def _select_python(explicit: str | None) -> str:
    if explicit:
        return explicit
    for candidate in ("python3.11", "python3.12", "python3"):
        path = shutil.which(candidate)
        if path:
            return path
    raise SystemExit("No Python executable found")


def _python_version(executable: str) -> tuple[int, int]:
    text = _python_version_text(executable)
    major, minor, *_ = text.split(".")
    return int(major), int(minor)


def _python_version_text(executable: str) -> str:
    result = subprocess.run(
        [executable, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _venv_binary(name: str) -> str | None:
    path = ROOT / ".venv" / "bin" / name
    return str(path) if path.exists() else None


def _run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


if __name__ == "__main__":
    main()
