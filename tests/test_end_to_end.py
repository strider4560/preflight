"""The CLI against a consumer repository: real worker processes, real testinfra, no network."""

import json
import subprocess
import sys

from conftest import write

LOCAL = """
from preflight import Section, check, ok, outcome


class Marker(Section):
    value: str


@check("e2e.marker", section=Marker)
def marker(ctx, s):
    return outcome(ok(observed=s.value))
"""

GATE = """
from preflight import Gate
from preflight.catalog import files
from consumer.checks.local import marker

gate = Gate("ready", checks=[files.present("required_files"), marker("marker")])
"""

CONTRACT = """
schema_version = 1
environment = "dev"

[required_files]
paths = ["README.md"]

[marker]
value = { tfvars = "envs/dev.tfvars", key = "marker", placeholder = ["CHANGEME"], how = "put the marker in envs/dev.tfvars" }
"""  # noqa: E501


def preflight(repo, *args):
    return subprocess.run(
        [sys.executable, "-m", "preflight", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=180,
    )


def setup(repo, marker):
    write(repo, "preflight/checks/local.py", LOCAL)
    write(repo, "preflight/gates/ready.py", GATE)
    write(repo, "preflight/contracts/dev.toml", CONTRACT)
    write(repo, "envs/dev.tfvars", f'marker = "{marker}"\n')


CHECK = ("check", "preflight/gates/ready.py", "--contract", "preflight/contracts/dev.toml")


def test_an_unfinished_repository_names_each_step(repo):
    setup(repo, "CHANGEME")
    result = preflight(repo, *CHECK, "--json", "report.json")
    assert result.returncode == 1, result.stderr
    report = json.loads((repo / "report.json").read_text())
    assert report["open"] == [
        "files.present[required_files]:README.md",
        "contract.placeholder[marker.value]",
    ]
    statuses = {i["id"]: i["status"] for run in report["runs"] for i in run["instances"]}
    assert statuses["e2e.marker[marker]"] == "blocked"
    assert "> NEXT  files.present[required_files]:README.md" in result.stdout


def test_a_finished_repository_passes_check_status_and_validate(repo):
    setup(repo, "m")
    write(repo, "README.md", "hello\n")
    check = preflight(repo, *CHECK, "--json", "report.json")
    assert check.returncode == 0, check.stdout + check.stderr
    report = json.loads((repo / "report.json").read_text())
    instances = {i["id"]: i for run in report["runs"] for i in run["instances"]}
    marker = instances["e2e.marker[marker]"]
    assert marker["status"] == "ok"
    assert [item["observed"] for item in marker["items"]] == ["m"]
    status = preflight(repo, "status")
    assert status.returncode == 0, status.stdout + status.stderr
    assert "  satisfied  ready" in status.stdout
    assert preflight(repo, "validate").returncode == 0
    assert list((repo / "preflight").rglob("__pycache__")) == []
