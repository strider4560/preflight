"""Expected AWS identities, how an observed caller is matched, and each worker's environment."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

AccountId = Annotated[str, StringConstraints(pattern=r"^\d{12}$")]
Profile = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.+=,@-]+$")]
Region = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")]
RoleName = Annotated[str, StringConstraints(pattern=r"^[\w+=,.@-]{1,64}$")]
PermissionSet = Annotated[str, StringConstraints(pattern=r"^[\w+=,.@-]{1,32}$")]

AWS_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_ROLE_SESSION_NAME",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ENDPOINT_URL",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
)
AWS_PREFIXES = ("AWS_ENDPOINT_URL_",)
TOFU_VARIABLES = ("TF_CLI_ARGS", "TF_WORKSPACE")
TOFU_PREFIXES = ("TF_CLI_ARGS_", "TF_VAR_")
ASSUMED_ROLE = re.compile(r"^arn:aws[a-z-]*:sts::(\d{12}):assumed-role/([^/]+)/.+$")


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    profile: Profile
    region: Region
    account_id: AccountId
    role: RoleName | None = None
    permission_set: PermissionSet | None = None

    @model_validator(mode="after")
    def one_role(self) -> Self:
        if (self.role is None) == (self.permission_set is None):
            raise ValueError("give exactly one of role or permission_set")
        return self

    def role_matches(self, role_name: str) -> bool:
        if self.role is not None:
            return role_name == self.role
        pattern = rf"AWSReservedSSO_{re.escape(self.permission_set or '')}_[0-9a-f]{{16}}"
        return re.fullmatch(pattern, role_name) is not None

    def matches(self, account: str, arn: str) -> bool:
        match = ASSUMED_ROLE.match(arn)
        return (
            match is not None
            and account == self.account_id
            and match[1] == self.account_id
            and self.role_matches(match[2])
        )

    def describe(self) -> str:
        role = self.role or f"AWSReservedSSO_{self.permission_set}_*"
        return f"role {role} in account {self.account_id}"


def worker_environment(
    base: Mapping[str, str], identity: Identity | None, *, keep_aws: bool, bin_dir: str
) -> dict[str, str]:
    env = dict(base)
    if not keep_aws:
        for name in list(env):
            if name in AWS_VARIABLES or name.startswith(AWS_PREFIXES):
                del env[name]
    for name in list(env):
        if name in TOFU_VARIABLES or name.startswith(TOFU_PREFIXES):
            del env[name]
    if identity is not None:
        if not keep_aws:
            env["AWS_PROFILE"] = identity.profile
        env["AWS_REGION"] = identity.region
        env["AWS_DEFAULT_REGION"] = identity.region
    env["PATH"] = os.pathsep.join(part for part in (bin_dir, env.get("PATH", "")) if part)
    return env
