"""Gate semantics: every selected check must complete setup, call and teardown."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gate.contract import load_contract

CONTRACT = pytest.StashKey()
RESULTS = pytest.StashKey()


def pytest_addoption(parser):
    group = parser.getgroup("preflight")
    group.addoption("--preflight-contract", required=True)
    group.addoption("--preflight-report", required=True)


def pytest_configure(config):
    path = Path(config.getoption("--preflight-contract"))
    try:
        contract, digest = load_contract(path)
    except Exception as exc:
        raise pytest.UsageError(f"Invalid preflight contract: {exc}") from exc
    config.stash[CONTRACT] = contract
    config.stash[RESULTS] = {
        "schema_version": 1,
        "contract_sha256": digest,
        "environment": contract.environment,
        "region": contract.region,
        "modules": contract.modules,
        "started_at": datetime.now(UTC).isoformat(),
        "collected": 0,
        "nodeids": [],
        "module_counts": dict.fromkeys(contract.modules, 0),
        "results": [],
        "blocking_reasons": [],
        "passed": False,
        "complete": False,
    }
    config.addinivalue_line("markers", "owner(name): prerequisite owner")
    config.pluginmanager.register(Recorder(config), "preflight-recorder")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    marker = item.get_closest_marker("owner")
    if marker and marker.args:
        report.user_properties.append(("owner", str(marker.args[0])))


class Recorder:
    def __init__(self, config):
        self.config = config
        self.data = config.stash[RESULTS]

    def pytest_collection_finish(self, session):
        self.data["nodeids"] = [item.nodeid for item in session.items]
        self.data["collected"] = len(session.items)
        root = Path(str(self.config.rootpath)) / "checks"
        for item in session.items:
            for name in self.data["modules"]:
                if Path(item.path).resolve().is_relative_to((root / name).resolve()):
                    self.data["module_counts"][name] += 1

    def pytest_deselected(self, items):
        if items:
            self.data["blocking_reasons"].append(f"{len(items)} required checks were deselected")

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.data["blocking_reasons"].append(
                f"Collection {report.outcome}: {report.nodeid}\n{report.longrepr}"
            )

    def pytest_runtest_logreport(self, report):
        result = {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "duration": report.duration,
            "expected_failure": hasattr(report, "wasxfail"),
            "owner": dict(report.user_properties).get("owner"),
        }
        if report.failed or report.skipped:
            result["details"] = report.longreprtext
        self.data["results"].append(result)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        reasons = self.data["blocking_reasons"]
        if exitstatus != 0:
            reasons.append(f"pytest exited with status {int(exitstatus)}")
        if self.config.option.collectonly:
            reasons.append("Collection-only execution cannot satisfy a gate")
        for name, count in self.data["module_counts"].items():
            if count == 0:
                reasons.append(f"Required module {name!r} collected no checks")
        if not self.data["nodeids"]:
            reasons.append("No checks were collected")
        stages = {}
        for result in self.data["results"]:
            stages.setdefault(result["nodeid"], []).append(result)
        for nodeid in self.data["nodeids"]:
            reports = stages.get(nodeid, [])
            clean = (
                len(reports) == 3
                and {r["when"] for r in reports} == {"setup", "call", "teardown"}
                and all(r["outcome"] == "passed" and not r["expected_failure"] for r in reports)
            )
            if not clean:
                reasons.append(f"Required check did not fully pass: {nodeid}")
        self.data["complete"] = True
        self.data["passed"] = not reasons
        self.data["finished_at"] = datetime.now(UTC).isoformat()
        if reasons and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        self.data["pytest_exit_code"] = int(session.exitstatus)
        target = Path(self.config.getoption("--preflight-report"))
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n")
        temporary.replace(target)

    def pytest_terminal_summary(self, terminalreporter):
        reasons = self.data["blocking_reasons"]
        terminalreporter.section("Preflight gate")
        if reasons:
            terminalreporter.write_line("BLOCKED: every mandatory check must pass.", red=True)
            for reason in reasons:
                terminalreporter.write_line(reason)
        else:
            terminalreporter.write_line("PASS: all mandatory checks completed.", green=True)


@pytest.fixture(scope="session")
def contract(pytestconfig):
    return pytestconfig.stash[CONTRACT]
