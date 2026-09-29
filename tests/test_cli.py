import json
import stat

import pytest
from conftest import write

from preflight import cli

CHECKS = """
from pathlib import Path

from preflight import Section, check, fail, ok, outcome


class Flag(Section):
    good: bool = True


class Touch(Section):
    file: str
    marker: str = ""


@check("sample.flag", section=Flag)
def flag(ctx, s):
    return outcome(ok() if s.good else fail(do="Flip the flag."))


@check("sample.touch", section=Touch)
def touch(ctx, s):
    with open(Path(ctx.root, s.file), "a") as handle:
        handle.write("# touched\\n")
    return outcome(ok())
"""

GATE = """
from preflight import Gate
from consumer.checks.flags import flag, touch

gate = Gate({name!r}, checks=[{checks}], requires={requires!r}, scope={scope!r})
"""

DEV = """
schema_version = 1
environment = "dev"

[flags]
good = {good}

[touch]
file = "envs/dev.tfvars"
marker = {{ tfvars = "envs/dev.tfvars", key = "marker" }}
"""


def gate(repo, name, checks, requires=(), scope="environment"):
    write(
        repo,
        f"preflight/gates/{name}.py",
        GATE.format(name=name, checks=checks, requires=list(requires), scope=scope),
    )
    return repo / "preflight" / "gates" / f"{name}.py"


def consumer(repo, good="true"):
    write(repo, "preflight/checks/flags.py", CHECKS)
    write(repo, "envs/dev.tfvars", 'marker = "m"\n')
    write(repo, "preflight/contracts/dev.toml", DEV.format(good=good))
    gate(repo, "touch", 'touch("touch")')
    return gate(repo, "g", 'flag("flags")'), repo / "preflight" / "contracts" / "dev.toml"


def test_check_passes(repo, capsys):
    gate_path, contract = consumer(repo)
    assert cli.main(["check", str(gate_path), "--contract", str(contract)]) == 0
    assert capsys.readouterr().out.endswith("Nothing open.\n")


def test_check_fails_and_names_the_next_step(repo, capsys):
    gate_path, contract = consumer(repo, good="false")
    assert cli.main(["check", str(gate_path), "--contract", str(contract)]) == 1
    out = capsys.readouterr().out
    assert "> NEXT  sample.flag[flags]\n        Flip the flag.\n" in out
    assert "\033" not in out  # piped output carries no colour


def test_an_invalid_contract_is_exit_2(repo, capsys):
    _, contract = consumer(repo)
    bad = gate(repo, "bad", 'flag("nope")')
    assert cli.main(["check", str(bad), "--contract", str(contract)]) == 2
    assert "no section [nope]" in capsys.readouterr().err


def test_a_broken_gate_file_is_exit_2_naming_it(repo, capsys):
    _, contract = consumer(repo)
    broken = write(repo, "preflight/gates/broken.py", "gate = (\n")
    assert cli.main(["check", str(broken), "--contract", str(contract)]) == 2
    assert "broken.py" in capsys.readouterr().err


def test_json_and_junit_reports(repo, tmp_path_factory):
    gate_path, contract = consumer(repo, good="false")
    out = tmp_path_factory.mktemp("out")
    code = cli.main(
        [
            "check",
            str(gate_path),
            "--contract",
            str(contract),
            "--json",
            str(out / "r.json"),
            "--junit",
            str(out / "r.xml"),
        ]
    )
    assert code == 1
    data = json.loads((out / "r.json").read_text())
    assert (data["exit_code"], data["next"]) == (1, "sample.flag[flags]")
    assert data["runs"][0]["gate"] == "g"
    assert stat.S_IMODE((out / "r.json").stat().st_mode) == 0o600
    assert "<failure" in (out / "r.xml").read_text()


def test_relative_paths_from_another_directory(repo, monkeypatch):
    consumer(repo)
    monkeypatch.chdir(repo / "preflight")
    assert cli.main(["check", "gates/g.py", "--contract", "contracts/dev.toml"]) == 0


def test_inputs_changed_during_the_run_block(repo, capsys):
    _, contract = consumer(repo)
    touch = repo / "preflight" / "gates" / "touch.py"
    assert cli.main(["check", str(touch), "--contract", str(contract)]) == 1
    assert "Inputs changed during the run: envs/dev.tfvars" in capsys.readouterr().out


def test_a_repository_gate_needs_repo_toml(repo, capsys):
    _, contract = consumer(repo)
    gate(repo, "repo_gate", 'flag("flags")', scope="repository")
    needs = gate(repo, "needs", 'flag("flags")', requires=["repo_gate"])
    assert cli.main(["check", str(needs), "--contract", str(contract)]) == 2
    assert "repo.toml" in capsys.readouterr().err


def test_an_internal_error_is_exit_3(repo, monkeypatch, capsys):
    gate_path, contract = consumer(repo)

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(cli, "run_plan", boom)
    assert cli.main(["check", str(gate_path), "--contract", str(contract)]) == 3
    assert "internal error (RuntimeError" in capsys.readouterr().err


def test_usage_errors_are_exit_2():
    with pytest.raises(SystemExit) as caught:
        cli.main(["check", "gates/g.py"])
    assert caught.value.code == 2


def test_validate(repo, monkeypatch, capsys):
    consumer(repo)
    monkeypatch.chdir(repo)
    assert cli.main(["validate"]) == 0
    assert "valid" in capsys.readouterr().out
    contract = repo / "preflight" / "contracts" / "dev.toml"
    contract.write_text(contract.read_text().replace("good = true", "good = true\nstray = 1"))
    assert cli.main(["validate"]) == 2
    assert "[flags].stray is used by no check" in capsys.readouterr().err


def test_internal_error_does_not_leak_the_message(repo, monkeypatch, capsys):
    gate_path, contract = consumer(repo)

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(cli, "run_plan", boom)
    assert cli.main(["check", str(gate_path), "--contract", str(contract)]) == 3
    err = capsys.readouterr().err
    assert "internal error (RuntimeError)" in err
    assert "bug" not in err


def test_repo_toml_must_have_repository_scope(repo, capsys):
    _, contract = consumer(repo)
    gate(repo, "repo_gate", 'flag("flags")', scope="repository")
    needs = gate(repo, "needs", 'flag("flags")', requires=["repo_gate"])
    write(repo, "preflight/contracts/repo.toml", DEV.format(good="true"))
    assert cli.main(["check", str(needs), "--contract", str(contract)]) == 2
    assert 'must have scope = "repository"' in capsys.readouterr().err


def test_validate_never_runs_checks(repo, monkeypatch):
    consumer(repo)
    monkeypatch.chdir(repo)

    def boom(*args, **kwargs):
        raise RuntimeError("ran")

    monkeypatch.setattr(cli, "run_plan", boom)
    assert cli.main(["validate"]) == 0


def test_exit_2_writes_no_report_files(repo, tmp_path_factory):
    _, contract = consumer(repo)
    bad = gate(repo, "bad", 'flag("nope")')
    out = tmp_path_factory.mktemp("out")
    code = cli.main(
        [
            "check",
            str(bad),
            "--contract",
            str(contract),
            "--json",
            str(out / "r.json"),
            "--junit",
            str(out / "r.xml"),
        ]
    )
    assert code == 2
    assert list(out.iterdir()) == []


def test_a_missing_contract_is_exit_2(repo, capsys):
    gate_path, _ = consumer(repo)
    missing = repo / "preflight" / "contracts" / "nope.toml"
    assert cli.main(["check", str(gate_path), "--contract", str(missing)]) == 2
