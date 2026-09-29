import shlex

from fakes import FakeHost, FakeResult, make_ctx

from preflight.catalog import files, git, sops
from preflight.outcome import Status

SHA = "a" * 40


def run(check, section, tmp_path, host):
    return check.observe(make_ctx(tmp_path, host=host), section)


def test_up_to_date(tmp_path):
    section = git.UpToDateSection()
    remote = ("ls-remote", FakeResult(0, f"{SHA}\trefs/heads/main\n"))
    ok_host = FakeHost([remote, ("cat-file", FakeResult(0)), ("merge-base", FakeResult(0))])
    assert run(git.up_to_date, section, tmp_path, ok_host).status is Status.OK
    assert "refs/heads/main" in ok_host.commands[0]
    behind = FakeHost([remote, ("cat-file", FakeResult(0)), ("merge-base", FakeResult(1))])
    item = run(git.up_to_date, section, tmp_path, behind).items[0]
    assert (item.status, item.next_step.paste) == (
        Status.FAIL,
        "git fetch origin && git rebase origin/main",
    )
    unfetched = FakeHost([remote, ("cat-file", FakeResult(128))])
    assert run(git.up_to_date, section, tmp_path, unfetched).status is Status.FAIL
    offline = FakeHost([("ls-remote", FakeResult(128))])
    assert run(git.up_to_date, section, tmp_path, offline).items[0].error_type == "GitRemote"
    assert "GIT_TERMINAL_PROMPT=0" in offline.commands[0]
    assert not any("fetch" in c for c in ok_host.commands + behind.commands + offline.commands)


def test_up_to_date_missing_branch_and_quoted_paste(tmp_path):
    empty = FakeHost([("ls-remote", FakeResult(0, ""))])
    item = run(git.up_to_date, git.UpToDateSection(), tmp_path, empty).items[0]
    assert item.status is Status.FAIL
    assert "does not exist on origin" in item.next_step.do
    odd = git.UpToDateSection(remote="my remote", branch="feat/it's")
    remote = ("ls-remote", FakeResult(0, f"{SHA}\trefs/heads/x\n"))
    behind = FakeHost([remote, ("cat-file", FakeResult(0)), ("merge-base", FakeResult(1))])
    paste = run(git.up_to_date, odd, tmp_path, behind).items[0].next_step.paste
    assert shlex.split(paste) == [
        "git",
        "fetch",
        "my remote",
        "&&",
        "git",
        "rebase",
        "my remote/feat/it's",
    ]


def test_files_present_and_absent(tmp_path):
    host = FakeHost(files={f"{tmp_path}/README.md": "x"})
    section = files.FilesSection(paths=["README.md", "secrets/dev.yaml"])
    present = {i.key: i.status for i in run(files.present, section, tmp_path, host).items}
    assert present == {"README.md": Status.OK, "secrets/dev.yaml": Status.FAIL}
    absent = {i.key: i.status for i in run(files.absent, section, tmp_path, host).items}
    assert absent == {"README.md": Status.FAIL, "secrets/dev.yaml": Status.OK}


def test_files_git_ignored_and_committed(tmp_path):
    section = files.FilesSection(paths=["secrets/prod.sops.yaml"])
    ignored = FakeHost([("check-ignore", FakeResult(1))])
    item = run(files.git_ignored, section, tmp_path, ignored).items[0]
    assert item.next_step.paste == "echo secrets/prod.sops.yaml >> .gitignore"
    odd = files.FilesSection(paths=["my dir/it's.yaml"])
    host = FakeHost([("check-ignore", FakeResult(1)), ("ls-files", FakeResult(1))])
    paste = run(files.git_ignored, odd, tmp_path, host).items[0].next_step.paste
    assert shlex.split(paste) == ["echo", "my dir/it's.yaml", ">>", ".gitignore"]
    paste = run(files.committed, odd, tmp_path, host).items[0].next_step.paste
    assert shlex.split(paste) == [
        "git",
        "add",
        "--",
        "my dir/it's.yaml",
        "&&",
        "git",
        "commit",
        "-m",
        "chore: commit my dir/it's.yaml",
    ]
    committed = FakeHost([("ls-files", FakeResult(0)), ("diff --quiet", FakeResult(1))])
    assert run(files.committed, section, tmp_path, committed).status is Status.FAIL


def test_sops_rule(tmp_path):
    config = (
        "creation_rules:\n"
        "  - path_regex: secrets/dev\\.(sops\\.)?yaml$\n"
        "    age: age1me,age1ci\n"
        "  - path_regex: secrets/prod\\.(sops\\.)?yaml$\n"
        "    age: age1me\n"
    )
    host = FakeHost(files={f"{tmp_path}/.sops.yaml": config})
    section = sops.SopsRuleSection(
        paths=["secrets/dev.sops.yaml", "secrets/prod.sops.yaml", "secrets/qa.yaml"],
        min_recipients=2,
    )
    items = {i.key: i.status for i in run(sops.rule, section, tmp_path, host).items}
    assert items == {
        "secrets/dev.sops.yaml": Status.OK,
        "secrets/prod.sops.yaml": Status.FAIL,
        "secrets/qa.yaml": Status.FAIL,
    }
    missing = run(sops.rule, section, tmp_path, FakeHost()).items[0]
    assert (missing.status, missing.next_step.generic) == (Status.FAIL, True)
    broken = FakeHost(files={f"{tmp_path}/.sops.yaml": "creation_rules:\n  - path_regex: '('\n"})
    assert run(sops.rule, section, tmp_path, broken).items[0].error_type == "RegexError"


def test_sops_rule_malformed_config(tmp_path):
    section = sops.SopsRuleSection(paths=["secrets/a.yaml"])
    for body in ("- a\n- b\n", "just a string\n", "creation_rules:\n  a: b\n"):
        host = FakeHost(files={f"{tmp_path}/.sops.yaml": body})
        item = run(sops.rule, section, tmp_path, host).items[0]
        assert (item.status, item.error_type) == (Status.ERROR, "YAMLError")
    empty = FakeHost(files={f"{tmp_path}/.sops.yaml": "creation_rules:\n"})
    item = run(sops.rule, section, tmp_path, empty).items[0]
    assert item.status is Status.FAIL
    assert "creation rule" in item.next_step.do
