"""Files in the consumer's repository: present, absent, git-ignored, committed. Existence and
git status only; never the contents."""

from __future__ import annotations

from pydantic import Field

from preflight.check import Section, check
from preflight.outcome import Item, Outcome, error, fail, ok, outcome


class FilesSection(Section):
    paths: list[str] = Field(min_length=1)


def _exists(ctx, path: str) -> bool:
    return ctx.host.file(str(ctx.path(path))).exists


@check("files.present", section=FilesSection)
def present(ctx, s: FilesSection) -> Outcome:
    return outcome(
        *(ok(p) if _exists(ctx, p) else fail(p, do=f"Create {p}.", generic=True) for p in s.paths)
    )


@check("files.absent", section=FilesSection)
def absent(ctx, s: FilesSection) -> Outcome:
    return outcome(
        *(fail(p, do=f"Remove {p}.", generic=True) if _exists(ctx, p) else ok(p) for p in s.paths)
    )


def _ignored(ctx, path: str) -> Item:
    result = ctx.host.run("git -C %s check-ignore -q -- %s", str(ctx.root), path)
    if result.rc == 0:
        return ok(path)
    if result.rc == 1:
        return fail(path, do=f"Add {path} to .gitignore.", paste=f"echo '{path}' >> .gitignore")
    return error(path, do="git could not check .gitignore here.", error_type="GitError")


@check("files.git_ignored", section=FilesSection)
def git_ignored(ctx, s: FilesSection) -> Outcome:
    return outcome(*(_ignored(ctx, p) for p in s.paths))


def _committed(ctx, path: str) -> Item:
    root = str(ctx.root)
    tracked = ctx.host.run("git -C %s ls-files --error-unmatch -- %s", root, path).rc == 0
    clean = tracked and ctx.host.run("git -C %s diff --quiet HEAD -- %s", root, path).rc == 0
    if clean:
        return ok(path)
    return fail(
        path,
        do=f"Commit {path}.",
        paste=f"git add {path} && git commit -m 'chore: commit {path}'",
        generic=True,
    )


@check("files.committed", section=FilesSection)
def committed(ctx, s: FilesSection) -> Outcome:
    return outcome(*(_committed(ctx, p) for p in s.paths))
