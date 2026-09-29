"""A sops creation rule covers each secrets file, with enough age recipients. `.sops.yaml`
holds public keys only."""

from __future__ import annotations

import re

import yaml
from pydantic import Field

from preflight.check import Section, check
from preflight.outcome import Outcome, error, fail, ok, outcome


class SopsRuleSection(Section):
    paths: list[str] = Field(min_length=1)
    min_recipients: int = Field(default=1, ge=1)
    config: str = ".sops.yaml"


def _recipients(rule: dict) -> list[str]:
    age = rule.get("age", "")
    values = age if isinstance(age, list) else str(age).split(",")
    return [value.strip() for value in values if str(value).strip()]


@check("sops.rule", section=SopsRuleSection)
def rule(ctx, s: SopsRuleSection) -> Outcome:
    config = ctx.host.file(str(ctx.path(s.config)))
    if not config.exists:
        skeleton = "creation_rules:\n" + "\n".join(
            f"  - path_regex: {re.escape(p)}$\n    age: age1..." for p in s.paths
        )
        return outcome(
            fail(
                do=f"Create {s.config} with a creation rule for each secrets file.",
                paste=skeleton,
                generic=True,
            )
        )
    try:
        rules = (yaml.safe_load(config.content_string) or {}).get("creation_rules") or []
    except yaml.YAMLError:
        return outcome(error(do=f"{s.config} is not valid YAML.", error_type="YAMLError"))
    rules = [r for r in rules if isinstance(r, dict)]
    items = []
    for path in s.paths:
        match = None
        for candidate in rules:
            try:
                if re.search(str(candidate.get("path_regex", "")), path):
                    match = candidate
                    break
            except re.error:
                return outcome(
                    error(
                        do=f"{s.config} has an invalid path_regex {candidate.get('path_regex')!r}.",
                        error_type="RegexError",
                    )
                )
        if match is None:
            items.append(
                fail(path, do=f"Add a creation rule to {s.config} whose path_regex matches {path}.")
            )
            continue
        count = len(_recipients(match))
        if count < s.min_recipients:
            items.append(
                fail(
                    path,
                    do=(
                        f"The rule for {path} names {count} age recipient(s); "
                        f"it needs at least {s.min_recipients}."
                    ),
                    observed=count,
                )
            )
        else:
            items.append(ok(path, observed=count))
    return outcome(*items)
