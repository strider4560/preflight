"""Explicit expectations, independent of the observations collected from AWS."""

import hashlib
import posixpath
import re
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$")]
AccountId = Annotated[str, StringConstraints(pattern=r"^\d{12}$")]
Nonempty = Annotated[str, StringConstraints(min_length=1)]
KmsKeyArn = Annotated[
    str,
    StringConstraints(pattern=r"^arn:aws(?:-[a-z]+)*:kms:[a-z0-9-]+:\d{12}:key/[a-zA-Z0-9-]+$"),
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Identity(Model):
    expected_account_id: AccountId
    expected_role_arn: Nonempty
    profile: Nonempty | None = None

    @model_validator(mode="after")
    def valid_role(self) -> Self:
        match = re.fullmatch(
            r"arn:(aws(?:-[a-z]+)*):iam::(\d{12}):role/(.+)", self.expected_role_arn
        )
        if not match or match[2] != self.expected_account_id:
            raise ValueError("expected_role_arn must be an IAM role in expected_account_id")
        return self

    @property
    def assumed_role_prefix(self) -> str:
        arn = self.expected_role_arn.split(":")
        role_name = arn[5].rsplit("/", 1)[1]
        return f"arn:{arn[1]}:sts::{self.expected_account_id}:assumed-role/{role_name}/"


class State(Model):
    identity: Name
    inspection_identity: Name | None = None
    bucket: Nonempty
    owner_account_id: AccountId
    region: Nonempty
    key: Nonempty
    mode: Literal["new", "existing"]
    workspace: Nonempty = "default"
    workspace_key_prefix: str = "env:"
    require_versioning: bool = True
    expected_kms_key_arn: KmsKeyArn | None = None

    @property
    def effective_key(self) -> str:
        if self.workspace == "default":
            return self.key
        # Terraform uses Go path.Join for named workspaces, but the raw key for default.
        # Join as text first: Python's path.join discards prefixes on absolute segments.
        joined = "/".join(p for p in (self.workspace_key_prefix, self.workspace, self.key) if p)
        return posixpath.normpath(re.sub(r"/+", "/", joined))


class Subnet(Model):
    name: Name
    id: Annotated[str, StringConstraints(pattern=r"^subnet-[0-9a-f]+$")]
    az_id: Nonempty
    additional_ipv4_required: int = Field(ge=0)
    reserve_ipv4: int = Field(ge=0)


class Network(Model):
    identity: Name
    vpc_id: Annotated[str, StringConstraints(pattern=r"^vpc-[0-9a-f]+$")]
    owner_account_id: AccountId
    minimum_azs: int = Field(default=2, ge=1)
    subnets: list[Subnet] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_subnets(self) -> Self:
        for field in ("id", "name"):
            values = [getattr(s, field) for s in self.subnets]
            if len(values) != len(set(values)):
                raise ValueError(f"subnet {field} values must be unique")
        if len({s.az_id for s in self.subnets}) < self.minimum_azs:
            raise ValueError("configured subnets do not cover minimum_azs")
        return self


class Probe(Model):
    name: Name
    host: Annotated[
        str, StringConstraints(pattern=r"^(local|ssh|docker|podman|kubectl|ansible|paramiko)://")
    ]
    url: Annotated[str, StringConstraints(pattern=r"^https?://[^\s]+$")]
    expected_body: Nonempty = "ready"
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class Runtime(Model):
    probes: list[Probe] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        names = [p.name for p in self.probes]
        if len(names) != len(set(names)):
            raise ValueError("probe names must be unique")
        return self


class Contract(Model):
    schema_version: Literal[1]
    environment: Nonempty
    region: Nonempty
    modules: list[Name] = Field(min_length=1)
    example: bool = False
    identities: dict[Name, Identity] = Field(default_factory=dict)
    state: State | None = None
    network: Network | None = None
    runtime: Runtime | None = None
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_modules(self) -> Self:
        if len(self.modules) != len(set(self.modules)):
            raise ValueError("modules must be unique")
        if "identity" in self.modules and not self.identities:
            raise ValueError("identity module requires at least one identity")
        for name in ("state", "network", "runtime"):
            section = getattr(self, name)
            if name in self.modules and section is None:
                raise ValueError(f"{name} module requires a [{name}] section")
        for section in (self.state, self.network):
            if section is not None:
                aliases = [section.identity]
                if isinstance(section, State) and section.inspection_identity:
                    aliases.append(section.inspection_identity)
                for alias in aliases:
                    if alias not in self.identities:
                        raise ValueError(f"undefined identity: {alias}")
        return self


def load_contract(path: Path) -> tuple[Contract, str]:
    raw = path.read_bytes()
    contract = Contract.model_validate(tomllib.loads(raw.decode("utf-8")))
    if contract.example:
        raise ValueError("example contract: edit the expectations and set example = false")
    return contract, hashlib.sha256(raw).hexdigest()
