import json

from fakes import IDENTITY, FakeAnsibleHost, FakeDns, make_probe, observe
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import acm
from preflight.dnsclient import DnsUnavailable
from preflight.outcome import Status

ARN = "arn:aws:acm:us-east-1:111111111111:certificate/abc"
ARGS = {"arn": ARN, "identity": IDENTITY}
RECORD = {"name": "_x.tellabs.dev.", "type": "CNAME", "value": "_y.acm-validations.aws."}


def certificate(status, records=(RECORD,)):
    options = [{"domain_name": "*.tellabs.dev", "resource_record": r} for r in records]
    return FakeAnsibleHost(
        {
            "community.aws.acm_certificate_info": {
                "certificates": [{"status": status, "domain_validation_options": options}]
            }
        }
    )


def run(tmp_path, ansible, dns=None):
    return observe(acm.issued(**ARGS), make_probe(tmp_path, ansible=ansible, dns=dns or FakeDns()))


def test_issued(tmp_path):
    ansible = certificate("ISSUED")
    assert run(tmp_path, ansible).status is Status.OK
    assert ansible.calls[0] == (
        "community.aws.acm_certificate_info",
        {"certificate_arn": ARN, "region": "us-east-1", "profile": "sandbox"},
    )


def test_pending_without_the_cname_is_the_operators_step(tmp_path):
    item = run(tmp_path, certificate("PENDING_VALIDATION")).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == "_x.tellabs.dev. CNAME _y.acm-validations.aws."
    assert item.next_step.do == (
        "Add the certificate's validation record(s) in the zone that holds them:"
    )


def test_pending_with_the_cname_visible_is_waiting(tmp_path):
    dns = FakeDns(cnames={"_x.tellabs.dev.": ["_y.acm-validations.aws"]})
    assert run(tmp_path, certificate("PENDING_VALIDATION"), dns).status is Status.PENDING


def test_pending_before_acm_publishes_records_is_waiting(tmp_path):
    assert run(tmp_path, certificate("PENDING_VALIDATION", records=())).status is Status.PENDING


def test_a_failed_certificate_asks_for_a_new_one(tmp_path):
    item = run(tmp_path, certificate("VALIDATION_TIMED_OUT")).items[0]
    assert item.status is Status.FAIL


def test_errors(tmp_path):
    failing = FakeAnsibleHost(
        {"community.aws.acm_certificate_info": AnsibleException({"failed": True})}
    )
    assert run(tmp_path, failing).items[0].error_type == "AnsibleException"
    dns = FakeDns(cnames={"_x.tellabs.dev.": DnsUnavailable("x")})
    pending = run(tmp_path, certificate("PENDING_VALIDATION"), dns)
    assert pending.items[0].error_type == "DnsUnavailable"


def test_a_module_that_reports_failure_is_an_error_without_its_message(tmp_path):
    ansible = FakeAnsibleHost(
        {
            "community.aws.acm_certificate_info": {
                "changed": False,
                "msg": "Couldn't connect to AWS: SECRET",
            }
        }
    )
    item = run(tmp_path, ansible).items[0]
    assert item.status is Status.ERROR
    assert item.error_type == "ModuleFailed"
    assert "SECRET" not in json.dumps(item.to_dict())
