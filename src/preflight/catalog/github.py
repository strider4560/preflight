"""GitHub settings the operator makes by hand, read with `gh api`. Secrets by name only."""

from __future__ import annotations

import json
import shlex
from typing import Annotated, Any, Self
from urllib.parse import quote

from pydantic import Field, StringConstraints, model_validator

from preflight.check import Section, check
from preflight.outcome import Item, Outcome, error, fail, ok, outcome

Repo = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
Owner = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$")]
Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$")]
SEE = ", or check that your gh token can see it."


class GhError(Exception):
    kind = "other"


class GhNotFound(GhError):
    kind = "notfound"


class GhMissing(GhError):
    kind = "missing"


class GhAuth(GhError):
    kind = "auth"


class GhRefused(GhError):
    kind = "refused"


def _classify(rc: int, stderr: str) -> GhError:
    low = stderr.lower()
    if rc == 127:
        return GhMissing("gh not installed")
    if "http 401" in low or "authentication" in low or "gh auth login" in low:
        return GhAuth(f"gh api exited {rc}")
    if "http 403" in low or "http 429" in low or "rate limit" in low:
        return GhRefused(f"gh api exited {rc}")
    return GhError(f"gh api exited {rc}")


def _gh_api(ctx, command: str, path: str) -> Any:
    result = ctx.host.run(command, path)
    if result.rc == 0:
        text = result.stdout.strip()
        try:
            return json.loads(text) if text else None
        except ValueError as exc:
            raise GhError("malformed JSON from gh api") from exc
    if "HTTP 404" in result.stderr:
        raise GhNotFound(path)
    raise _classify(result.rc, result.stderr)


def gh_api(ctx, path: str) -> Any:
    return _gh_api(ctx, "gh api %s", path)


def gh_api_pages(ctx, path: str) -> list:
    """Every page of a list endpoint, as the list of page bodies."""
    return _list(_gh_api(ctx, "gh api --paginate --slurp %s", path))


def _dict(body: Any) -> dict:
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise GhError("unexpected response shape")
    return body


def _list(body: Any) -> list:
    if body is None:
        return []
    if not isinstance(body, list):
        raise GhError("unexpected response shape")
    return body


def _gh_error(key: str | None, exc: Exception) -> Item:
    kind = getattr(exc, "kind", "other")
    if kind == "missing":
        return error(key, do="Install the GitHub CLI (gh).", error_type="MissingTool")
    if kind == "auth":
        return error(
            key,
            do="Sign in to GitHub with the GitHub CLI.",
            paste="gh auth login",
            error_type="GhAuth",
        )
    if kind == "refused":
        return error(
            key,
            do=(
                "GitHub refused the request (permissions or rate limit); check your access, "
                "or retry later."
            ),
            error_type="GhRefused",
        )
    return error(
        key, do="Could not reach GitHub; check your network, then rerun.", error_type="GhError"
    )


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
                do=(f"Create the repository {s.repo}, or correct its name in the contract{SEE}"),
                paste=f"gh repo create {shlex.quote(s.repo)} --private",
            )
        )
    except GhError as exc:
        return outcome(_gh_error("exists", exc))
    items = [ok("exists")]
    if s.actions_access is not None:
        try:
            body = gh_api(ctx, f"repos/{s.repo}/actions/permissions/access")
            access = _dict(body).get("access_level")
        except GhError as exc:
            items.append(_gh_error("actions_access", exc))
        else:
            if access == s.actions_access:
                items.append(ok("actions_access", observed=access))
            else:
                endpoint = shlex.quote(f"repos/{s.repo}/actions/permissions/access")
                items.append(
                    fail(
                        "actions_access",
                        do=(
                            f"Set Actions access for {s.repo} to {s.actions_access} "
                            "(Settings → Actions → General → Access)."
                        ),
                        paste=(
                            f"gh api -X PUT {endpoint} "
                            f"-f access_level={shlex.quote(s.actions_access)}"
                        ),
                        observed=access,
                    )
                )
    return outcome(*items)


class VariablesSection(Section):
    repo: Repo | None = None
    org: Owner | None = None
    variables: dict[Name, str]

    @model_validator(mode="after")
    def one_owner(self) -> Self:
        if (self.repo is None) == (self.org is None):
            raise ValueError("give exactly one of repo or org")
        return self


@check("github.variables", section=VariablesSection)
def variables(ctx, s: VariablesSection) -> Outcome:
    if s.org:
        base, flag = f"orgs/{s.org}", f"--org {shlex.quote(s.org)}"
    else:
        base, flag = f"repos/{s.repo}", f"--repo {shlex.quote(s.repo or '')}"
    items = []
    for name, expected in s.variables.items():
        paste = f"gh variable set {shlex.quote(name)} {flag} --body {shlex.quote(expected)}"
        try:
            value = _dict(gh_api(ctx, f"{base}/actions/variables/{name}")).get("value")
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
    environments: list[EnvName] = Field(min_length=1)


@check("github.environments", section=EnvironmentsSection)
def environments(ctx, s: EnvironmentsSection) -> Outcome:
    items = []
    for name in s.environments:
        endpoint = f"repos/{s.repo}/environments/{quote(name)}"
        try:
            gh_api(ctx, endpoint)
        except GhNotFound:
            items.append(
                fail(
                    name,
                    do=f"Create the GitHub environment {name} in {s.repo}.",
                    paste=f"gh api -X PUT {shlex.quote(endpoint)}",
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
        summaries = [
            _dict(r) for page in gh_api_pages(ctx, f"repos/{s.repo}/rulesets") for r in _list(page)
        ]
        match = next((r for r in summaries if r.get("name") == s.name), None)
        if match is None:
            return outcome(
                fail("exists", do=f"Create the ruleset {s.name!r} ({where}).", generic=True)
            )
        detail = _dict(gh_api(ctx, f"repos/{s.repo}/rulesets/{match['id']}"))
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
        try:
            contexts = {
                check_.get("context")
                for rule in map(_dict, _list(detail.get("rules")))
                if rule.get("type") == "required_status_checks"
                for check_ in map(
                    _dict, _list(_dict(rule.get("parameters")).get("required_status_checks"))
                )
                if check_.get("context")
            }
        except GhError as exc:
            items.append(_gh_error("required_checks", exc))
            return outcome(*items)
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
    environment: EnvName | None = None
    names: list[Name] = Field(min_length=1)


@check("github.secret_names", section=SecretNamesSection)
def secret_names(ctx, s: SecretNamesSection) -> Outcome:
    if s.environment:
        path = f"repos/{s.repo}/environments/{quote(s.environment)}/secrets"
        where, flag = f" on environment {s.environment}", f" --env {shlex.quote(s.environment)}"
    else:
        path, where, flag = f"repos/{s.repo}/actions/secrets", "", ""
    ending = "."
    try:
        present = {
            _dict(x).get("name")
            for page in gh_api_pages(ctx, path)
            for x in _list(_dict(page).get("secrets"))
        }
    except GhNotFound:
        present, ending = set(), SEE
    except GhError as exc:
        return outcome(_gh_error(None, exc))
    return outcome(
        *(
            ok(name)
            if name in present
            else fail(
                name,
                do=f"Set the secret {name}{where}{ending}",
                paste=f"gh secret set {shlex.quote(name)}{flag} --repo {shlex.quote(s.repo)}",
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
        runs = _list(_dict(body).get("workflow_runs"))
        last = _dict(runs[0]) if runs else None
    except GhError as exc:
        return outcome(_gh_error(None, exc))
    if last is None:
        return outcome(
            fail(do=f"{s.workflow} has no completed run yet; trigger one.", generic=True)
        )
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
