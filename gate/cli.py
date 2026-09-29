"""Run the complete configured suite before optionally executing an argv command."""

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from gate.contract import load_contract

ROOT = Path(__file__).resolve().parents[1]


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return number


def run_suite(command, *, env, timeout):
    # A separate process group lets a POSIX timeout stop subprocesses of a check too.
    process = subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=(os.name == "posix"))
    try:
        return process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()
        raise


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    child = []
    if "--" in argv:
        index = argv.index("--")
        child, argv = argv[index + 1 :], argv[:index]
        if not child:
            print("A command must follow --", file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--suite-timeout", type=positive_seconds, default=600)
    parser.add_argument("--test-timeout", type=positive_seconds, default=60)
    args = parser.parse_args(argv)

    try:
        contract_path = args.contract.resolve(strict=True)
        contract, digest = load_contract(contract_path)
        paths = []
        for module in contract.modules:
            path = ROOT / "checks" / module
            if not path.is_dir():
                raise ValueError(f"Missing required module: {module}")
            paths.append(str(path))
    except Exception as exc:
        print(f"BLOCKED: invalid preflight contract or module: {exc}", file=sys.stderr)
        return 2

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    try:
        directory = args.report_dir.resolve() / run_id
        directory.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        print(f"BLOCKED: cannot create report directory: {exc}", file=sys.stderr)
        return 2
    print(f"Preflight {contract.environment}; reports: {directory}", flush=True)
    env = os.environ.copy()
    env["PYTEST_ADDOPTS"] = ""
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_PLUGINS", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(ROOT / "pyproject.toml"),
        "--rootdir",
        str(ROOT),
        "--confcutdir",
        str(ROOT),
        "-o",
        "addopts=",
        "-o",
        "pythonpath=.",
        "-p",
        "gate.plugin",
        "-p",
        "testinfra.plugin",
        "-p",
        "pytest_metadata.plugin",
        "-p",
        "pytest_html.plugin",
        "-p",
        "pytest_html.fixtures",
        "-p",
        "pytest_timeout",
        "--preflight-contract",
        str(contract_path),
        "--preflight-report",
        str(directory / "gate.json"),
        "--html",
        str(directory / "report.html"),
        "--self-contained-html",
        "--junitxml",
        str(directory / "junit.xml"),
        "--timeout",
        str(args.test_timeout),
        "--maxfail=0",
        "--strict-markers",
        "--strict-config",
        "--import-mode=importlib",
        "--tb=short",
        "--no-showlocals",
        "-v",
        "-ra",
        *paths,
    ]
    reason = None
    try:
        result = run_suite(command, env=env, timeout=args.suite_timeout)
        data = json.loads((directory / "gate.json").read_text())
        fresh = hashlib.sha256(contract_path.read_bytes()).hexdigest() == digest
        if not (
            result == 0
            and data.get("complete") is True
            and data.get("passed") is True
            and data.get("contract_sha256") == digest
            and fresh
        ):
            reason = "one or more checks failed, were incomplete, or the contract changed"
    except subprocess.TimeoutExpired:
        reason = f"preflight suite timed out after {args.suite_timeout:g} seconds"
    except KeyboardInterrupt:
        reason = "preflight interrupted"
    except (OSError, ValueError) as exc:
        reason = f"preflight could not produce complete evidence: {exc}"
    if reason:
        print(f"BLOCKED: {reason}. See {directory}", file=sys.stderr)
        (directory / "runner.json").write_text(
            json.dumps({"passed": False, "reason": reason}, indent=2) + "\n"
        )
        return 1
    print("Preflight passed.", flush=True)
    if not child:
        return 0
    try:
        # No shell: argv, environment, and caller's cwd are retained.
        result = subprocess.run(child, check=False)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except OSError as exc:
        print(f"Could not start command: {exc}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130
