# tests/test_status.py
from conftest import write
from test_cli import CHECKS, gate

from preflight import cli

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
