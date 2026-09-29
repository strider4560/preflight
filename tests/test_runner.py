"""Behavioral tests using real pytest subprocesses; no AWS calls."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "preflight"
    root.mkdir()
    for name in ("gate", "run.py"):
        source = SOURCE / name
        if source.is_dir():
            shutil.copytree(source, root / name, ignore=shutil.ignore_patterns("__pycache__"))
        elif source.exists():
            shutil.copy2(source, root / name)
    (root / "checks/custom").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (root / "contract.toml").write_text(
        'schema_version = 1\nenvironment = "selftest"\nregion = "us-east-1"\nmodules = ["custom"]\n'
    )
    return root


def run_gate(root, source, *, options=(), env=None, child_code=None):
    (root / "checks/custom/test_check.py").write_text(source)
    sentinel = root.parent / "operation-ran"
    child = child_code or f"from pathlib import Path; Path({str(sentinel)!r}).touch()"
    command = [
        sys.executable,
        str(root / "run.py"),
        "--contract",
        str(root / "contract.toml"),
        "--report-dir",
        str(root / "reports"),
        *options,
        "--",
        sys.executable,
        "-c",
        child,
    ]
    clean_env = dict(os.environ, AWS_EC2_METADATA_DISABLED="true", PYTEST_ADDOPTS="")
    clean_env.update(env or {})
    result = subprocess.run(
        command,
        cwd=root.parent,
        env=clean_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    return result, sentinel


def report(root):
    paths = list((root / "reports").glob("*/gate.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_success_runs_command_in_callers_directory_and_writes_reports(project):
    result, _ = run_gate(
        project,
        "def test_ready(): assert True\n",
        child_code="from pathlib import Path; Path('operation-ran').write_text(str(Path.cwd()))",
    )
    assert result.returncode == 0, result.stdout
    assert (project.parent / "operation-ran").read_text() == str(project.parent)
    data = report(project)
    assert data["passed"] is True
    assert data["collected"] == 1
    directory = next((project / "reports").iterdir())
    assert (directory / "report.html").is_file()
    assert (directory / "junit.xml").is_file()


@pytest.mark.parametrize(
    "source",
    [
        "def test_ready(): assert False, 'configuration wrong'\n",
        "import pytest\ndef test_ready(): pytest.skip('cannot verify')\n",
        "import pytest\n@pytest.mark.xfail\ndef test_ready(): assert False\n",
        "import pytest\n@pytest.mark.xfail\ndef test_ready(): assert True\n",
        "import pytest\npytest.skip('module unavailable', allow_module_level=True)\n",
        "raise RuntimeError('collector unavailable')\n",
        "# no checks at all\n",
    ],
)
def test_nonpassing_or_uncollected_checks_block_command(project, source):
    result, sentinel = run_gate(project, source)
    assert result.returncode != 0
    assert not sentinel.exists()
    assert report(project)["passed"] is False


def test_fixture_failure_does_not_prevent_independent_checks(project):
    source = """import pytest
@pytest.fixture
def unavailable():
    raise RuntimeError("credentials unavailable")
def test_dependent(unavailable): pass
def test_independent(): assert True
"""
    result, sentinel = run_gate(project, source)
    assert result.returncode != 0
    assert not sentinel.exists()
    data = report(project)
    assert any(
        r["nodeid"].endswith("test_independent")
        and r["when"] == "call"
        and r["outcome"] == "passed"
        for r in data["results"]
    )


def test_missing_module_blocks_even_when_other_module_passes(project):
    path = project / "contract.toml"
    path.write_text(path.read_text().replace('["custom"]', '["custom", "missing"]'))
    result, sentinel = run_gate(project, "def test_ready(): pass\n")
    assert result.returncode != 0
    assert not sentinel.exists()
    assert "Missing required module: missing" in result.stdout


def test_empty_required_module_blocks_otherwise_passing_suite(project):
    (project / "checks/empty").mkdir()
    path = project / "contract.toml"
    path.write_text(path.read_text().replace('["custom"]', '["custom", "empty"]'))
    result, sentinel = run_gate(project, "def test_ready(): pass\n")
    assert result.returncode != 0
    assert not sentinel.exists()
    assert "empty" in " ".join(report(project)["blocking_reasons"])


def test_inherited_pytest_options_cannot_hide_failing_checks(project):
    result, sentinel = run_gate(
        project,
        "def test_good(): pass\ndef test_bad(): assert False\n",
        env={"PYTEST_ADDOPTS": "-k good --collect-only"},
    )
    assert result.returncode != 0
    assert not sentinel.exists()
    assert report(project)["collected"] == 2


def test_chained_command_exit_code_is_preserved(project):
    result, _ = run_gate(project, "def test_ready(): pass\n", child_code="raise SystemExit(7)")
    assert result.returncode == 7, result.stdout
    assert report(project)["passed"] is True


def test_timeout_blocks_command(project):
    result, sentinel = run_gate(
        project,
        "import time\ndef test_stuck(): time.sleep(10)\n",
        options=("--suite-timeout", "1"),
    )
    assert result.returncode != 0
    assert not sentinel.exists()
    assert "timed out" in result.stdout.lower()


@pytest.mark.parametrize(
    "extra",
    [
        "example = true\n",
        "misspelled_option = true\n",
    ],
)
def test_unsafe_or_invalid_contract_blocks_before_tests(project, extra):
    path = project / "contract.toml"
    path.write_text(path.read_text() + extra)
    result, sentinel = run_gate(project, "def test_ready(): pass\n")
    assert result.returncode != 0
    assert not sentinel.exists()
    assert "contract" in result.stdout.lower()


def test_parent_conftest_is_not_loaded(project):
    (project.parent / "conftest.py").write_text("raise RuntimeError('parent suite leaked in')\n")
    result, sentinel = run_gate(project, "def test_ready(): pass\n")
    assert result.returncode == 0, result.stdout
    assert sentinel.exists()


def test_teardown_failure_blocks_command(project):
    source = """import pytest
@pytest.fixture
def resource():
    yield
    raise RuntimeError('cleanup failed')
def test_ready(resource): pass
"""
    result, sentinel = run_gate(project, source)
    assert result.returncode != 0
    assert not sentinel.exists()
    assert report(project)["passed"] is False


def test_zero_exit_without_completion_report_blocks_command(project):
    result, sentinel = run_gate(project, "import os\ndef test_aborted(): os._exit(0)\n")
    assert result.returncode != 0
    assert not sentinel.exists()
    assert "complete evidence" in result.stdout


def test_contract_change_during_run_blocks_command(project):
    source = """from pathlib import Path
def test_ready():
    path = Path('contract.toml')
    path.write_text(path.read_text() + '\\n[settings]\\nchanged = true\\n')
"""
    result, sentinel = run_gate(project, source)
    assert result.returncode != 0
    assert not sentinel.exists()
    assert "contract changed" in result.stdout


def test_deselected_checks_block_command(project):
    (
        project / "checks/custom/conftest.py"
    ).write_text("""def pytest_collection_modifyitems(config, items):
    removed = items[1:]
    del items[1:]
    config.hook.pytest_deselected(items=removed)
""")
    result, sentinel = run_gate(project, "def test_one(): pass\ndef test_two(): pass\n")
    assert result.returncode != 0
    assert not sentinel.exists()
    assert any("deselected" in r for r in report(project)["blocking_reasons"])


def test_owner_is_in_structured_report(project):
    source = 'import pytest\n@pytest.mark.owner("network-platform")\ndef test_ready(): pass\n'
    result, _ = run_gate(project, source)
    assert result.returncode == 0, result.stdout
    calls = [r for r in report(project)["results"] if r["when"] == "call"]
    assert calls[0]["owner"] == "network-platform"
