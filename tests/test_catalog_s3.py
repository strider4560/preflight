import pytest
from fakes import IDENTITY, FakeAnsibleHost, FakeHost, FakeResult, make_probe, observe
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import s3
from preflight.check import CheckCallError
from preflight.outcome import Status


def head(rc, stderr=""):
    return FakeHost([("s3api head-bucket", FakeResult(rc, "", stderr))])


@pytest.mark.parametrize(
    ("host", "status", "observed"),
    [
        (head(0), Status.OK, "present"),
        (
            head(254, "An error occurred (404) when calling the HeadBucket operation: Not Found"),
            Status.OK,
            "absent",
        ),
        (
            head(254, "An error occurred (403) when calling the HeadBucket operation: Forbidden"),
            Status.OK,
            "forbidden",
        ),
        (head(254, "Unable to locate credentials"), Status.ERROR, None),
        (head(127), Status.ERROR, None),
    ],
)
def test_bucket_status(tmp_path, host, status, observed):
    bound = s3.bucket_status(bucket="tellabs-tfstate-dev", identity=IDENTITY)
    item = observe(bound, make_probe(tmp_path, host=host)).items[0]
    assert (item.status, item.observed) == (status, observed)
    assert "--bucket tellabs-tfstate-dev" in host.commands[0]
    assert bound.label == "s3.bucket_status(tellabs-tfstate-dev)"


def test_bucket_status_error_steps(tmp_path):
    bound = s3.bucket_status(bucket="bkt", identity=IDENTITY)
    missing = observe(bound, make_probe(tmp_path, host=head(127)))
    assert missing.items[0].error_type == "MissingTool"
    other = observe(bound, make_probe(tmp_path, host=head(254, "SECRET detail")))
    assert other.items[0].error_type == "AwsCli"
    assert "SECRET" not in other.items[0].next_step.do
    assert "profile sandbox" in other.items[0].next_step.do


def test_object_exists(tmp_path):
    present = FakeAnsibleHost({"amazon.aws.s3_object": {"s3_keys": ["platform/bootstrap.tfstate"]}})
    bound = s3.object_exists(bucket="bkt", key="platform/bootstrap.tfstate", identity=IDENTITY)
    assert observe(bound, make_probe(tmp_path, ansible=present)).items[0].observed is True
    assert present.calls == [
        (
            "amazon.aws.s3_object",
            {
                "bucket": "bkt",
                "mode": "list",
                "prefix": "platform/bootstrap.tfstate",
                "max_keys": 1,
                "region": "us-east-1",
                "profile": "sandbox",
            },
        )
    ]
    absent = FakeAnsibleHost({"amazon.aws.s3_object": {"s3_keys": []}})
    assert observe(bound, make_probe(tmp_path, ansible=absent)).items[0].observed is False
    sibling = FakeAnsibleHost(
        {"amazon.aws.s3_object": {"s3_keys": ["platform/bootstrap.tfstate.backup"]}}
    )
    assert observe(bound, make_probe(tmp_path, ansible=sibling)).items[0].observed is False
    failing = FakeAnsibleHost(
        {"amazon.aws.s3_object": AnsibleException({"failed": True, "msg": "SECRET"})}
    )
    item = observe(bound, make_probe(tmp_path, ansible=failing)).items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "AnsibleException")
    assert bound.label == "s3.object_exists(platform/bootstrap.tfstate)"


def test_bucket_names_are_validated():
    with pytest.raises(CheckCallError, match="bucket"):
        s3.bucket_status(bucket="Not A Bucket", identity=IDENTITY)
