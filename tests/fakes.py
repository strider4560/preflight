"""Fakes for testinfra hosts, Ansible modules and DNS, used by the catalog tests."""

import json
import shlex
from dataclasses import dataclass

from preflight.check import BoundCheck
from preflight.identity import Identity
from preflight.outcome import Outcome
from preflight.probe import Probe

IDENTITY = Identity(
    profile="sandbox",
    region="us-east-1",
    account_id="111111111111",
    permission_set="AWSAdministratorAccess",
)
ROLE_ARN = (
    "arn:aws:sts::111111111111:assumed-role/"
    "AWSReservedSSO_AWSAdministratorAccess_bd9c3ff84c4cd64d/operator"
)


@dataclass
class FakeResult:
    rc: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def succeeded(self):
        return self.rc == 0


@dataclass
class FakeFile:
    exists: bool
    content_string: str = ""


class FakeHost:
    """Answers `run` from (substring, result) rules, first match wins; records every command."""

    def __init__(self, rules=(), files=None):
        self.rules = list(rules)
        self.files = dict(files or {})
        self.commands = []

    def run(self, command, *args):
        rendered = command % tuple(shlex.quote(str(a)) for a in args) if args else command
        self.commands.append(rendered)
        for pattern, result in self.rules:
            if pattern in rendered:
                return result
        raise AssertionError(f"unexpected command: {rendered}")

    def file(self, path):
        path = str(path)
        return FakeFile(True, self.files[path]) if path in self.files else FakeFile(False)


class FakeAnsibleHost:
    """Answers `ansible(module, args)` from a table of results, exceptions or callables."""

    def __init__(self, modules):
        self.modules = dict(modules)
        self.calls = []

    def ansible(self, module, module_args=None, check=True, **kwargs):
        args = json.loads(module_args) if module_args else {}
        self.calls.append((module, args))
        answer = self.modules[module]
        if callable(answer) and not isinstance(answer, BaseException):
            answer = answer(args)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeDns:
    def __init__(self, referrals=None, cnames=None, caa=None):
        self.referrals = referrals or {}
        self.cnames = cnames or {}
        self.caa_records = caa or {}

    @staticmethod
    def _answer(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def referral(self, name, parent):
        return self._answer(self.referrals[name])

    def cname(self, name):
        return self._answer(self.cnames.get(name, []))

    def caa(self, name):
        return self._answer(self.caa_records.get(name, []))


def make_probe(root, *, host=None, ansible=None, dns=None, identity=IDENTITY, environ=None):
    return Probe(
        root=root,
        identity=identity,
        environ=environ if environ is not None else {},
        _host=host,
        _ansible_host=ansible,
        _dns=dns,
    )


def observe(bound: BoundCheck, probe: Probe) -> Outcome:
    """Runs a bound check's function in process, with the arguments its call validated."""
    return bound.check.observe(probe, **bound.values)
