"""Files in the consumer's repository: present, absent, git-ignored, committed. Existence and
git status only; never the contents."""

from __future__ import annotations

import shlex
from typing import Annotated

from pydantic import Field

from preflight.check import UniqueList, check
from preflight.outcome import Item, Outcome, error, fail, ok, outcome
from preflight.probe import Probe

Paths = Annotated[UniqueList[str], Field(min_length=1)]


def _exists(probe: Probe, path: str) -> bool:
    return probe.host.file(str(probe.path(path))).exists


@check
def present(probe: Probe, paths: Paths) -> Outcome:
    return outcome(*(ok(p) if _exists(probe, p) else fail(p, do=f"Create {p}.") for p in paths))


@check
def absent(probe: Probe, paths: Paths) -> Outcome:
    return outcome(*(fail(p, do=f"Remove {p}.") if _exists(probe, p) else ok(p) for p in paths))


def _ignored(probe: Probe, path: str) -> Item:
    root, quoted = str(probe.root), shlex.quote(path)
    # git never reports a tracked file as ignored, so adding it to .gitignore would not help.
    if probe.host.run("git -C %s ls-files --error-unmatch -- %s", root, path).rc == 0:
        return fail(
            path,
            do=f"{path} is committed; untrack it, then keep it ignored.",
            paste=f"git rm --cached -- {quoted} && echo {quoted} >> .gitignore",
        )
    result = probe.host.run("git -C %s check-ignore -q -- %s", root, path)
    if result.rc == 0:
        return ok(path)
    if result.rc == 1:
        return fail(path, do=f"Add {path} to .gitignore.", paste=f"echo {quoted} >> .gitignore")
    return error(path, do="git could not check .gitignore here.", error_type="GitError")


@check
def git_ignored(probe: Probe, paths: Paths) -> Outcome:
    return outcome(*(_ignored(probe, p) for p in paths))


def _committed(probe: Probe, path: str) -> Item:
    root = str(probe.root)
    tracked = probe.host.run("git -C %s ls-files --error-unmatch -- %s", root, path).rc == 0
    clean = tracked and probe.host.run("git -C %s diff --quiet HEAD -- %s", root, path).rc == 0
    if clean:
        return ok(path)
    return fail(
        path,
        do=f"Commit {path}.",
        paste=(
            f"git add -- {shlex.quote(path)} && "
            f"git commit -m {shlex.quote(f'chore: commit {path}')}"
        ),
    )


@check
def committed(probe: Probe, paths: Paths) -> Outcome:
    return outcome(*(_committed(probe, p) for p in paths))
