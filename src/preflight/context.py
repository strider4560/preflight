"""What a check observes through: testinfra hosts, Ansible modules, DNS, and its identity."""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import testinfra

from preflight.dnsclient import DnsClient
from preflight.identity import Identity

SAFE = re.compile(r"^[A-Za-z0-9_.\-/+=,@]+$")
# How the SSM lookup renders a missing parameter with on_missing='skip' (see Spike results).
MISSING = (None, "", "None")
# How a failed lookup renders through ansible.builtin.debug: as a message, without raising.
LOOKUP_FAILED_PREFIX = "Task failed:"


class ModuleFailed(Exception):
    """An Ansible module or lookup reported failure without raising; never read it as a value."""


def _literal(value: str) -> str:
    if not SAFE.match(value):
        raise ValueError("value is not safe to place in a lookup expression")
    return f"'{value}'"


def make_ansible_host() -> Any:
    directory = tempfile.mkdtemp(prefix="preflight-ansible-")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    inventory = Path(directory) / "inventory.ini"
    inventory.write_text(
        f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n"
    )
    config = Path(directory) / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled = False\nlocalhost_warning = False\n")
    # A consumer's own ansible.cfg in the repo root must not change how probes run.
    os.environ["ANSIBLE_CONFIG"] = str(config)
    return testinfra.get_host("ansible://localhost", ansible_inventory=str(inventory))


@dataclass
class Context:
    root: Path
    environment: str | None = None
    identity: Identity | None = None
    identities: Mapping[str, Identity] = field(default_factory=dict)
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    _host: Any = None
    _ansible_host: Any = None
    _dns: DnsClient | None = None

    @property
    def host(self) -> Any:
        if self._host is None:
            self._host = testinfra.get_host("local://")
        return self._host

    @property
    def ansible_host(self) -> Any:
        if self._ansible_host is None:
            self._ansible_host = make_ansible_host()
        return self._ansible_host

    @property
    def dns(self) -> DnsClient:
        if self._dns is None:
            self._dns = DnsClient()
        return self._dns

    def path(self, relative: str) -> Path:
        return self.root / relative

    def _identity(self, identity: Identity | None) -> Identity:
        chosen = identity or self.identity
        if chosen is None:
            raise RuntimeError("this check has no identity")
        return chosen

    def aws_module(
        self,
        module: str,
        args: Mapping[str, Any] | None = None,
        *,
        identity: Identity | None = None,
        ambient: bool = False,
        expect: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        chosen = self._identity(identity)
        payload = {**(args or {}), "region": chosen.region}
        if not ambient:
            payload["profile"] = chosen.profile
        result = self.ansible_host.ansible(module, json.dumps(payload), check=True)
        if result.get("failed") or any(key not in result for key in expect):
            raise ModuleFailed(module)
        return result

    def ssm_lookup(self, name: str, *, identity: Identity | None = None) -> str | None:
        chosen = self._identity(identity)
        expression = (
            "{{ lookup('amazon.aws.ssm_parameter', "
            + _literal(name)
            + ", decrypt=False, on_missing='skip', profile="
            + _literal(chosen.profile)
            + ", region="
            + _literal(chosen.region)
            + ") }}"
        )
        result = self.ansible_host.ansible(
            "ansible.builtin.debug", json.dumps({"msg": expression}), check=True
        )
        value = result.get("msg")
        if result.get("failed") or (
            isinstance(value, str) and value.startswith(LOOKUP_FAILED_PREFIX)
        ):
            raise ModuleFailed("amazon.aws.ssm_parameter")
        return None if value in MISSING else str(value)
