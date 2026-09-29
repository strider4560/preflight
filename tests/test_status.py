# tests/test_status.py
import json

from conftest import write
from test_cli import CHECKS, gate

from preflight import cli
from preflight.outcome import NextStep, Status
from preflight.render import OpenStep, render_status

REPO_TOML = 'schema_version = 1\nscope = "repository"\n\n[repo_flag]\ngood = true\n'
ENV_TOML = """schema_version = 1
environment = "{env}"

[boot]
good = {boot}

[later_flag]
good = {later}

[entry]
good = true
"""

EXPECTED = """preflight status

repository
  satisfied  repository

dev
  open (1)   bootstrap
  waiting    later  (waits on bootstrap)

prod
  satisfied  bootstrap
  satisfied  later

Open steps:
> NEXT  dev: sample.flag[boot]
        Flip the flag.

Also open, in gates still waiting:
  1.    dev: sample.flag[later_flag]
        Flip the flag.
"""


def consumer(repo, dev_boot="false"):
    write(repo, "preflight/checks/flags.py", CHECKS)
    write(repo, "preflight/contracts/repo.toml", REPO_TOML)
    write(
        repo,
        "preflight/contracts/dev.toml",
        ENV_TOML.format(env="dev", boot=dev_boot, later="false"),
    )
    write(
        repo,
        "preflight/contracts/prod.toml",
        ENV_TOML.format(env="prod", boot="true", later="true"),
    )
    gate(repo, "repository", 'flag("repo_flag")', scope="repository")
    gate(repo, "bootstrap", 'flag("boot")', requires=["repository"])
    gate(repo, "later", 'flag("later_flag")', requires=["bootstrap"])
    write(
        repo,
        "preflight/gates/bootstrap_entry.py",
        "from preflight import Gate\nfrom consumer.checks.flags import flag\n\n"
        'gate = Gate("bootstrap_entry", checks=[flag("entry")], requires=["repository"], '
        'guards="scripts/bootstrap.sh")\n',
    )


def test_status_shows_every_environment_and_one_next_step(repo, monkeypatch, capsys):
    consumer(repo)
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 1
    out = capsys.readouterr().out
    assert out == EXPECTED
    assert "bootstrap_entry" not in out


def test_status_passes_when_everything_is_satisfied(repo, monkeypatch, capsys):
    consumer(repo, dev_boot="true")
    contract = repo / "preflight" / "contracts" / "dev.toml"
    contract.write_text(
        contract.read_text().replace("[later_flag]\ngood = false", "[later_flag]\ngood = true")
    )
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out.endswith("Nothing open.\n")


def test_status_without_contracts_is_exit_2(repo, monkeypatch, capsys):
    consumer(repo)
    for path in (repo / "preflight" / "contracts").glob("*.toml"):
        path.unlink()
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 2
    assert "holds no contracts" in capsys.readouterr().err


def test_status_with_environment_gates_but_no_environment_contract_is_exit_2(
    repo, monkeypatch, capsys
):
    consumer(repo)
    (repo / "preflight" / "contracts" / "dev.toml").unlink()
    (repo / "preflight" / "contracts" / "prod.toml").unlink()
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 2
    assert "have no environment contract" in capsys.readouterr().err


def test_status_open_repository_gate_holds_every_environment_waiting(repo, monkeypatch, capsys):
    consumer(repo, dev_boot="true")
    contracts = repo / "preflight" / "contracts"
    (contracts / "repo.toml").write_text(REPO_TOML.replace("good = true", "good = false"))
    (contracts / "dev.toml").write_text(ENV_TOML.format(env="dev", boot="true", later="true"))
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "repository\n  open (1)   repository\n" in out
    for env in ("dev", "prod"):
        assert f"{env}\n  waiting    bootstrap  (waits on repository)\n" in out
    assert "> NEXT  repository: sample.flag[repo_flag]" in out


def test_render_status_with_only_waiting_steps_says_so():
    # Through the CLI every unsatisfied gate traces back to an open gate with its own
    # steps, so "main empty, also non-empty" is reachable only by calling the renderer.
    step = OpenStep(
        "dev: sample.flag[later_flag]", "n", Status.FAIL, NextStep("Flip the flag."), (), ()
    )
    out = render_status([("dev", [("waiting", "later", ["bootstrap"])])], [], [step])
    assert "Nothing open outside gates still waiting.\n" in out
    assert "Also open, in gates still waiting:\n  1.    dev: sample.flag[later_flag]" in out
    assert "Open steps:" not in out


def test_status_json_lists_every_run_and_every_open_item(repo, monkeypatch, tmp_path):
    consumer(repo)
    monkeypatch.chdir(repo)
    target = tmp_path / "status.json"
    assert cli.main(["status", "--json", str(target)]) == 1
    report = json.loads(target.read_text())
    assert report["command"] == "status"
    assert report["exit_code"] == 1
    assert [(r["gate"], r["environment"]) for r in report["runs"]] == [
        ("repository", None),
        ("bootstrap", "dev"),
        ("later", "dev"),
        ("bootstrap", "prod"),
        ("later", "prod"),
    ]
    assert report["open"] == ["dev: sample.flag[boot]", "dev: sample.flag[later_flag]"]
    assert report["next"] == "dev: sample.flag[boot]"


def test_status_with_no_milestone_gates_is_exit_2(repo, monkeypatch, capsys):
    write(repo, "preflight/checks/flags.py", CHECKS)
    write(
        repo, "preflight/contracts/dev.toml", ENV_TOML.format(env="dev", boot="true", later="true")
    )
    write(
        repo,
        "preflight/gates/bootstrap_entry.py",
        "from preflight import Gate\nfrom consumer.checks.flags import flag\n\n"
        'gate = Gate("bootstrap_entry", checks=[flag("entry"), flag("boot"), flag("later_flag")], '
        'guards="scripts/bootstrap.sh")\n',
    )
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 2
    assert "no milestone gates to check (every gate has guards=)" in capsys.readouterr().err
