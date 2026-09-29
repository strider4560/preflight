"""SDK sessions with explicit identity checks and normal credential refresh."""

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from gate.contract import Contract

SDK_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"mode": "standard", "total_max_attempts": 3},
)


class ObservationError(RuntimeError):
    """A required fact could not be verified; this is never a passing check."""


class AwsContexts:
    def __init__(self, contract: Contract, *, session_factory=boto3.Session):
        self.contract = contract
        self.session_factory = session_factory
        self._sessions = {}
        self._identities = {}
        self._clients = {}
        self._errors = {}

    def identity(self, alias: str) -> dict:
        if alias in self._errors:
            raise ObservationError(self._errors[alias])
        if alias not in self.contract.identities:
            raise ObservationError(f"Unknown identity: {alias}")
        if alias not in self._sessions:
            spec = self.contract.identities[alias]
            try:
                session = self.session_factory(
                    profile_name=spec.profile, region_name=self.contract.region
                )
                observed = session.client("sts", config=SDK_CONFIG).get_caller_identity()
            except (BotoCoreError, ClientError) as exc:
                code = (
                    exc.response["Error"]["Code"]
                    if isinstance(exc, ClientError)
                    else type(exc).__name__
                )
                message = (
                    f"Cannot verify AWS identity {alias!r}: {code}. "
                    "Check this identity's profile, credential source and role trust."
                )
                self._errors[alias] = message
                raise ObservationError(message) from None
            if (
                observed["Account"] != spec.expected_account_id
                or not observed["Arn"].startswith(spec.assumed_role_prefix)
                or not observed["Arn"][len(spec.assumed_role_prefix) :]
            ):
                message = (
                    f"AWS identity {alias!r} mismatch. Expected role "
                    f"{spec.expected_role_arn}; observed {observed['Arn']} "
                    f"in account {observed['Account']}. Correct the deployment credentials."
                )
                self._errors[alias] = message
                raise ObservationError(message)
            self._sessions[alias] = session
            self._identities[alias] = observed
        return self._identities[alias]

    def client(self, alias: str, service: str, *, region_name: str | None = None):
        self.identity(alias)
        region = region_name or self.contract.region
        key = alias, service, region
        if key not in self._clients:
            self._clients[key] = self._sessions[alias].client(
                service, region_name=region, config=SDK_CONFIG
            )
        return self._clients[key]
