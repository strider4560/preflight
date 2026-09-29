"""The checkout contains the remote branch's latest commit. Reads the remote with ls-remote and
never fetches, so checking changes nothing."""

from __future__ import annotations

from preflight.check import Section, check
from preflight.outcome import Outcome, error, fail, ok, outcome


class UpToDateSection(Section):
    remote: str = "origin"
    branch: str = "main"


@check("git.up_to_date", section=UpToDateSection)
def up_to_date(ctx, s: UpToDateSection) -> Outcome:
    root = str(ctx.root)
    ref = f"{s.remote}/{s.branch}"
    paste = f"git fetch {s.remote} && git rebase {ref}"
    remote = ctx.host.run("git -C %s ls-remote %s %s", root, s.remote, f"refs/heads/{s.branch}")
    if remote.rc != 0 or not remote.stdout.strip():
        return outcome(
            error(
                do=f"Could not read {ref} from the remote; check your network and git credentials.",
                error_type="GitRemote",
            )
        )
    sha = remote.stdout.split()[0]
    if ctx.host.run("git -C %s cat-file -e %s", root, f"{sha}^{{commit}}").rc != 0:
        return outcome(
            fail(
                do=f"Your checkout does not have the latest {ref}; fetch it and rebase onto it.",
                paste=paste,
                observed=sha[:12],
            )
        )
    ancestor = ctx.host.run("git -C %s merge-base --is-ancestor %s HEAD", root, sha)
    if ancestor.rc == 0:
        return outcome(ok(observed=sha[:12]))
    if ancestor.rc == 1:
        return outcome(
            fail(
                do=f"Your branch does not contain the latest {ref}; rebase onto it first.",
                paste=paste,
                observed=sha[:12],
            )
        )
    return outcome(
        error(do="git could not compare your branch with the remote.", error_type="GitError")
    )
