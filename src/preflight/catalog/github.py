"""GitHub settings the operator makes by hand, read with `gh api`. Secrets by name only."""

from __future__ import annotations

import json
import shlex
from typing import Annotated, Any
from urllib.parse import quote

from pydantic import Field, StringConstraints

from preflight.check import UniqueList, check
from preflight.outcome import Item, Outcome, error, fail, ok, outcome
from preflight.probe import Probe

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


def _gh_api(probe: Probe, command: str, path: str) -> Any:
    result = probe.host.run(command, path)
    if result.rc == 0:
        text = result.stdout.strip()
        try:
            return json.loads(text) if text else None
        except ValueError as exc:
            raise GhError("malformed JSON from gh api") from exc
    if "HTTP 404" in result.stderr:
        raise GhNotFound(path)
    raise _classify(result.rc, result.stderr)


def gh_api(probe: Probe, path: str) -> Any:
    return _gh_api(probe, "gh api %s", path)


def gh_api_pages(probe: Probe, path: str) -> list:
    """Every page of a list endpoint, as the list of page bodies."""
    return _list(_gh_api(probe, "gh api --paginate --slurp %s", path))


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


@check
def auth(probe: Probe, hostname: str = "github.com") -> Outcome:
    result = probe.host.run("gh auth status --hostname %s", hostname)
    if result.rc == 127:
        return outcome(error(do="Install the GitHub CLI (gh).", error_type="MissingTool"))
    if result.rc == 0:
        return outcome(ok())
    return outcome(
        error(
            do=f"Sign in to {hostname} with the GitHub CLI.",
            paste="gh auth login",
            error_type="GhAuth",
        )
    )


@check(key="repo")
def repo(probe: Probe, repo: Repo, actions_access: str | None = None) -> Outcome:
    try:
        gh_api(probe, f"repos/{repo}")
    except GhNotFound:
        return outcome(
            fail(
                "exists",
                do=f"Create the repository {repo}, or correct its name in the gate{SEE}",
                paste=f"gh repo create {shlex.quote(repo)} --private",
            )
        )
    except GhError as exc:
        return outcome(_gh_error("exists", exc))
    items = [ok("exists")]
    if actions_access is not None:
        try:
            body = gh_api(probe, f"repos/{repo}/actions/permissions/access")
            access = _dict(body).get("access_level")
        except GhError as exc:
            items.append(_gh_error("actions_access", exc))
        else:
            if access == actions_access:
                items.append(ok("actions_access", observed=access))
            else:
                endpoint = shlex.quote(f"repos/{repo}/actions/permissions/access")
                items.append(
                    fail(
                        "actions_access",
                        do=(
                            f"Set Actions access for {repo} to {actions_access} "
                            "(Settings → Actions → General → Access)."
                        ),
                        paste=(
                            f"gh api -X PUT {endpoint} "
                            f"-f access_level={shlex.quote(actions_access)}"
                        ),
                        observed=access,
                    )
                )
    return outcome(*items)


def _variables(probe: Probe, base: str, flag: str, variables: dict[str, str]) -> Outcome:
    items = []
    for name, expected in variables.items():
        paste = f"gh variable set {shlex.quote(name)} {flag} --body {shlex.quote(expected)}"
        try:
            value = _dict(gh_api(probe, f"{base}/actions/variables/{name}")).get("value")
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


@check(key="repo")
def variables(probe: Probe, repo: Repo, variables: dict[Name, str]) -> Outcome:
    return _variables(probe, f"repos/{repo}", f"--repo {shlex.quote(repo)}", variables)


@check(key="org")
def org_variables(probe: Probe, org: Owner, variables: dict[Name, str]) -> Outcome:
    return _variables(probe, f"orgs/{org}", f"--org {shlex.quote(org)}", variables)


@check(key="repo")
def environments(
    probe: Probe, repo: Repo, environments: Annotated[UniqueList[EnvName], Field(min_length=1)]
) -> Outcome:
    items = []
    for name in environments:
        endpoint = f"repos/{repo}/environments/{quote(name)}"
        try:
            gh_api(probe, endpoint)
        except GhNotFound:
            items.append(
                fail(
                    name,
                    do=f"Create the GitHub environment {name} in {repo}.",
                    paste=f"gh api -X PUT {shlex.quote(endpoint)}",
                )
            )
        except GhError as exc:
            items.append(_gh_error(name, exc))
        else:
            items.append(ok(name))
    return outcome(*items)


@check(key="name")
def ruleset(
    probe: Probe,
    repo: Repo,
    name: str,
    required_checks: tuple[str, ...] = (),
    enforcement: str = "active",
) -> Outcome:
    where = f"Settings → Rules → Rulesets in {repo}"
    try:
        summaries = [
            _dict(r) for page in gh_api_pages(probe, f"repos/{repo}/rulesets") for r in _list(page)
        ]
        match = next((r for r in summaries if r.get("name") == name), None)
        if match is None:
            return outcome(fail("exists", do=f"Create the ruleset {name!r} ({where})."))
        detail = _dict(gh_api(probe, f"repos/{repo}/rulesets/{match['id']}"))
    except GhError as exc:
        return outcome(_gh_error("exists", exc))
    items = [ok("exists")]
    actual = detail.get("enforcement")
    if actual == enforcement:
        items.append(ok("enforcement", observed=actual))
    else:
        items.append(
            fail(
                "enforcement",
                do=f"Set ruleset {name!r} enforcement to {enforcement} ({where}).",
                observed=actual,
            )
        )
    if required_checks:
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
        missing = [c for c in required_checks if c not in contexts]
        if missing:
            items.append(
                fail(
                    "required_checks",
                    do=(
                        f"Add the required status check(s) {', '.join(missing)} to ruleset "
                        f"{name!r} ({where})."
                    ),
                    observed=sorted(contexts),
                )
            )
        else:
            items.append(ok("required_checks", observed=sorted(contexts)))
    return outcome(*items)


@check(key="repo")
def secret_names(
    probe: Probe,
    repo: Repo,
    names: Annotated[UniqueList[Name], Field(min_length=1)],
    environment: EnvName | None = None,
) -> Outcome:
    if environment:
        path = f"repos/{repo}/environments/{quote(environment)}/secrets"
        where, flag = f" on environment {environment}", f" --env {shlex.quote(environment)}"
    else:
        path, where, flag = f"repos/{repo}/actions/secrets", "", ""
    ending = "."
    try:
        present = {
            _dict(x).get("name")
            for page in gh_api_pages(probe, path)
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
                paste=f"gh secret set {shlex.quote(name)}{flag} --repo {shlex.quote(repo)}",
            )
            for name in names
        )
    )


@check(key="workflow")
def workflow_green(
    probe: Probe, repo: Repo, workflow: str, branch: str | None = None, event: str | None = None
) -> Outcome:
    query = "per_page=1&status=completed"
    if branch:
        query += f"&branch={quote(branch)}"
    if event:
        query += f"&event={quote(event)}"
    try:
        body = gh_api(probe, f"repos/{repo}/actions/workflows/{quote(workflow)}/runs?{query}")
        runs = _list(_dict(body).get("workflow_runs"))
        last = _dict(runs[0]) if runs else None
    except GhError as exc:
        return outcome(_gh_error(None, exc))
    if last is None:
        return outcome(fail(do=f"{workflow} has no completed run yet; trigger one."))
    if last.get("conclusion") == "success":
        return outcome(ok(observed=last.get("html_url")))
    return outcome(
        fail(
            do=(
                f"The last {workflow} run concluded {last.get('conclusion')}; open it, fix the "
                "cause, and rerun."
            ),
            paste=last.get("html_url"),
            observed=last.get("conclusion"),
        )
    )
