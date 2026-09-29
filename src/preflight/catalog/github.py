"""GitHub settings the operator makes by hand, read with `gh api`. Secrets by name only."""

from __future__ import annotations

import json
from typing import Annotated, Any, Self
from urllib.parse import quote

from pydantic import Field, StringConstraints, model_validator

from preflight.check import Section, check
from preflight.outcome import Item, Outcome, error, fail, ok, outcome

Repo = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
Owner = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$")]
SIGN_IN = "Could not query GitHub; check `gh auth status` and sign in if needed."


class GhError(Exception):
    pass


class GhNotFound(GhError):
    pass


def gh_api(ctx, path: str) -> Any:
    result = ctx.host.run("gh api %s", path)
    if result.rc == 0:
        text = result.stdout.strip()
        return json.loads(text) if text else None
    if "HTTP 404" in result.stderr:
        raise GhNotFound(path)
    raise GhError(f"gh api exited {result.rc}")


def _gh_error(key: str | None, exc: Exception) -> Item:
    return error(key, do=SIGN_IN, paste="gh auth login", error_type=type(exc).__name__)


def _all(items: list[Item]) -> Outcome:
    return outcome(*items) if items else outcome(ok(observed="nothing to check"))


class AuthSection(Section):
    hostname: str = "github.com"


@check("github.auth", section=AuthSection)
def auth(ctx, s: AuthSection) -> Outcome:
    result = ctx.host.run("gh auth status --hostname %s", s.hostname)
    if result.rc == 127:
        return outcome(error(do="Install the GitHub CLI (gh).", error_type="MissingTool"))
    if result.rc == 0:
        return outcome(ok())
    return outcome(fail(do=f"Sign in to {s.hostname} with the GitHub CLI.", paste="gh auth login"))


class RepoSection(Section):
    repo: Repo
    actions_access: str | None = None


@check("github.repo", section=RepoSection)
def repo(ctx, s: RepoSection) -> Outcome:
    try:
        gh_api(ctx, f"repos/{s.repo}")
    except GhNotFound:
        return outcome(
            fail(
                "exists",
                do=f"Create the repository {s.repo}, or correct its name in the contract.",
                paste=f"gh repo create {s.repo} --private",
            )
        )
    except GhError as exc:
        return outcome(_gh_error("exists", exc))
    items = [ok("exists")]
    if s.actions_access is not None:
        try:
            access = (gh_api(ctx, f"repos/{s.repo}/actions/permissions/access") or {}).get(
                "access_level"
            )
        except GhError as exc:
            items.append(_gh_error("actions_access", exc))
        else:
            if access == s.actions_access:
                items.append(ok("actions_access", observed=access))
            else:
                items.append(
                    fail(
                        "actions_access",
                        do=(
                            f"Set Actions access for {s.repo} to {s.actions_access} "
                            "(Settings → Actions → General → Access)."
                        ),
                        paste=(
                            f"gh api -X PUT repos/{s.repo}/actions/permissions/access "
                            f"-f access_level={s.actions_access}"
                        ),
                        observed=access,
                    )
                )
    return outcome(*items)


class VariablesSection(Section):
    repo: Repo | None = None
    org: Owner | None = None
    variables: dict[str, str]

    @model_validator(mode="after")
    def one_owner(self) -> Self:
        if (self.repo is None) == (self.org is None):
            raise ValueError("give exactly one of repo or org")
        return self


@check("github.variables", section=VariablesSection)
def variables(ctx, s: VariablesSection) -> Outcome:
    base, flag = (
        (f"orgs/{s.org}", f"--org {s.org}") if s.org else (f"repos/{s.repo}", f"--repo {s.repo}")
    )
    items = []
    for name, expected in s.variables.items():
        paste = f"gh variable set {name} {flag} --body '{expected}'"
        try:
            value = (gh_api(ctx, f"{base}/actions/variables/{name}") or {}).get("value")
        except GhNotFound:
            items.append(fail(name, do=f"Create the Actions variable {name}.", paste=paste))
            continue
        except GhError as exc:
            items.append(_gh_error(name, exc))
            continue
        if value == expected:
            items.append(ok(name))
        else:
            items.append(
                fail(
                    name,
                    do=f"Set the Actions variable {name} to {expected!r}.",
                    paste=paste,
                    observed=value,
                )
            )
    return _all(items)


class EnvironmentsSection(Section):
    repo: Repo
    environments: list[str] = Field(min_length=1)


@check("github.environments", section=EnvironmentsSection)
def environments(ctx, s: EnvironmentsSection) -> Outcome:
    items = []
    for name in s.environments:
        try:
            gh_api(ctx, f"repos/{s.repo}/environments/{quote(name)}")
        except GhNotFound:
            items.append(
                fail(
                    name,
                    do=f"Create the GitHub environment {name} in {s.repo}.",
                    paste=f"gh api -X PUT repos/{s.repo}/environments/{quote(name)}",
                )
            )
        except GhError as exc:
            items.append(_gh_error(name, exc))
        else:
            items.append(ok(name))
    return outcome(*items)


class RulesetSection(Section):
    repo: Repo
    name: str
    required_checks: list[str] = []
    enforcement: str = "active"


@check("github.ruleset", section=RulesetSection)
def ruleset(ctx, s: RulesetSection) -> Outcome:
    where = f"Settings → Rules → Rulesets in {s.repo}"
    try:
        summaries = gh_api(ctx, f"repos/{s.repo}/rulesets") or []
        match = next((r for r in summaries if r.get("name") == s.name), None)
        if match is None:
            return outcome(
                fail("exists", do=f"Create the ruleset {s.name!r} ({where}).", generic=True)
            )
        detail = gh_api(ctx, f"repos/{s.repo}/rulesets/{match['id']}") or {}
    except GhError as exc:
        return outcome(_gh_error("exists", exc))
    items = [ok("exists")]
    enforcement = detail.get("enforcement")
    if enforcement == s.enforcement:
        items.append(ok("enforcement", observed=enforcement))
    else:
        items.append(
            fail(
                "enforcement",
                do=f"Set ruleset {s.name!r} enforcement to {s.enforcement} ({where}).",
                observed=enforcement,
            )
        )
    if s.required_checks:
        contexts = {
            check_.get("context")
            for rule in detail.get("rules", [])
            if rule.get("type") == "required_status_checks"
            for check_ in (rule.get("parameters") or {}).get("required_status_checks", [])
            if check_.get("context")
        }
        missing = [c for c in s.required_checks if c not in contexts]
        if missing:
            items.append(
                fail(
                    "required_checks",
                    do=(
                        f"Add the required status check(s) {', '.join(missing)} to ruleset "
                        f"{s.name!r} ({where})."
                    ),
                    observed=sorted(contexts),
                )
            )
        else:
            items.append(ok("required_checks", observed=sorted(contexts)))
    return outcome(*items)


class SecretNamesSection(Section):
    repo: Repo
    environment: str | None = None
    names: list[str] = Field(min_length=1)


@check("github.secret_names", section=SecretNamesSection)
def secret_names(ctx, s: SecretNamesSection) -> Outcome:
    if s.environment:
        path = f"repos/{s.repo}/environments/{quote(s.environment)}/secrets"
        where, flag = f" on environment {s.environment}", f" --env {s.environment}"
    else:
        path, where, flag = f"repos/{s.repo}/actions/secrets", "", ""
    try:
        present = {x.get("name") for x in (gh_api(ctx, path) or {}).get("secrets", [])}
    except GhNotFound:
        present = set()
    except GhError as exc:
        return outcome(_gh_error(None, exc))
    return outcome(
        *(
            ok(name)
            if name in present
            else fail(
                name,
                do=f"Set the secret {name}{where}.",
                paste=f"gh secret set {name}{flag} --repo {s.repo} < <file holding the value>",
            )
            for name in s.names
        )
    )


class WorkflowSection(Section):
    repo: Repo
    workflow: str
    branch: str | None = None
    event: str | None = None


@check("github.workflow_green", section=WorkflowSection)
def workflow_green(ctx, s: WorkflowSection) -> Outcome:
    query = "per_page=1&status=completed"
    if s.branch:
        query += f"&branch={quote(s.branch)}"
    if s.event:
        query += f"&event={quote(s.event)}"
    try:
        body = gh_api(ctx, f"repos/{s.repo}/actions/workflows/{quote(s.workflow)}/runs?{query}")
    except GhError as exc:
        return outcome(_gh_error(None, exc))
    runs = (body or {}).get("workflow_runs", [])
    if not runs:
        return outcome(
            fail(do=f"{s.workflow} has no completed run yet; trigger one.", generic=True)
        )
    last = runs[0]
    if last.get("conclusion") == "success":
        return outcome(ok(observed=last.get("html_url")))
    return outcome(
        fail(
            do=(
                f"The last {s.workflow} run concluded {last.get('conclusion')}; open it, fix the "
                "cause, and rerun."
            ),
            paste=last.get("html_url"),
            observed=last.get("conclusion"),
            generic=True,
        )
    )
