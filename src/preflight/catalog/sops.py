"""A sops creation rule covers each secrets file, with enough age recipients. `.sops.yaml`
holds public keys only."""

from __future__ import annotations

import re
from typing import Annotated

import yaml
from pydantic import Field

from preflight.check import UniqueList, check
from preflight.outcome import Outcome, error, fail, ok, outcome
from preflight.probe import Probe


def _recipients(rule: dict) -> list[str]:
    age = rule.get("age", "")
    values = age if isinstance(age, list) else str(age).split(",")
    return [value.strip() for value in values if str(value).strip()]


@check
def rule(
    probe: Probe,
    paths: Annotated[UniqueList[str], Field(min_length=1)],
    min_recipients: Annotated[int, Field(ge=1)] = 1,
    config: str = ".sops.yaml",
) -> Outcome:
    config_file = probe.host.file(str(probe.path(config)))
    if not config_file.exists:
        skeleton = "creation_rules:\n" + "\n".join(
            f"  - path_regex: {re.escape(p)}$\n    age: age1..." for p in paths
        )
        return outcome(
            fail(
                do=f"Create {config} at the repository root containing:",
                paste=skeleton,
            )
        )
    try:
        loaded = yaml.safe_load(config_file.content_string) or {}
    except yaml.YAMLError:
        return outcome(error(do=f"{config} is not valid YAML.", error_type="YAMLError"))
    rules = (loaded.get("creation_rules") or []) if isinstance(loaded, dict) else None
    if not isinstance(rules, list):
        return outcome(
            error(
                do=(
                    f"{config} does not have the sops shape (a mapping with a creation_rules list)."
                ),
                error_type="YAMLError",
            )
        )
    rules = [r for r in rules if isinstance(r, dict)]
    items = []
    for path in paths:
        match = None
        for candidate in rules:
            try:
                if re.search(str(candidate.get("path_regex", "")), path):
                    match = candidate
                    break
            except re.error:
                return outcome(
                    error(
                        do=f"{config} has an invalid path_regex {candidate.get('path_regex')!r}.",
                        error_type="RegexError",
                    )
                )
        if match is None:
            items.append(
                fail(path, do=f"Add a creation rule to {config} whose path_regex matches {path}.")
            )
            continue
        count = len(set(_recipients(match)))
        if count < min_recipients:
            items.append(
                fail(
                    path,
                    do=(
                        f"The rule for {path} names {count} age recipient(s); "
                        f"it needs at least {min_recipients}."
                    ),
                    observed=count,
                )
            )
        else:
            items.append(ok(path, observed=count))
    return outcome(*items)
